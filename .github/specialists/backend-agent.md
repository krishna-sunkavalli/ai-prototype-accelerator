# Backend Agent — Generates generated/prototype/backend/config.py ONLY

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Generate generated/prototype/backend/config.py with all environment variables and branding constants.
This is the ONLY file generated in emitted backend/. Everything else is scaffold materialized from accelerator-owned source.

## Prerequisite
Read generated/build-state/manifest.json first. If missing, stop.
Record the step clock: `py -3 accelerator/scripts/build-metrics.py step-start 06-backend-agent`.

## Inputs
Read: generated/build-state/manifest.json

## Output
Write: generated/prototype/backend/config.py
Write: generated/build-state/06-backend-agent.done — via:

```
py -3 accelerator/generators/sentinels.py write \
  --sentinel generated/build-state/06-backend-agent.done \
  --manifest generated/build-state/manifest.json \
  --output generated/prototype/backend/config.py
```

Never write the sentinel as a plain timestamp.

---

## Pre-built files — NEVER create, overwrite, or modify these:
- generated/prototype/backend/main.py
- generated/prototype/backend/api/routes.py
- generated/prototype/agents/orchestrator.py
- generated/prototype/agents/tools/sql_tool.py       ← uses run_sql_query() — must not be renamed
- generated/prototype/agents/tools/mock_api_tool.py  ← uses call_mock_api()

Note: `search_knowledge_base` is NOT a local tool file anymore — it's a
native Foundry IQ MCP tool wired by `register_agents.py` (see RESOLVED.md
#31). There is no `agents/tools/search_tool.py` to avoid touching.

---

## Step 1 — Run fill-templates.py for config only

```
py -3 accelerator/generators/fill-templates.py --target config
```

This reads manifest.json and hydrates only `generated/prototype/backend/config.py` from the accelerator-owned template.
It populates all env var defaults, branding constants, STARTER_QUESTIONS,
AGENT_NAMES, MOCK_API_ENDPOINTS_RAW, and a basic SCENARIOS list.

**Do this first before making any manual edits.**

## Step 2 — Optionally enhance SCENARIOS (the only LLM-authored part)

After running fill-templates.py, review `SCENARIOS` in config.py.
The script generates one scenario per agent using the starter questions.
You may replace these with domain-specific demo flows that better reflect
the customer's actual use case. Format:

```python
SCENARIOS = [
    {
        "name": "<Persona> <Task>",
        "description": "<One sentence describing the demo flow>",
        "steps": [
            "<Question 1 to ask the agent>",
            "<Question 2>",
            "<Question 3>",
        ],
    },
    # ... one scenario per key persona
]
```

If SCENARIOS looks reasonable from the template output, skip Step 2 entirely.

---

## backend/config.py — exact required content

```python
import os
import json

# ── Azure Identity ────────────────────────────────────────────────────────────
AZURE_CLIENT_ID           = os.environ["AZURE_CLIENT_ID"]

# ── AI Foundry ────────────────────────────────────────────────────────────────
# AZURE_AI_PROJECT_ENDPOINT:
#   Format: https://{hub}.services.ai.azure.com/api/projects/{project}/
#   Used by: MAF FoundryAgent (agent_framework.foundry) — all agent operations.
#   MAF derives the OpenAI inference endpoint internally from this URL.
#   No separate AzureOpenAI client or AZURE_FOUNDRY_ENDPOINT needed in orchestrator.
#
AZURE_AI_PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]

# ── Search + Storage ──────────────────────────────────────────────────────────
AZURE_SEARCH_ENDPOINT     = os.environ["AZURE_SEARCH_ENDPOINT"]
AZURE_SEARCH_INDEX        = os.environ["AZURE_SEARCH_INDEX"]
AZURE_STORAGE_ACCOUNT     = os.environ["AZURE_STORAGE_ACCOUNT_NAME"]

# ── Cosmos DB (NOT PostgreSQL) ────────────────────────────────────────────────
AZURE_COSMOS_ENDPOINT     = os.environ["AZURE_COSMOS_ENDPOINT"]
AZURE_COSMOS_DATABASE     = os.environ["AZURE_COSMOS_DATABASE"]

# ── Branding (from Container App env vars wired from Bicep) ──────────────────
CUSTOMER_NAME             = os.environ.get("CUSTOMER_NAME",    "<manifest.customer.name>")
AGENT_NAME                = os.environ.get("AGENT_NAME",       "<manifest.branding.agentName>")
PRIMARY_COLOR             = os.environ.get("PRIMARY_COLOR",    "<manifest.branding.primaryColor>")
ACCENT_COLOR              = os.environ.get("ACCENT_COLOR",     "<manifest.branding.accentColor>")
FONT_FAMILY               = os.environ.get("FONT_FAMILY",      "<manifest.branding.fontFamily>")
LOGO_URL                  = os.environ.get("LOGO_URL",         "<manifest.branding.logoUrl>")
WELCOME_MESSAGE           = os.environ.get("WELCOME_MESSAGE",  "<manifest.branding.welcomeMessage>")
USE_CASE_TITLE            = os.environ.get("USE_CASE_TITLE",   "<manifest.branding.useCaseTitle>")

# ── Starter questions (JSON array from Container App env var) ─────────────────
_starter_raw    = os.environ.get("STARTER_QUESTIONS", json.dumps(<manifest.branding.starterQuestions as Python list>))
STARTER_QUESTIONS = json.loads(_starter_raw) if isinstance(_starter_raw, str) else _starter_raw

# ── Demo persona ──────────────────────────────────────────────────────────────
PERSONA_NAME              = os.environ.get("PERSONA_NAME",  "<manifest.branding.personaName>")
PERSONA_ROLE              = os.environ.get("PERSONA_ROLE",  "<manifest.branding.personaRole>")

# ── Mock API (only if manifest.mockApiEnabled == true) ────────────────────────
# MOCK_API_ENABLED        = os.environ.get("MOCK_API_ENABLED", "false").lower() == "true"
```

---

## Foundry endpoint — one variable only

The orchestrator uses **Microsoft Agent Framework (MAF)** — `FoundryAgent` from
`agent_framework.foundry`. MAF connects using `AZURE_AI_PROJECT_ENDPOINT` and derives
the OpenAI inference endpoint internally. There is no `AzureOpenAI` direct client in
orchestrator.py and no need for `AZURE_FOUNDRY_ENDPOINT` in config.py.

| Variable | Format | Used for |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | `https://{hub}.services.ai.azure.com/api/projects/{project}/` | MAF `FoundryAgent` — all agent operations + triage routing |

`AZURE_FOUNDRY_ENDPOINT` is still output by Bicep (Azure OpenAI endpoint) and injected as
a Container App env var — it is available at runtime but orchestrator.py does not read it.
Do NOT add it to config.py; nothing in backend/ uses it.

---

## Database variables — Cosmos DB only

The pre-built `generated/prototype/agents/tools/sql_tool.py` reads:
- `AZURE_COSMOS_ENDPOINT`
- `AZURE_COSMOS_DATABASE`

Never add `AZURE_POSTGRES_SERVER`, `AZURE_POSTGRES_DB`, or any PostgreSQL variable.
The database is Cosmos DB NoSQL. There is no PostgreSQL in this scaffold.

> **Container discovery — nothing to hand-edit.**
> `sql_tool.py` discovers the live Cosmos container list at runtime via
> `list_containers()` and caches it for the process lifetime. Do NOT
> hand-edit `sql_tool.py` — it is a static template on the "never modify
> during build" list. The old instruction to update `_KNOWN_CONTAINERS`
> from `manifest.tables[]` was based on an obsolete scaffold that no
> longer exists in this repository.

---

## After success
Print:
```
[Step 6/7] Backend config generated.
  backend/config.py written
  Cosmos DB vars: AZURE_COSMOS_ENDPOINT, AZURE_COSMOS_DATABASE
  Branding: <agentName> / <primaryColor>
```
