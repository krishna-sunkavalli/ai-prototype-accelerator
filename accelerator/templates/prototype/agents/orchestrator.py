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
  - MAF's FunctionInvocationLayer executes tool callables (SQL, AI Search)
    locally inside this container. Foundry never receives raw data — only
    the serialised string output of each tool call crosses the boundary.
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

from agents.tools import sql_tool, search_tool, mock_api_tool

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
_pending_confirmations: dict = {}                      # session_id → pending write context


# ── Tool sets ─────────────────────────────────────────────────────────────────

_TOOL_CALLABLES = {
    "run_sql_query": sql_tool.run_sql_query,
    "search_knowledge_base": search_tool.search_knowledge_base,
    "call_mock_api": mock_api_tool.call_mock_api,
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


def get_registered_agent_names() -> list:
    return list(_specialist_agents.keys())


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


# ── Routing ────────────────────────────────────────────────────────────────────

def _select_agent_keyword(user_message: str) -> str:
    """Keyword-score fallback: return the agent with most keyword matches."""
    msg_lower = user_message.lower()
    scores: dict[str, int] = {}
    for keyword, agent_name in _routing_table.items():
        if keyword in msg_lower:
            scores[agent_name] = scores.get(agent_name, 0) + 1
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
    target = await _select_agent_llm(user_message)
    config = _agent_configs.get(target, {})

    if target not in _specialist_agents:
        await _send(websocket, {"type": "error", "content": f"Agent '{target}' not available."})
        return

    display_name = config.get("display_name", target.replace("_", " ").title())
    await _send(websocket, {"type": "handoff", "agent": display_name})

    response = await _run_agent(
        agent_name=target,
        session_id=session_id,
        user_message=user_message,
        websocket=websocket,
        stream=True,
    )
    await _generate_suggestions(user_message, response, websocket)


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
        names = [_agent_configs[a].get("display_name", a) for a in agents_in_group]
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
            "agent": _agent_configs[synthesis_agent].get("display_name", "Synthesis"),
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

    try:
        result = await agent.run(framed_message, session=session)
        full_text = str(result).strip() if result else ""
    except Exception as e:
        logger.error("FoundryAgent.run failed for %s: %s", agent_name, e)
        if stream:
            await _send(websocket, {"type": "error", "content": f"Agent error: {e}"})
        return ""

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
            await _send(websocket, {"type": "text", "content": candidate, "done": True})
        else:
            words = full_text.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                await _send(websocket, {"type": "text", "content": chunk, "done": i == len(words) - 1})
                await asyncio.sleep(0.02)

    return full_text


async def handle_confirmation(incoming: dict, thread_id: str, websocket) -> None:
    """Resume a paused write operation after the user confirms or cancels."""
    pending = _pending_confirmations.pop(thread_id, None)
    if not pending:
        await _send(websocket, {"type": "error", "content": "No pending operation to confirm."})
        return

    confirmed = incoming.get("value", False)
    if not confirmed:
        await _send(websocket, {"type": "text", "content": "Operation cancelled.", "done": True})
        return

    confirmation_obj = pending["confirmation"]
    try:
        result = sql_tool.execute_write(
            query=confirmation_obj.get("query", ""),
            params=confirmation_obj.get("params", {}),
        )
        tool_output = json.dumps(result)
    except Exception as e:
        tool_output = json.dumps({"error": str(e)})

    agent_name = pending.get("agent_logical_name", "")
    session_id = pending.get("session_key", thread_id)
    resume_msg = f"The write operation completed: {tool_output}"
    response = await _run_agent(
        agent_name=agent_name,
        session_id=session_id,
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

