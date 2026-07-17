"""
ai-prototype-accelerator — Triage-Based Routing Orchestrator
MAF v1.3.0 + Foundry Registered PromptAgents (non-classic, Responses API)

Architecture:
  - Agents are pre-registered in Azure AI Foundry with names like
    "CreditRiskAgent-prototype" by agents/register_agents.py using
    AIProjectClient.agents.create_version() (PromptAgentDefinition).
  - FoundryAgent (agent_framework_foundry v1.3.0) connects to each
    pre-registered PromptAgent by name. It does NOT create new agents.
    Internally it uses the OpenAI Responses API via AIProjectClient —
    NOT the classic Assistants API.
  - AgentSession (agent_framework) maintains per-specialist per-user
    conversation history across turns via previous_response_id.
  - MAF's FunctionInvocationLayer executes local tool callables (SQL,
    mock API) inside this container. Foundry never receives raw data —
    only the serialised string output of each tool call crosses the
    boundary. Knowledge base retrieval is NOT a local callable: it's a
    native MCP tool (knowledge_base_retrieve) declared directly on the
    registered PromptAgent by register_agents.py, executed server-side
    by Foundry's Responses API against the Foundry IQ knowledge base.
  - Triage routing is a single-turn call through the triage FoundryAgent;
    no session is needed because routing decisions are stateless.
"""
import os
import re
import json
import logging
import asyncio
import glob
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from agent_framework.foundry import FoundryAgent
from agent_framework import AgentSession

from agents.tools import sql_tool, mock_api_tool, activity, confirmations

logger = logging.getLogger(__name__)


# ── Module-level state ────────────────────────────────────────────────────────

_credential: Optional[DefaultAzureCredential] = None
_project_endpoint: str = ""
_agent_configs: dict = {}                              # agent_name → config from agent.yaml
_specialist_agents: dict[str, FoundryAgent] = {}       # agent_name → FoundryAgent instance
_triage_agent: Optional[FoundryAgent] = None
# sessions: agent_name → session_id → AgentSession
_specialist_sessions: dict[str, dict[str, AgentSession]] = {}
_routing_table: dict = {}                              # keyword → agent_name
_open_websockets: list = []                            # for graceful shutdown
# Last exchange per chat session, so triage can keep follow-ups sticky and a
# newly-routed specialist inherits context from whichever specialist answered
# before it (AgentSession history is per-specialist, so without this a
# cross-agent follow-up starts from a blank slate).
_last_exchange: dict[str, dict] = {}                   # session_id → {question, agent, answer}
_LAST_EXCHANGE_CAP = 500


# ── Tool sets ─────────────────────────────────────────────────────────────────

_TOOL_CALLABLES = {
    "run_sql_query": sql_tool.run_sql_query,
    "call_mock_api": mock_api_tool.call_mock_api,
    # NOTE: "search_knowledge_base" is intentionally absent. It's no longer a
    # local FunctionTool -- register_agents.py attaches it as a native MCP
    # tool (knowledge_base_retrieve) on the registered PromptAgent, which
    # Foundry's Responses API executes server-side. There is nothing for
    # MAF's FunctionInvocationLayer to dispatch locally; _tools_for_agent()
    # below silently drops any agent.yaml tool name not in this dict.
}


def _tools_for_agent(config: dict) -> list:
    """Return callable list for the tools declared in an agent's config."""
    return [
        _TOOL_CALLABLES[name]
        for name in config.get("tools", [])
        if name in _TOOL_CALLABLES
    ]


# ── Initialization ─────────────────────────────────────────────────────────────

def initialize(credential: DefaultAzureCredential) -> None:
    """Store credential. FoundryAgent instances are built in warm_up()."""
    global _credential
    _credential = credential


async def warm_up() -> None:
    """
    Build one FoundryAgent per pre-registered Foundry agent (triage + specialists).

    Each FoundryAgent connects to an existing PromptAgent resource in Foundry by
    name; it does not create anything. MAF v1.3.0 uses the Responses API internally
    (not the classic Assistants API). Tool callables are registered so MAF's
    FunctionInvocationLayer can execute them locally when Foundry requests a tool call.
    """
    global _project_endpoint, _triage_agent

    _project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    if not _project_endpoint:
        logger.error(
            "AZURE_AI_PROJECT_ENDPOINT not set — agent execution will fail. "
            "Expected: https://<hub>.services.ai.azure.com/api/projects/<project>/"
        )
        return

    # Build credential — managed identity in Azure (AZURE_CLIENT_ID is injected
    # by the Container App's user-assigned identity binding); CLI / interactive
    # fallback locally. `DefaultAzureCredential(managed_identity_client_id=...)`
    # is the canonical SDK pattern: in Azure it uses the UAMI; locally it walks
    # its chain (CLI, env, VSCode, …). Never instantiate a bare
    # `DefaultAzureCredential()` — the security non-negotiable in
    # `.github/copilot-instructions.md` forbids it.
    client_id = os.environ.get("AZURE_CLIENT_ID", "")
    cred = _credential
    if cred is None:
        if client_id:
            cred = ManagedIdentityCredential(client_id=client_id)
        else:
            cred = DefaultAzureCredential(managed_identity_client_id=client_id or None)

    _load_agent_configs()

    # Triage agent — no tools, pure routing
    triage_config = _agent_configs.get("triage", {})
    triage_foundry_name = f"{triage_config.get('name', 'triage')}-prototype"
    _triage_agent = FoundryAgent(
        project_endpoint=_project_endpoint,
        agent_name=triage_foundry_name,
        credential=cred,
    )
    logger.info("Triage FoundryAgent ready: %s", triage_foundry_name)

    # Specialist agents — each registered in Foundry with their tools
    for name, config in _agent_configs.items():
        if name == "triage":
            continue
        foundry_name = f"{config.get('name', name)}-prototype"
        tools = _tools_for_agent(config)
        _specialist_agents[name] = FoundryAgent(
            project_endpoint=_project_endpoint,
            agent_name=foundry_name,
            credential=cred,
            tools=tools,
        )
        logger.info(
            "Specialist FoundryAgent ready: %s → %s (tools: %s)",
            name, foundry_name, [t.__name__ for t in tools],
        )

    _build_routing_table()
    logger.info(
        "Routing table built (%d keywords). Specialists: %s",
        len(_routing_table),
        list(_specialist_agents.keys()),
    )


async def close_all_connections() -> None:
    for ws in list(_open_websockets):
        try:
            await ws.close()
        except Exception:
            pass
    _open_websockets.clear()
    _specialist_sessions.clear()
    _last_exchange.clear()
    confirmations.clear()


def get_registered_agent_names() -> list:
    return list(_specialist_agents.keys())


def get_agent_summaries() -> list:
    """Display name + role per specialist — powers the welcome team cards."""
    return [
        {"name": _display_name(name), "role": config.get("role", "")}
        for name, config in _agent_configs.items()
        if name != "triage"
    ]


# ── Config loading ─────────────────────────────────────────────────────────────

def _load_agent_configs() -> None:
    pattern = str(Path("agents") / "specialists" / "*" / "agent.yaml")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                config = yaml.safe_load(f)
            name = config.get("name") or Path(path).parent.name
            _agent_configs[name] = config
            logger.info("Loaded agent config: %s from %s", name, path)
        except Exception as e:
            logger.error("Failed to load agent config %s: %s", path, e)


def _build_routing_table() -> None:
    for name, config in _agent_configs.items():
        if name == "triage":
            continue
        for keyword in config.get("routing_keywords", []):
            _routing_table[keyword.lower()] = name


# ── Session management ─────────────────────────────────────────────────────────

def _get_session(agent_name: str, session_id: str) -> AgentSession:
    """Return the AgentSession for this agent+session pair, creating if needed."""
    return _specialist_sessions.setdefault(agent_name, {}).setdefault(
        session_id, AgentSession()
    )


def _humanize_agent_name(name: str) -> str:
    """'ProcurementAgent' → 'Procurement Agent'; 'materials_estimator' → 'Materials Estimator'.

    str.title() lowercases interior capitals ('ProcurementAgent' →
    'Procurementagent'), so PascalCase names from the spec must be split at
    camel boundaries instead.
    """
    spaced = name.replace("_", " ").replace("-", " ")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    return " ".join(w if w[:1].isupper() else w.capitalize() for w in spaced.split())


def _display_name(agent_name: str) -> str:
    config = _agent_configs.get(agent_name, {})
    return config.get("display_name") or _humanize_agent_name(agent_name)


# ── Routing ────────────────────────────────────────────────────────────────────

def _keyword_scores(user_message: str) -> dict[str, int]:
    """Per-agent count of routing keywords present in the message."""
    msg_lower = user_message.lower()
    scores: dict[str, int] = {}
    for keyword, agent_name in _routing_table.items():
        if keyword in msg_lower:
            scores[agent_name] = scores.get(agent_name, 0) + 1
    return scores


def _select_agent_keyword(user_message: str) -> str:
    """Keyword-score fallback: return the agent with most keyword matches."""
    scores = _keyword_scores(user_message)
    if scores:
        return max(scores, key=lambda k: scores[k])
    for name in _agent_configs:
        if name != "triage":
            return name
    return list(_agent_configs.keys())[0] if _agent_configs else "triage"


async def _select_agent_llm(user_message: str) -> str:
    """Route via the triage FoundryAgent (single-turn, no session needed)."""
    if _triage_agent is None:
        return _select_agent_keyword(user_message)

    agent_names = [n for n in _agent_configs if n != "triage"]
    try:
        result = await _triage_agent.run(user_message)
        candidate = str(result).strip()
        for name in agent_names:
            if name.lower() == candidate.lower() or name.lower() in candidate.lower():
                return name
        logger.warning("Triage returned '%s', not a known agent — keyword fallback", candidate)
    except Exception as e:
        logger.warning("Triage agent failed (%s) — keyword fallback", e)

    return _select_agent_keyword(user_message)


async def route(user_message: str, thread_id: str, websocket) -> None:
    """Route user message to the appropriate agent(s) and stream the response."""
    if websocket not in _open_websockets:
        _open_websockets.append(websocket)

    pattern = "sequential"
    for name, config in _agent_configs.items():
        if name != "triage":
            pattern = config.get("pattern", "sequential")
            break

    if pattern == "parallel":
        await _route_parallel(user_message, thread_id, websocket)
    else:
        await _route_sequential(user_message, thread_id, websocket)


async def _route_sequential(user_message: str, session_id: str, websocket) -> None:
    prev = _last_exchange.get(session_id)

    # Fast paths that skip the triage LLM round-trip (1-2s per turn).
    # Routing keywords never overlap across agents (spec-validator enforces
    # it), so a message that clearly matches one specialist doesn't need the
    # model to arbitrate; and a short follow-up with no competing keyword
    # signal stays with whoever answered last.
    target = None
    scores = _keyword_scores(user_message)
    matched = [a for a, s in scores.items() if s > 0]
    if len(matched) == 1 and scores[matched[0]] >= 2:
        target = matched[0]
        logger.info("routing fast-path: decisive keywords → %s", target)
    elif prev and len(user_message) <= 60 and not [a for a in matched if a != prev["agent"]]:
        target = prev["agent"]
        logger.info("routing fast-path: sticky follow-up → %s", target)

    if target is None:
        # Tell triage who answered last so ambiguous follow-ups ("what about
        # tomorrow?") stay with the same specialist instead of re-routing cold.
        routing_input = user_message
        if prev:
            routing_input = (
                f"[Context: the previous question in this conversation was handled "
                f"by the {prev['agent']} specialist]\n{user_message}"
            )
        target = await _select_agent_llm(routing_input)

    if target not in _specialist_agents:
        await _send(websocket, {"type": "error", "content": f"Agent '{target}' not available."})
        return

    display_name = _display_name(target)
    await _send(websocket, {"type": "handoff", "agent": display_name})

    # When routing lands on a different specialist than last turn, hand it the
    # previous exchange — its own AgentSession has no cross-agent history.
    agent_message = user_message
    if prev and prev["agent"] != target:
        agent_message = (
            f"[Context: earlier in this conversation the user asked "
            f"\"{prev['question'][:200]}\" and the {prev['agent']} specialist "
            f"answered: {prev['answer'][:300]}]\n{user_message}"
        )

    response = await _run_agent(
        agent_name=target,
        session_id=session_id,
        user_message=agent_message,
        websocket=websocket,
        stream=True,
    )

    if response:
        if len(_last_exchange) >= _LAST_EXCHANGE_CAP:
            _last_exchange.clear()
        _last_exchange[session_id] = {
            "question": user_message,
            "agent": target,
            "answer": _summarize_response(response),
        }

    # Specialists include suggested_questions in their JSON response (see
    # agents-builder.md), which saves the extra triage round-trip per turn.
    # The triage-generated fallback keeps older prototypes working.
    suggestions = _extract_suggestions(response)
    if suggestions:
        await _send(websocket, {"type": "suggestions", "questions": suggestions})
    else:
        await _generate_suggestions(user_message, response, websocket)


def _extract_suggestions(response: str) -> list:
    """Pull suggested_questions out of a structured specialist response."""
    text = (response or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    if not text.startswith("{"):
        return []
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    questions = parsed.get("suggested_questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list):
        return []
    return [str(q) for q in questions if str(q).strip()][:3]


def _summarize_response(response: str) -> str:
    """Condense a specialist response for cross-agent context handoff.

    Structured JSON responses carry a `summary` field — prefer it over raw
    JSON, which wastes context and reads poorly inside a prompt frame.
    """
    text = response.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("summary"):
                return str(parsed["summary"])[:500]
        except (json.JSONDecodeError, ValueError):
            pass
    return text[:500]


async def _route_parallel(user_message: str, session_id: str, websocket) -> None:
    """Group agents by execution_order, run groups in parallel, then synthesize."""
    groups: dict[int, list] = {}
    synthesis_agent = None

    for name, config in _agent_configs.items():
        if name == "triage":
            continue
        if config.get("role") == "synthesis":
            synthesis_agent = name
            continue
        order = config.get("execution_order", 1)
        groups.setdefault(order, []).append(name)

    all_responses: dict[str, str] = {}

    for order in sorted(groups.keys()):
        agents_in_group = groups[order]
        names = [_display_name(a) for a in agents_in_group]
        await _send(websocket, {"type": "handoff", "agent": " + ".join(names)})

        tasks = [
            _run_agent(
                agent_name=name,
                session_id=f"{session_id}__{name}",
                user_message=user_message,
                websocket=websocket,
                stream=False,
            )
            for name in agents_in_group
            if name in _specialist_agents
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(agents_in_group, results):
            if isinstance(result, Exception):
                logger.error("Parallel agent %s failed: %s", name, result)
            else:
                all_responses[name] = result

    if synthesis_agent and synthesis_agent in _specialist_agents:
        synthesis_prompt = (
            f"User asked: {user_message}\n\n"
            + "\n\n".join(f"[{name}]: {resp}" for name, resp in all_responses.items())
            + "\n\nSynthesize the above into a single coherent answer."
        )
        await _send(websocket, {
            "type": "handoff",
            "agent": _display_name(synthesis_agent),
        })
        response = await _run_agent(
            agent_name=synthesis_agent,
            session_id=f"{session_id}__synthesis",
            user_message=synthesis_prompt,
            websocket=websocket,
            stream=True,
        )
    else:
        combined = "\n\n".join(f"**{name}**: {resp}" for name, resp in all_responses.items())
        await _send(websocket, {"type": "text", "content": combined, "done": True})
        response = combined

    await _generate_suggestions(user_message, response, websocket)


# ── Agent runner (MAF FoundryAgent + AgentSession) ────────────────────────────

async def _try_stream(agent, framed_message, session, websocket, display_name):
    """Stream real tokens when MAF exposes a streaming API.

    Returns (full_text, streamed_live). streamed_live is True only when
    prose deltas were actually forwarded to the client — the caller then
    just closes the message. Structured JSON replies are buffered whole
    (the frontend JSON.parses complete payloads) and returned with
    streamed_live=False so the caller's JSON path sends them in one piece.

    The MAF streaming surface is probed defensively: when run_stream is
    missing, yields nothing, or fails before any visible output, we return
    ("", False) and the caller falls back to the buffered run() +
    word-replay path. A mid-stream failure after live output surfaces as a
    truncated-but-honest message rather than a silent retry.
    """
    run_stream = getattr(agent, "run_stream", None)
    if run_stream is None:
        return ("", False)

    chunks: list[str] = []
    buffering_json: bool | None = None  # unknown until first visible char
    try:
        async for update in run_stream(framed_message, session=session):
            delta = None
            for attr in ("text", "delta", "content"):
                value = getattr(update, attr, None)
                if isinstance(value, str) and value:
                    delta = value
                    break
            if delta is None:
                continue
            chunks.append(delta)
            if buffering_json is None:
                visible = "".join(chunks).lstrip("﻿ \t\n")
                if visible:
                    # '{', '[' or a code fence → structured reply: buffer it
                    buffering_json = visible[0] in "{[`"
            if buffering_json is False:
                await _send(websocket, {
                    "type": "text", "content": delta, "done": False, "agent": display_name,
                })
    except Exception as exc:
        if not chunks:
            logger.info("run_stream unavailable (%s); falling back to run()", exc)
            return ("", False)
        logger.error("run_stream failed mid-response: %s", exc)

    full_text = "".join(chunks).strip()
    return (full_text, bool(full_text) and buffering_json is False)

async def _run_agent(
    agent_name: str,
    session_id: str,
    user_message: str,
    websocket,
    stream: bool = True,
) -> str:
    """
    Invoke a specialist FoundryAgent with per-session conversation continuity.

    MAF v1.3.0 FunctionInvocationLayer handles the full tool call loop:
      Foundry (Responses API) → tool_call output item → MAF intercepts →
      local Python tool → result string → Foundry → ... → final text

    Tool results (Cosmos DB rows, AI Search hits) never leave this container;
    only their serialised string representation is returned to Foundry.
    """
    agent = _specialist_agents.get(agent_name)
    if agent is None:
        logger.error("No FoundryAgent found for '%s'", agent_name)
        if stream:
            await _send(websocket, {"type": "error", "content": f"Agent '{agent_name}' not initialised."})
        return ""

    session = _get_session(agent_name, session_id)

    # Inject current date/time so the agent doesn't rely on its training-cutoff guess.
    # This is prepended only to the per-turn user message; system instructions are unchanged.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    framed_message = f"[Context: current UTC datetime is {now_iso}]\n{user_message}"

    display_name = _display_name(agent_name)

    # Bind the tool event channel so tools invoked during this run can send
    # status lines and confirmation cards to this websocket, keyed to this
    # session + agent.
    activity_token = activity.bind(
        asyncio.get_running_loop(), websocket, session_id, agent_name
    )
    try:
        full_text = ""
        streamed_live = False
        if stream:
            full_text, streamed_live = await _try_stream(
                agent, framed_message, session, websocket, display_name
            )
        if not full_text and not streamed_live:
            result = await agent.run(framed_message, session=session)
            full_text = str(result).strip() if result else ""
    except Exception as e:
        logger.error("FoundryAgent run failed for %s: %s", agent_name, e)
        if stream:
            await _send(websocket, {"type": "error", "content": f"Agent error: {e}"})
        return ""
    finally:
        activity.unbind(activity_token)

    if streamed_live:
        # Tokens already reached the client; just close the message.
        await _send(websocket, {"type": "text", "content": "", "done": True, "agent": display_name})
        return full_text

    if stream and full_text:
        # Detect structured JSON responses and send as a single message to avoid
        # word-by-word reassembly issues that break JSON.parse on the frontend.
        candidate = full_text.strip().lstrip("\ufeff")
        # Strip markdown fences if present
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate).strip()
        is_json = False
        try:
            json.loads(candidate)
            is_json = True
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: model returned prose + a fenced JSON block. Extract the
        # first ```json ... ``` block (or first {...} object) so the frontend
        # still gets structured data instead of a markdown wall.
        if not is_json:
            fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", full_text, flags=re.IGNORECASE)
            extracted = fence_match.group(1) if fence_match else None
            if not extracted:
                obj_match = re.search(r"(\{[\s\S]*\})", full_text)
                extracted = obj_match.group(1) if obj_match else None
            if extracted:
                try:
                    json.loads(extracted)
                    candidate = extracted
                    is_json = True
                except (json.JSONDecodeError, ValueError):
                    pass

        if is_json:
            await _send(websocket, {"type": "text", "content": candidate, "done": True, "agent": display_name})
        else:
            words = full_text.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                await _send(websocket, {"type": "text", "content": chunk, "done": i == len(words) - 1, "agent": display_name})
                await asyncio.sleep(0.02)

    return full_text


async def handle_confirmation(incoming: dict, thread_id: str, websocket) -> None:
    """Resume a parked write operation after the user confirms or cancels.

    Write tools never execute directly: they park the operation in the
    confirmations registry (keyed by session) and push a confirmation card
    to the UI. This claims the parked operation and, only on an explicit
    yes, executes it and lets the owning specialist narrate the outcome.
    """
    pending = confirmations.pop(thread_id)
    if not pending:
        await _send(websocket, {"type": "error", "content": "No pending operation to confirm."})
        return

    if not incoming.get("value", False):
        await _send(websocket, {
            "type": "text",
            "content": "Operation cancelled. No changes were made.",
            "done": True,
        })
        return

    operation = pending.pop("operation", "")
    agent_name = pending.pop("agent", "") or next(
        (n for n in _agent_configs if n != "triage"), ""
    )
    try:
        result = sql_tool.execute_write(operation, **pending)
        tool_output = json.dumps(result)
    except Exception as e:
        logger.error("confirmed write failed | op=%s | error=%s", operation, e)
        tool_output = json.dumps({"error": str(e)})

    resume_msg = (
        f"[The user confirmed the {operation} operation and it was executed. "
        f"Result: {tool_output}] Briefly tell the user the outcome."
    )
    response = await _run_agent(
        agent_name=agent_name,
        session_id=thread_id,
        user_message=resume_msg,
        websocket=websocket,
        stream=True,
    )
    if not response:
        await _send(websocket, {"type": "text", "content": "Operation completed.", "done": True})


# ── Suggestions ────────────────────────────────────────────────────────────────

async def _generate_suggestions(user_message: str, agent_response: str, websocket) -> None:
    """Use the triage FoundryAgent to generate 2-3 follow-up question chips."""
    if _triage_agent is None:
        return

    suggestion_prompt = (
        f"The user asked: \"{user_message}\"\n"
        f"The agent responded: \"{agent_response[:500]}\"\n\n"
        "Generate 2-3 short follow-up questions the user might want to ask next. "
        "Return ONLY a JSON object: {\"questions\": [\"...\", \"...\", \"...\"]}"
    )
    try:
        result = await _triage_agent.run(suggestion_prompt)
        text = str(result).strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            questions = parsed.get("questions", [])[:3]
            if questions:
                await _send(websocket, {"type": "suggestions", "questions": questions})
    except Exception as e:
        logger.debug("Suggestion generation failed (non-critical): %s", e)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _send(websocket, data: dict) -> None:
    try:
        await websocket.send_text(json.dumps(data))
    except Exception as e:
        logger.debug("send failed: %s", e)

