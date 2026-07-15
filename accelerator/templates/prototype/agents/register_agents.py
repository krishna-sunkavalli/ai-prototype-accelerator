#!/usr/bin/env python3
"""Register agents with Azure AI Foundry as PromptAgent versions.

Each agent.yaml under ./agents/specialists/ is registered as a PromptAgent version via
AIProjectClient.agents.create_version(). The agent_framework.foundry.FoundryAgent
runtime connects to these PromptAgents by name (Responses API).

IMPORTANT: tools MUST be declared on the registered PromptAgent. MAF strips
the `tools` field from per-request payloads when calling an agent endpoint
(see agent_framework_foundry/_agent.py — "Strip tools from request body").
Without tool declarations on the registered agent, the model has no idea
the functions exist and will hallucinate calls in plain markdown instead of
invoking them.

Required environment variables:
    AZURE_AI_PROJECT_ENDPOINT  e.g. https://<hub>.services.ai.azure.com/api/projects/<project>/
    AZURE_CLIENT_ID            optional, for managed identity
"""

from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import AzureCliCredential, DefaultAzureCredential


AGENT_SUFFIX = "-prototype"

# ── Tool catalogue ────────────────────────────────────────────────────────────
# Loaded from agents/tools/tool_definitions.yaml at runtime. The YAML file is
# the single source of truth for tool schemas; do not hard-code tool
# definitions here. To add a tool, edit tool_definitions.yaml (and implement
# the matching Python callable under agents/tools/).

_TOOL_DEFINITIONS_PATH = Path(__file__).resolve().parent / "tools" / "tool_definitions.yaml"


def _load_tool_catalogue() -> dict[str, FunctionTool]:
    if not _TOOL_DEFINITIONS_PATH.exists():
        raise RuntimeError(
            f"Tool definitions file not found: {_TOOL_DEFINITIONS_PATH}. "
            "agents/tools/tool_definitions.yaml must ship with the prototype."
        )
    with open(_TOOL_DEFINITIONS_PATH, "r", encoding="utf-8") as f:
        defs = yaml.safe_load(f) or []
    if not isinstance(defs, list):
        raise RuntimeError(
            f"tool_definitions.yaml must be a YAML list of tool entries; got {type(defs).__name__}"
        )
    catalogue: dict[str, FunctionTool] = {}
    for entry in defs:
        name = entry.get("name")
        if not name:
            raise RuntimeError(f"tool_definitions.yaml entry missing 'name': {entry!r}")
        catalogue[name] = FunctionTool(
            type="function",
            name=name,
            description=(entry.get("description") or "").strip(),
            strict=bool(entry.get("strict", False)),
            parameters=entry.get("parameters") or {"type": "object", "properties": {}},
        )
    return catalogue


_TOOL_CATALOGUE: dict[str, FunctionTool] = _load_tool_catalogue()


def get_client() -> AIProjectClient:
    endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
    try:
        return AIProjectClient(
            endpoint=endpoint,
            credential=AzureCliCredential(process_timeout=60),
            allow_preview=True,
        )
    except Exception:
        client_id = os.environ.get("AZURE_CLIENT_ID")
        # Always pass managed_identity_client_id (None when unset locally) so
        # we never instantiate a bare DefaultAzureCredential() — see
        # .github/copilot-instructions.md security non-negotiables.
        cred = DefaultAzureCredential(managed_identity_client_id=client_id)
        return AIProjectClient(
            endpoint=endpoint,
            credential=cred,
            allow_preview=True,
        )


def _load_agent_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Shared specialist preamble — Cosmos SQL rules, tool signatures, JSON
# response contract. Prepended to every agent that declares tools, so the
# per-agent system_prompt in agent.yaml can focus on domain content instead
# of restating the runtime contract. Triage (which declares no tools) does
# not receive this preamble; its output format is unique.
_PREAMBLE_PATH = Path(__file__).resolve().parent / "system_prompt_preamble.md"
_PREAMBLE_CACHE: str | None = None


def _load_preamble() -> str:
    global _PREAMBLE_CACHE
    if _PREAMBLE_CACHE is None:
        try:
            _PREAMBLE_CACHE = _PREAMBLE_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            _PREAMBLE_CACHE = ""
    return _PREAMBLE_CACHE


def _instructions_from_config(config: dict) -> str:
    sp = config.get("system_prompt")
    if not (sp and sp.strip()):
        role = config.get("role", "specialist agent")
        return f"You are {config.get('name', 'agent')}. {role}".strip()

    specialist_prompt = sp.strip()

    # Only tool-using agents (specialists) need the SQL + JSON contract
    # preamble. The triage agent declares no tools and has its own
    # single-word output contract.
    tools = config.get("tools") or []
    if not tools:
        return specialist_prompt

    preamble = _load_preamble()
    if not preamble:
        return specialist_prompt
    return f"{preamble}\n\n---\n\n{specialist_prompt}"


def _tools_from_config(config: dict) -> list[FunctionTool]:
    """Return FunctionTool definitions for the tools declared in agent.yaml.

    Any tool referenced by agent.yaml that is not present in
    tool_definitions.yaml is a configuration error — fail fast so we don't
    silently register an agent that thinks it has tools the runtime can't
    invoke.
    """
    tools: list[FunctionTool] = []
    unknown: list[str] = []
    for tool_name in config.get("tools", []):
        if tool_name in _TOOL_CATALOGUE:
            tools.append(_TOOL_CATALOGUE[tool_name])
        else:
            unknown.append(tool_name)
    if unknown:
        known = sorted(_TOOL_CATALOGUE.keys())
        raise RuntimeError(
            f"Agent '{config.get('name')}' declares unknown tools {unknown}. "
            f"Add them to agents/tools/tool_definitions.yaml. Known tools: {known}"
        )
    return tools


def register_agent(
    client: AIProjectClient,
    name: str,
    model: str,
    instructions: str,
    tools: list[FunctionTool],
    max_retries: int = 5,
) -> None:
    kwargs: dict = {"kind": "prompt", "model": model, "instructions": instructions}
    if tools:
        kwargs["tools"] = tools
    definition = PromptAgentDefinition(**kwargs)

    for attempt in range(max_retries):
        try:
            version = client.agents.create_version(
                agent_name=name,
                definition=definition,
            )
            tool_names = [t.name for t in tools]
            print(
                f"  REGISTERED  {name}  "
                f"(version {getattr(version, 'version', '?')}, tools={tool_names})"
            )
            return
        except Exception as e:
            msg = str(e).lower()
            if (
                ("project does not exist" in msg or "not found" in msg)
                and attempt < max_retries - 1
            ):
                print(
                    f"  Waiting for project to be ready... "
                    f"({attempt + 1}/{max_retries})"
                )
                time.sleep(30)
            else:
                print(f"  FAILED   {name}: {e}")
                raise


def main() -> None:
    client = get_client()
    print("Registering agents with Foundry (PromptAgent versions)...")

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    os.chdir(project_root)

    pattern = str(Path("agents") / "specialists" / "*" / "agent.yaml")
    agents_to_register: list[tuple[str, str, str, list[FunctionTool]]] = []

    for yaml_path in sorted(glob.glob(pattern)):
        config = _load_agent_yaml(yaml_path)
        name = config.get("name") or Path(yaml_path).parent.name
        model = config.get("model", "gpt-4o-mini").replace(".", "-")
        instructions = _instructions_from_config(config)
        tools = _tools_from_config(config)
        foundry_name = f"{name}{AGENT_SUFFIX}"
        agents_to_register.append((foundry_name, model, instructions, tools))

    if not agents_to_register:
        print("  No agent.yaml files found under ./agents/specialists/. Nothing to register.")
        return

    for name, model, instructions, tools in agents_to_register:
        register_agent(client, name, model, instructions, tools)

    print("Agent registration complete.")


if __name__ == "__main__":
    main()
