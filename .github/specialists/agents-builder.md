# Agents Builder — Generates generated/prototype/agents/

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Generate all agent YAML definitions, SKILL.md files, and schemas.py files.

**DO NOT generate `agents/register_agents.py`.** That script is a static
template at `accelerator/templates/prototype/agents/register_agents.py`
copied verbatim by the scaffold step. It reads `agents/*/agent.yaml` at
runtime and registers each one — no per-prototype emission needed.

## Prerequisite
Read generated/build-state/manifest.json first. If missing, stop.

## Inputs
Read: generated/build-state/manifest.json

## Outputs
For triage agent:
    Write: generated/prototype/agents/specialists/triage/agent.yaml
    Write: generated/prototype/agents/specialists/triage/skills/routing/SKILL.md

For each specialist agent in manifest.agents (excluding triage):
    Write: generated/prototype/agents/specialists/{agentName}/agent.yaml
    Write: generated/prototype/agents/specialists/{agentName}/schemas.py
  For each skill in manifest.agents[].skills:
        Write: generated/prototype/agents/specialists/{agentName}/skills/{skillName}/SKILL.md

Write: generated/build-state/04-agents-builder.done — via:

```
py -3 accelerator/generators/sentinels.py write \
  --sentinel generated/build-state/04-agents-builder.done \
  --manifest generated/build-state/manifest.json \
  --output generated/prototype/agents/specialists
```

Pass `--output generated/prototype/agents/specialists` (the directory) so the sentinel's
output hash covers every emitted agent.yaml, SKILL.md, and schemas.py file.
Never write the sentinel as a plain timestamp.

---

## agent.yaml structure (all agents)

```yaml
name: <manifest.agents[].name>
role: <manifest.agents[].role>
model: <manifest.agents[].model>
tools:
  - run_sql_query
  - search_knowledge_base
  # add call_mock_api only if manifest.mockApiEnabled == true
grounds_against:
  - knowledge_base
  - sql
system_prompt: |
  You are <manifest.branding.agentName>, <role> for <manifest.customer.name>.

  ## Your focus
  <specific focus from spec.yaml agents[].system_prompt_focus>

  ## Database (Azure Cosmos DB NoSQL)
  <list ONLY the tables this specialist owns; describe each field the
  specialist queries and enumerate controlled-vocabulary values (status,
  priority, credit tier, etc.) with which values count as "open" /
  "closed" / equivalent>

  ## Specialist output keys
  In addition to the shared response contract (summary / confidence /
  data_sources / recommended_action / suggested_questions), this
  specialist must also return:
  - <specialist_domain_array> (array of objects) — the rows returned
    from the specialist's primary container. MANDATORY whenever
    run_sql_query was called; the UI renders this as a table. Do NOT
    summarize the rows away into prose.
  - <any specialist-specific fields from spec.yaml agents[].response_format>
```

**DO NOT include the following boilerplate blocks in `system_prompt`** — they
are prepended automatically at runtime by
`register_agents.py._instructions_from_config()` from the shared file
`agents/system_prompt_preamble.md`:

- Cosmos SQL syntax rules (the `c.` prefix, parameter format, `IS_DEFINED`,
  `ARRAY_CONTAINS`, `LIMIT` vs `TOP`, no-joins, aggregates)
- Tool signatures (`run_sql_query`, `search_knowledge_base`, `call_mock_api`)
- JSON-only response format contract and the shared required keys
  (`summary`, `confidence`, `data_sources`, `recommended_action`,
  `suggested_questions`)

Keeping specialists boilerplate-free cuts LLM generation time in this step
by roughly 30 % per agent and keeps the tool/output contract in one place.
When the contract changes, edit `system_prompt_preamble.md`; do not
regenerate all agent.yaml files.

---

## SQL rules in system prompts — CRITICAL

Use Cosmos SQL syntax. NEVER reference PostgreSQL syntax.

- Correct: `SELECT * FROM c WHERE c.accountId = @accountId`
- Wrong:   `SELECT * FROM loan_accounts WHERE account_id = $1`

Cosmos SQL rules:
- Always alias the container as `c`: `FROM c` AND prefix every projected
  column with `c.` (`SELECT c.col`, not `SELECT col`). Bare column names
  fail with SC2001.
- Parameters: `[{"name": "@paramName", "value": "value"}]`  ← list of objects, NOT a dict
- `LIMIT` not `TOP`
- No joins — cross-container queries are separate SDK calls
- `IS_DEFINED(c.field)` to check field existence
- `ARRAY_CONTAINS(c.tags, "value")` for array membership
- Treat each controlled-vocabulary field (status, priority, severity, etc.)
  as a distinct enum. Do NOT mix values across fields (e.g. don't filter
  `c.status = 'urgent'` when `urgent` is a priority value).

> **CRITICAL — type annotation in sql_tool.py:**
> `run_sql_query(params: list | None)` — MAF generates the JSON schema for tool calls
> directly from Python type hints. If `params` is annotated as `dict`, the LLM will send
> a JSON object instead of an array, causing every tool call to fail silently with
> "consecutive function call errors reached (3)". The annotation must match the agent
> system prompt example format (a list).

---

## Triage agent — special rules

```yaml
name: triage
role: Intelligent routing agent
model: gpt-4o-mini  # always — cost-optimized, do not change
```

System prompt must:
- Describe each specialist by **responsibility + data ownership** — what
  containers / documents it owns and what kind of question it answers.
  Do NOT emit keyword lists or tie-breaker decision rules; the model picks
  by reasoning about which data scope answers the user's underlying intent.
- Default to the dispatch/queue-style specialist when the message is
  ambiguous or purely conversational.
- Include an explicit **Output format (routing)** section that requires
  the model's entire response to be EXACTLY ONE specialist agent name
  and nothing else — no prose, no punctuation, no explanation. The
  orchestrator does case-insensitive substring matching, so any extra
  text risks matching the wrong agent name.
- After routing and receiving the specialist response, when asked for
  follow-up suggestions return:
  ```json
  {"type": "suggestions", "questions": ["Q1", "Q2", "Q3"]}
  ```
- Never answer domain questions directly — always route.

---

## schemas.py — one Pydantic response model per specialist agent

```python
from pydantic import BaseModel
from typing import List, Optional

class <AgentName>Response(BaseModel):
    summary: str
    confidence: float
    data_sources: List[str]
    recommended_action: str
    # add fields from spec.yaml agents[].response_format
```

---

## agents/register_agents.py — STATIC, do not regenerate

The Foundry registration script is **a static template** at
`accelerator/templates/prototype/agents/register_agents.py`. It is copied
verbatim during the scaffold step. At runtime it:

- iterates `agents/*/agent.yaml`,
- reads `name`, `model`, `system_prompt`, `tools` from each,
- maps tool names through the built-in `_TOOL_CATALOGUE` (run_sql_query,
  search_knowledge_base, call_mock_api),
- registers each as a `PromptAgentDefinition` via
  `AIProjectClient.agents.create_version()` (azure-ai-projects ≥ 2.1.0).

**This builder MUST NOT write `agents/register_agents.py`.** Any tool the
agents use must already exist in the static template's `_TOOL_CATALOGUE`.
If you need a new tool, add it to the template — not as a per-prototype
emission.

---

## After success
Print:
```
[Step 4/7] Agent definitions generated.
    Triage agent: generated/prototype/agents/specialists/triage/
  Specialists: <list agent names>
  Skills: <total count> SKILL.md files
```
