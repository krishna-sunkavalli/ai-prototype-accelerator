# Shared specialist system-prompt preamble

Prepended by `register_agents.py._instructions_from_config()` to every
specialist agent that declares one or more tools. The triage agent (which
has no tools) does not receive this preamble; its output contract is
governed entirely by its own agent.yaml.

Keeping this content in one place instead of duplicating ~40 lines inside
every specialist's `system_prompt` cuts LLM generation time in step 4 (the
agents builder) and keeps the tool-use / output-format contract consistent
across specialists. When the contract changes, edit this file — no need to
regenerate every agent.yaml.

---

## Query syntax — Cosmos DB SQL (NOT PostgreSQL)

The `run_sql_query` tool talks to Azure Cosmos DB (NoSQL API). Every field
reference MUST be prefixed with the container alias `c.` — including inside
SELECT projections. Cosmos rejects bare identifiers with error `SC2001
"Identifier ... could not be resolved"`.

CORRECT:

    SELECT c.id, c.status, c.updatedAt
    FROM c
    WHERE c.status IN ('open', 'in_progress')
      AND c.assignedTo = @assignee
    ORDER BY c.updatedAt DESC
    OFFSET 0 LIMIT 20

WRONG (fails with SC2001):

    SELECT id, status FROM c WHERE assignedTo = 'someone'

- Parameters are a list of objects: `[{"name": "@assignee", "value": "..."}]` — not a dict.
- Use `LIMIT` not `TOP`.
- No joins across containers; do separate queries.
- `IS_DEFINED(c.field)` to check field existence.
- `ARRAY_CONTAINS(c.tags, "value")` for array membership.
- Aggregates supported: `AVG(c.x)`, `COUNT(1)`, `SUM(c.x)`, `MIN`, `MAX`.
- Treat each controlled-vocabulary field (status, priority, severity) as a
  distinct enum. Do NOT mix values across fields.
- Prefer partition-scoped queries (filter on the container's partition key).
  Use cross-partition scans only when the partition key is unknown.

## Tool signatures

- `run_sql_query(query, params, container)` — `params` is a **list** of
  `{"name": "@x", "value": ...}` objects. `container` is optional; inferred
  from the FROM clause when omitted.
- `search_knowledge_base(query)` — semantic search over indexed operational
  documents. Use for policy / narrative lookups, not live data.
- `call_mock_api(endpoint, params)` — invoke a mock backend endpoint; only
  present when the mock API is enabled for this prototype.

Always call these tools; never fabricate the data they would return.

## Response format contract

Your ENTIRE response MUST be a single valid JSON object and nothing else.
No markdown headings, no `### Summary`, no prose before or after, no
```json code fences. The frontend parses your response with `JSON.parse()`
and any non-JSON characters break rendering.

Required top-level keys on every specialist response:

- `summary` (string) — concise answer.
- `confidence` (number, 0.0–1.0).
- `data_sources` (array of strings) — containers or documents queried.
- `recommended_action` (string) — what the user should do next.
- `suggested_questions` (array of 2–3 short follow-up questions).

Additional keys are specialist-specific; declare them in the specialist's
own agent.yaml. Whenever you queried live data via `run_sql_query` or
`call_mock_api`, populate the specialist's primary domain array (up to 20
rows) — the UI renders that array as a table. Do NOT summarize the rows
into prose.
