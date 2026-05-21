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
  <list all tables from manifest.tables[] with field descriptions>

  ## Query syntax — Cosmos SQL (NOT PostgreSQL)
  Every field reference MUST be prefixed with the container alias `c.`,
  including inside SELECT projections. Cosmos rejects bare identifiers
  (error SC2001 "Identifier ... could not be resolved").

  CORRECT:
    SELECT c.col1, c.col2, c.col3
    FROM c
    WHERE c.status IN (<valid status enum>)
      AND c.<date_field> <= @cutoff
    ORDER BY c.<date_field> ASC
    OFFSET 0 LIMIT 20

  WRONG (fails with SC2001):
    SELECT col1, col2 FROM c WHERE status = 'open'

  Parameters format: [{"name": "@paramName", "value": "value"}]

  Always use parameterized queries. Never string-interpolate values.
  Use enable_cross_partition_query=True only when partition key is unknown.
  When the schema uses controlled vocabularies (e.g. status, priority),
  enumerate the valid values in the agent.yaml schema block and explicitly
  state which values count as "open" / "closed" / equivalent.

  ## Tools available
  - run_sql_query(query, params, container): Query a Cosmos DB container
  - search_knowledge_base(query): Semantic search across operational documents
  <add call_mock_api only if mock_api enabled>

  ## Response format
  CRITICAL: Your ENTIRE response MUST be a single valid JSON object and
  nothing else. No markdown headings, no `### Summary`, no prose before or
  after, no ```json code fences. The frontend parses your response with
  `JSON.parse(response)` and any non-JSON characters will break rendering.

  Return a single JSON object with at least:
  - summary: concise answer
  - confidence: 0.0-1.0
  - data_sources: list of containers or documents queried
  - recommended_action: what the user should do next
  - <domain_array>: list of data rows returned (work_orders / technicians /
    timeline / positions / etc. — match the agent's primary entity)

  MANDATORY: Whenever you queried data via run_sql_query or call_mock_api,
  you MUST populate the domain array with the actual rows returned (up to
  20). Do NOT summarize them away into prose. The UI renders this array
  as a table — if it is missing or empty, the user sees no data.
```

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
