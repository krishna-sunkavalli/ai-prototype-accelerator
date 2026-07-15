# Resolved Issues

Archive of deployment-blocking issues that surfaced during real builds, along
with the template-level fixes applied so they cannot recur. Open issues live in
`KNOWN_ISSUES.md`.

---

## 1. BCP332 — `customerName` overflow when slug > 20 chars

**Symptom:** `azd up` fails during Bicep compile:
```
Error BCP332: The provided value '<long-slug>' (length 23) exceeds maxLength 20.
```

**Root cause:** `main.bicep` enforces `@maxLength(20)` on `customerName` (used as a
resource prefix), but `spec-validator.py` was passing `customer.slug` straight
through. Companies with long names (e.g. `acco-engineered-systems`, 23 chars)
break the build.

**Fix (applied to accelerator):**
- `accelerator/generators/spec-validator.py` derives `customerShort` (≤20 chars)
  by truncating to the first hyphen-segment, then to 20 chars. Stored at
  `manifest.deployment.customerShort`.
- `accelerator/generators/fill-templates.py` exposes
  `{{CUSTOMER_SHORT_BICEP}}` and `{{DEMO_THEME_BICEP}}` placeholders.
- `main.bicepparam.tpl` now uses `{{CUSTOMER_SHORT_BICEP}}` for `customerName`
  and `{{DEMO_THEME_BICEP}}` for `demoTheme`.

**Future work:** Add similar length-budget checks for the AKS-style resources
when we introduce them. The 32-char CAE limit and 44-char Cosmos limit are
already checked via warnings in `spec-validator.py`.

---

## 2. `miName` double-slug

**Symptom:** Generated managed-identity name like
`acco-engineered-systems-acco-engineered-systems-prototype-id` (60 chars),
which fails the 24-char limit on identity names.

**Root cause:** `spec-validator.py` used `f"{slug}-{env_name}-id"`, but
`env_name` already starts with the slug (e.g. `acco-engineered-systems-prototype`).

**Fix:** `spec-validator.py` now uses `f"{customer_short}-{env_name}-id"`. The
short customer is already a prefix of `env_name`, so the result still has the
slug duplicated — but at the short form. Long-term, we should strip the slug
from `env_name` before composing. **Tracking:** revisit when next overflow hits.

---

## 3. AI Search standard SKU capacity exhaustion in eastus2

**Symptom:** `azd up` fails on the AI Search resource with:
```
SubscriptionCannotCreateNewResource: Subscription <id> cannot create new
resource of type Microsoft.Search/searchServices in the location 'eastus2'
because the location is currently capacity-constrained.
```

**Root cause:** `foundry-iq.bicep` hard-coded `sku.name = 'standard'` and used
`location` (which is usually `eastus2`). Standard SKU has frequent capacity
problems in eastus2.

**Fix:**
- `foundry-iq.bicep` added `param searchLocation string = location` and
  `sku.name = 'basic'` (sufficient for prototype workloads — supports semantic
  ranking, vector search, up to 2 GB / index).
- `main.bicep` plumbs `searchLocation` through the module call.
- `main.bicepparam.tpl` exposes `param searchLocation = '{{AZURE_REGION}}'`
  so users can override per-deployment.

**To upgrade:** switch SKU back to `standard` in `foundry-iq.bicep` only if you
need replica/partition counts > 1 or per-index storage > 2 GB.

---

## 4. `register_agents.py` — wrong SDK / wrong agent resource type

**Symptom A (initial):** Post-provision hook fails:
```
AttributeError: 'AgentsOperations' object has no attribute 'create_agent'
```

**Symptom B (after first fix):** Registration succeeds but the running app
returns `404 Agent <name> with version not found` from the Foundry data plane
when MAF tries to invoke the agent via `/api/projects/{project}/openai/v1/responses`.

**Root cause:** The `agent_framework.foundry.FoundryAgent` class connects to
**PromptAgent versions** via the Responses API. PromptAgent versions are a
DIFFERENT resource type than classic Assistants. They must be registered with
`AIProjectClient.agents.create_version(agent_name=..., definition=PromptAgentDefinition(...))`
from `azure-ai-projects>=2.1.0` with `allow_preview=True`.

Using `azure-ai-agents.AgentsClient.create_agent(...)` (classic Assistants API)
creates a resource the Responses-API runtime cannot find — so MAF fails at
first chat turn even though registration "succeeded".

**Fix:**
- `accelerator/templates/prototype/requirements.txt` drops `azure-ai-agents`
  and keeps `azure-ai-projects>=2.1.0`.
- `.github/specialists/agents-builder.md` rewrote the `register_agents.py`
  template to use:
  ```python
  from azure.ai.projects import AIProjectClient
  from azure.ai.projects.models import PromptAgentDefinition
  client = AIProjectClient(endpoint=..., credential=..., allow_preview=True)
  client.agents.create_version(
      agent_name=name,
      definition=PromptAgentDefinition(kind="prompt", model=model, instructions=instructions),
  )
  ```
- Idempotent: each run creates a new version of the same `agent_name`; the
  runtime FoundryAgent picks the latest version.

**Future watch:** when `allow_preview=True` is no longer required (PromptAgent
operations leave preview), drop the flag from `AIProjectClient(...)` calls.

### 4b. PromptAgent registered without `tools` → model hallucinates tool calls

**Symptom:** Chat returns a response but the agent fabricates SQL queries
and result JSON in plain markdown instead of calling `run_sql_query` /
`search_knowledge_base` / `call_mock_api`. No tool invocations show up in
Application Insights traces.

**Root cause:** MAF strips the `tools` field from per-request payloads when
calling a registered agent endpoint (see `agent_framework_foundry/_agent.py`
— `run_options.pop("tools", None)` in `_prepare_options`). The model only
sees the tools declared on the registered PromptAgent itself.

**Fix:** `register_agents.py` declares every tool in a `_TOOL_CATALOGUE`
and passes the appropriate `FunctionTool` objects in
`PromptAgentDefinition(tools=[...])` for each agent based on
`agent.yaml.tools[]`. The tool schemas in the catalogue must match the
actual Python callable signatures under `src/agents/tools/`.

---

## 5. No length-budget warnings at validation time

**Symptom:** Spec validation succeeded for inputs that later failed Bicep
compile because derived resource names exceeded Azure limits (CAE ≤ 32, Cosmos
≤ 44, Storage ≤ 24, ACR ≤ 50).

**Fix:** `spec-validator.py` now builds a `warnings: list[str]` during
manifest synthesis and prints any entries before the success line. Current
checks cover the CAE and Cosmos limits using the derived `resource_prefix`
(`{customer_short}-{demo_theme}`).

**Future work:** add ACR + Storage warnings using the actual generated names
from `manifest.resources`.

---

## 6. UAMI RBAC — `Azure AI Developer` is insufficient for Foundry V2 agents

**Symptom:** Chat returns `401 PermissionDenied` from the Foundry data plane:
```
The principal <objectId> lacks the required data action
'Microsoft.CognitiveServices/accounts/AIServices/agents/write' to perform
'POST /api/projects/{projectName}/openai/*' operation.
```

**Root cause:** Foundry V2's Responses API requires the new data action
`Microsoft.CognitiveServices/accounts/AIServices/agents/write`. The
`Azure AI Developer` built-in role does NOT include it. The
`Azure AI User` role would, but it's not yet registered in every tenant.
The most-permissive built-in that includes this action AND is widely
available is `Azure AI Administrator`
(roleDefinitionId `b78c5d69-af96-48a3-bf8d-a8b4d589de94`).

**Fix:** `accelerator/templates/prototype/infra/modules/foundry.bicep`
assigns `Azure AI Administrator` to the UAMI on BOTH the hub account scope
AND the `foundryProject` sub-resource scope. Assignment at hub-only is not
sufficient — V2 data-plane checks the project sub-resource.

**Future watch:** when `Azure AI User` is registered in all target tenants,
switch to that role (less-privileged) — its roleDefinitionId differs by
tenant during preview, so prefer the role name lookup at assignment time.

---

## How to add a new entry

1. Reproduce the failure on a fresh `@devlead build` to confirm it's a
   template-level issue (not a spec-level user error).
2. Patch the template/generator under `accelerator/` so the next build is
   immune.
3. Add a numbered section above with: symptom, root cause, fix location, and
   any future work / version pins.
4. If the fix introduces new placeholders, document them in
   `accelerator/templates/prototype/README.md` (when that exists) or in the
   relevant `.github/specialists/*.md` file.

---

## 6. Azure AI Administrator role required at both hub and project scope

**Symptom:** `register_agents.py` fails with `(PermissionDenied) Principal
does not have access to API/Operation` when calling
`AIProjectClient.agents.create_version`. Reader/Contributor on the resource
group is not enough.

**Root cause:** PromptAgent create/update operations require the data action
`Microsoft.CognitiveServices/accounts/AIServices/agents/write`. Among the
widely-available built-in roles, only **Azure AI Administrator**
(`b78c5d69-af96-48a3-bf8d-a8b4d589de94`) includes this data action — and it
must be assigned at **both** the AI hub account scope and the project
sub-resource scope.

**Fix:** `infra/modules/foundry.bicep` declares two role assignments:
`roleAssignmentAiAdminHub` scoped to `aiHub` and
`roleAssignmentAiAdminProject` scoped to `foundryProject`, both targeting
the UAMI principal that runs the postprovision hook and the app.

---

## 7. Cosmos DB `publicNetworkAccess: Disabled` blocks Container Apps

**Symptom:** Tool calls from the deployed app return
`(Forbidden) Request originated from IP ... through public internet. This is
blocked by your Cosmos DB account firewall settings` even though the bicep
template sets `networkAclBypass: 'AzureServices'`.

**Root cause:** Without VNet integration, Container Apps egress traffic does
not present as a managed Azure service to Cosmos. `networkAclBypass:
AzureServices` alone is insufficient — the account must also have
`publicNetworkAccess: 'Enabled'`. Note that the AVM Cosmos module defaults
`publicNetworkAccess` to `Disabled` and can reset it on every provision.

**Fix:** `infra/modules/cosmos.bicep` already pins
`networkRestrictions.publicNetworkAccess: 'Enabled'` together with
`networkAclBypass: 'AzureServices'`. If a build hits this error, verify the
live account matches the template (`az cosmosdb show ... --query
publicNetworkAccess`) and re-apply with
`az cosmosdb update -n <name> -g <rg> --public-network-access Enabled`.

---

## 8. `search_tool.py` field names must match the seeded index schema

**Symptom:** Knowledge-base queries return
`(InvalidRequestParameter) Invalid expression: Could not find a property
named 'source' on type 'search.document'`.

**Root cause:** The seeded AI Search index (built by the postprovision hook
from `operational-docs/`) creates fields `id`, `content`, `title`,
`filename`, `category` — there is no `source` field. Older versions
of `src/agents/tools/search_tool.py` selected `source` directly.

**Fix:** `src/agents/tools/search_tool.py` (template and emitted) now
selects `["content", "title", "filename", "category"]` and derives the
display source as `result.get("filename") or result.get("category") or
"Unknown"`. Keep field names in the tool aligned with the index schema
written by `database/cosmos_seed.py` / the search-indexer hook.

---

## 9. Agent generates Cosmos SQL without `c.` alias on projected columns

**Symptom:** Specialist tool calls fail repeatedly with
`(BadRequest) ... SC2001: Identifier 'work_order_id' could not be resolved.`
After 3 consecutive failures MAF stops invoking the tool and the agent
returns a "Unable to retrieve ... data query failures" message.

**Root cause:** The agent.yaml "Query syntax" section said "Always alias the
container as `c`" but the model interpreted that as only `FROM c`, not as
`SELECT c.col`. It produced `SELECT work_order_id, customer_account FROM c
WHERE c.status = 'new'`, which Cosmos rejects.

**Fix:** Every data-querying agent.yaml's "Query syntax" block now contains
explicit ✅ / ❌ examples showing `c.` prefix on every projected column,
plus a note that `status` and `priority` are distinct fields with disjoint
enums. Tracked in `.github/specialists/agents-builder.md` so future
generated agents inherit the rule.

---

## 10. Agent hallucinates dates / uses model-training cutoff as "now"

**Symptom:** SLA-risk queries return empty because the model assumes the
current date is its training cutoff (e.g. June 2024) and filters
`c.sla_due_at <= '2024-...Z'` against seed data dated 2026-05.

**Root cause:** Foundry PromptAgents have no implicit current-time
injection. The model defaults to training-time priors.

**Fix:** `src/agents/orchestrator.py` prepends a context line to every
user_message before calling `agent.run(...)`:
```python
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
framed = f"[Context: current UTC datetime is {now_iso}]\n{user_message}"
```

---

## 11. Agent emits markdown wrappers around JSON; UI renders nothing

**Symptom:** Specialist replies render as raw `### Summary` headings with
embedded `\`\`\`json ... \`\`\`` blocks instead of a structured card.

**Root cause:** Model defaults to chat-style markdown despite the "Return a
single JSON object" instruction. The frontend calls `JSON.parse(response)`
and silently falls back to text on parse failure.

**Fix (two layers):**
- All specialist agent.yamls have a hardened "Response format" block:
  *"Your ENTIRE response MUST be a single valid JSON object and nothing
  else. No markdown headings, no `### Summary`, no prose before or after,
  no \`\`\`json code fences."*
- `src/agents/orchestrator.py` has a salvage path that, when the raw text
  isn't pure JSON, regex-extracts the first `{...}` block (with or without
  code fence) and validates with `json.loads` before emitting.

---

## 12. Status vocabulary mismatch — agent filters `c.status = 'open'`

**Symptom:** Open-work-order queries return zero rows.

**Root cause:** Seed data uses statuses `new | dispatched | on_site |
waiting_parts | escalated | completed`. The model assumed `status = 'open'`
based on common usage.

**Fix:** Each agent.yaml schema section now enumerates valid statuses,
defines "open" as `status IN ('new','dispatched','on_site',
'waiting_parts','escalated')`, and explicitly forbids `c.status = 'open'`.

---

## 13. UI renderer drops domain data arrays

**Symptom:** Specialist returns a well-formed JSON with `work_orders: [...]`
but the chat card shows only the summary + confidence + recommended_action
— no table.

**Root cause:** `static/index.html` `renderStructuredJSON()` only checked
for arrays named `positions / servicers / items / results / data` —
inherited from an earlier financial-services template. ACCO Dispatch's
arrays (`work_orders`, `technicians`, `timeline`, `parts`, `history`) were
silently ignored.

**Fix:** The `listKey` lookup now includes
`['work_orders','technicians','timeline','history','parts', ...existing...]`.
Long-term, the table key list should be data-driven from a manifest field
so each prototype's domain arrays land automatically.

---

## 14. Specialist summarizes data into prose instead of populating array

**Symptom:** Even after #13 fix, work_orders array often arrives empty —
the model wrote a prose summary like "5 urgent work orders identified"
without listing them.

**Fix:** Each agent.yaml "Response format" now ends with a MANDATORY
clause: *"Whenever you queried any work-order data via run_sql_query or
call_mock_api, you MUST populate the `work_orders` array with the actual
rows returned (up to 20). Do NOT summarize them away into prose."*

---

## 15. Triage prompt — keyword lists are brittle

**Symptom:** "Who can I assign to these urgent work orders?" routes to
DispatchCoordinatorAgent (matches "assign" + "urgent" + "work orders")
when it should route to TechnicianReadinessAgent (the question is about
people).

**Root cause:** Original triage.agent.yaml used keyword-overlap scoring
with tie-breakers. Once we added a "WHO → Technician" override rule the
user (correctly) pushed back that we were hardcoding routes the model
should infer.

**Fix:** Triage prompt rewritten to describe each specialist by
**responsibility + data ownership** and ask the model to reason about
which data scope answers the user's underlying intent. No keyword lists,
no tie-breakers, no carve-outs.

---

## 16. Stale suggestion chips persist after user sends a follow-up

**Symptom:** Clicking a suggestion chip submits the question, but the
chip row remains visible above the new message, creating duplicate UI.

**Fix:** `static/index.html` `sendMessage()` now removes any existing
`.suggestions-row` nodes before appending the user message.

---

## 17. UI table renderer was tied to a single domain's array names

**Symptom:** Each new prototype needed `static/index.html` edited to teach
`renderStructuredJSON()` about its domain arrays (`work_orders`,
`technicians`, `positions`, etc.). Forgetting to do so silently drops
the data table even when the agent returns well-formed JSON.

**Fix:** `renderStructuredJSON()` no longer holds a hardcoded list. It now
discovers the first response field whose value is a non-empty array of
plain objects and renders it as the table:

```js
var listKey = Object.keys(obj).find(function(k){
  return Array.isArray(obj[k]) && obj[k].length > 0
    && obj[k][0] !== null
    && typeof obj[k][0] === 'object'
    && !Array.isArray(obj[k][0]);
});
```

`data_sources` (array of strings) and similar scalar arrays are
automatically skipped. Works for any prototype's response schema as long
as the specialist's MANDATORY data array (issue #14) is populated.


---

## 18. Many bugs above were rooted in LLM-generated deterministic code

**Symptom:** Roughly half of the entries above (#4, #4b, #6, #9, #11, #12,
#15, parts of #16) were not "the model is wrong about facts" bugs — they
were "the model drifted on deterministic scaffolding it shouldn't have
been writing in the first place" bugs. Examples:

- Wrong SDK call in `register_agents.py` (classic Assistants vs. Responses).
- Missing `c.` prefix examples in agent.yaml after a re-emission.
- `MODELS=( ... )` list in `preprovision.sh` getting out of sync with the
  manifest because two specialists were each writing their own copy.
- Bicep `foundry-iq.bicep` being rewritten end-to-end each build, with
  small variations in API version / version field across runs.

**Root cause:** The build graph used LLM specialists to emit code that is
fully derivable from `manifest.json`. There was no creativity in those
outputs — only a chance for drift.

**Fix (architectural — applied):** Three layers, applied per artifact.

| Layer | Pattern | Used for |
|---|---|---|
| **Static template** | Copied verbatim by `materialize-prototype.py` | `scripts/register_agents.py`, `infra/modules/foundry-iq.bicep`, `src/agents/*.py`, `Dockerfile`, `azure.yaml`, `hooks/preprovision.sh` |
| **`.tpl` + placeholders** | Hydrated by `fill-templates.py` from `manifest.json` | `infra/main.bicepparam`, `src/config.py`, `hooks/postprovision.{sh,ps1}` |
| **LLM specialist** | Genuinely creative content | `agents/*/agent.yaml.system_prompt`, `agents/*/skills/*/SKILL.md`, `operational-docs/*.md`, `database/cosmos_seed.py` data narratives |

**What changed in this pass:**

- `scripts/register_agents.py` was moved from "LLM-emitted in step 4" to
  `accelerator/templates/prototype/scripts/register_agents.py` (static).
  It now reads `agents/*/agent.yaml` at runtime and registers each agent
  via `_TOOL_CATALOGUE`. The specialist `.md` (`agents-builder.md`) was
  rewritten to forbid emitting this file.
- `infra/modules/foundry-iq.bicep` is now static and uses a `for` loop
  over `param modelDeployments array`. The deployments come from
  `main.bicepparam`, which `fill-templates.py` hydrates from
  `manifest.modelDeployments`. The specialist `.md` (`infra-agent.md`)
  was rewritten to forbid editing any `.bicep` file.
- `preprovision.sh` now reads the `MODELS` array from `manifest.json` at
  runtime via `jq` (with a small static fallback).
- `hook-agent.md` was trimmed — postprovision was already template-based,
  but the specialist was carrying ~430 lines of redundant lesson notes.

**Bugs that become structurally impossible after this refactor:**

- Wrong Foundry SDK call (Assistants vs. Responses) — code is no longer
  re-emitted.
- Missing tool declarations on a registered agent — `_TOOL_CATALOGUE`
  lives in the static template.
- Model deployment version drift across builds — versions are in one
  table (`MODEL_VERSION_DEFAULTS` in `fill-templates.py`).
- `preprovision.sh`'s MODELS list disagreeing with the actual deployed
  models — both now read the same `manifest.json`.
- `foundry-iq.bicep` API version drift between builds.


---

## 16. Unicode punctuation in branding strings became mojibake

**Symptom:** Em dashes / smart quotes / ellipses authored in `spec.yaml` survived into `manifest.json` and `main.bicepparam` as proper UTF-8 but rendered as `?` (U+FFFD) inside deployed Container App env vars (likely a cp1252 transcode somewhere along Bicep -> ARM -> ACA env-var rendering on Windows hosts).

**Fix:** `accelerator/generators/fill-templates.py` defines `_PUNCT_MAP` + `ascii_safe()` and applies it to every branding/persona/welcome/use-case/font field before substitution. No Unicode punctuation can reach Bicep now.

---

## 17. Region selection required hand-editing emitted bicepparam

**Symptom:** When the spec-declared region hit `InsufficientResourcesAvailable` for AI Search or Foundry, the only recovery was hand-editing `generated/prototype/infra/main.bicepparam` -- which violated the never-patch-emitted-output rule and was lost on the next `@devlead build`.

**Fix:** `main.bicepparam.tpl` now wraps every region param in `readEnvironmentVariable('AZURE_LOCATION', '<spec-default>')` (also `AZURE_AI_LOCATION`, `AZURE_SEARCH_LOCATION`). `azd env set AZURE_LOCATION <region>` re-targets the deploy without modifying generated files.

---

## 18. Soft-deleted Foundry account held the custom subdomain and blocked redeploy

**Symptom:** After a failed deploy left a Cognitive Services account soft-deleted in the original region, the next `azd up` (even in a different region) failed with `CustomDomainInUse` because the global subdomain was reserved for 48 hours.

**Fix:** `accelerator/generators/preflight.py` now runs `check_foundry_subdomain_available()` which calls `az cognitiveservices check-domain-availability` for the computed subdomain and hard-fails with a copy/paste-able `az cognitiveservices account purge ...` remediation hint when the subdomain is held by a soft-deleted account.

---

## 19. Stale azd env from a previous customer silently reused

**Symptom:** When the workspace still held a `.azure/<env>/.env` from an earlier customer (different slug, different RG), `azd up` happily deployed into that stale RG until the user noticed resources landing in the wrong place.

**Fix:** `accelerator/generators/preflight.py` now runs `check_azd_env_matches_manifest()` which compares the active azd env (read from `generated/prototype/.azure/config.json`) against `manifest.deployment.environmentName` and fails with an `azd env new <expected>` remediation hint when they differ.

---

## 20. `text-embedding-3-large` deployment defined twice in foundry-iq.bicep

**Symptom:** `azd up` fails during `azd provision` with:

```
InvalidTemplate: Deployment template validation failed:
'The resource 'Microsoft.CognitiveServices/accounts/<hub>/deployments/text-embedding-3-large' at line '1' and column '2522' is defined multiple times in a template.'
```

Bicep what-if also warns: `The resource '.../deployments/text-embedding-3-large' is defined multiple times in this deployment. Only the final state of the resource is shown.`

**Root cause:** `accelerator/templates/prototype/infra/modules/foundry-iq.bicep` declared `text-embedding-3-large` twice:
1. Via the `@batchSize(1) resource llmDeployments = [for m in modelDeployments: ...]` loop, which iterates every entry in `manifest.modelDeployments` — the canonical `spec.yaml` template shipped by `.github/agents/business-analyst.agent.md` always includes `text-embedding-3-large` in `model_deployments` so the loop always emits it.
2. As the hard-coded `resource embeddingDeployment` right below the loop, which owns the same deployment name because AI Search vector indexing depends on it.

Every real build hit this — the bug did not surface earlier only because prior specs happened to omit the embedding model from `model_deployments` or the older business-analyst template did.

**Fix (applied to accelerator):** In [foundry-iq.bicep](../accelerator/templates/prototype/infra/modules/foundry-iq.bicep), filter the embedding model out of the loop before iterating:

```bicep
var llmOnlyModelDeployments = filter(modelDeployments, m => m.deploymentName != embeddingModelName)

@batchSize(1)
resource llmDeployments '...' = [for m in llmOnlyModelDeployments: { ... }]
```

The fixed `embeddingDeployment` resource remains the sole owner of `text-embedding-3-large`. Backward-compatible with specs that omit the embedding from `model_deployments` (the filter is a no-op then).

**Regression guard:** `az bicep build` on the emitted `main.bicep` returns 0 errors with the fix in place. Any future duplicate-model regression will surface as a what-if `ResourceDeployedMultipleTimes` diagnostic before `azd provision` starts creating resources.

---

## 21. Knowledge docs had no upper bound on length (docs-agent.md)

**Symptom:** `.github/specialists/docs-agent.md` R1 said only `Minimum 800 words per document` - no ceiling. Docs generation grew to 1000-1200 words per doc without improving retrieval quality (top-k chunks are already well-formed by ~800 words for a demo's question surface). Extra words slowed both generation and the Search index-and-embed step by 10-30% each.

**Fix (applied to accelerator):** `.github/specialists/docs-agent.md` R1 now specifies a **600-900 word band** with a target of 700-800. Guidance explicitly discourages padding and notes the retrieval-quality plateau. Backward-compatible: prior builds' longer docs still index fine.

## 22. `build-metrics.py` conflated generation, provisioning, and verification time

**Symptom:** The build metric printed one flat wall-clock (e.g., `Spec -> deployed, verified product: 738m 52s`) with no way to see which phase dominated. When the wall-clock included overnight idle + interactive login gaps between phases, the number was actively misleading for benchmarking.

**Fix (applied to accelerator):** New `deploy-start` event in [`accelerator/scripts/build-metrics.py`](../accelerator/scripts/build-metrics.py) records when devlead invokes `azd up`. `summary` now prints a **Phase breakdown** section:

```
Phase breakdown
  Generation   : 14m 43s
  Provisioning : 22m 12s
  Verification :  1m 05s
```

[`.github/agents/devlead.agent.md`](../.github/agents/devlead.agent.md) Step 10 now records `deploy-start` immediately before `azd up`. Existing metric events (`build-start`, `deploy-done`, `verify-done`) unchanged; older metrics files still summarize (breakdown just omitted when `deploy-start` is absent).

## 23. Embedding deployment serialized behind LLM loop for no reason

**Symptom:** In `foundry-iq.bicep`, the fixed `embeddingDeployment` had `dependsOn: [llmDeployments]`, forcing the embedding model deployment to wait until ALL LLM deployments in the `@batchSize(1)` loop had finished. Added ~4-8s of pure serial wait on every provision.

**Root cause:** No documented rationale for the chain. The `@batchSize(1)` on `llmDeployments` already caps loop concurrency at 1, and Azure's AIServices account serializes concurrent model-deployment creates internally - so the dependsOn was defensive overkill. Auditing the whole `accelerator/templates/prototype/infra/**/*.bicep` tree found no other explicit `dependsOn` (all inter-module deps are implicit via `.outputs.X` references, which is the recommended pattern).

**Fix (applied to accelerator):** Removed `dependsOn: [llmDeployments]` from [`foundry-iq.bicep`](../accelerator/templates/prototype/infra/modules/foundry-iq.bicep). Embedding now runs in parallel with the first LLM iteration. Small trim, but establishes the pattern: don't add `dependsOn` where implicit deps already exist.

**Regression guard:** Comment above the embedding resource explains why the chain was removed, so future edits don't reintroduce it accidentally. `az bicep build` still returns 0 errors.


## 24. Bicep resource type refresh — preview APIs and stale AVM modules

**Symptom:** Every Foundry / Cognitive Services resource in infra/modules/ was declared against `Microsoft.CognitiveServices/*@2025-04-01-preview` even though three GA API versions (`2025-09-01`, `2025-12-01`, `2026-03-01`) had been published since. Preview APIs occasionally return response shapes that Bicep's resource-type validator rejects and can be deprecated without a compat guarantee — the `RequestConflict on parent` and `CustomDomainInUse` failures we hit during the Hudson Advisors build all came through the preview endpoint. AVM modules were also 1-4 minor versions stale, most notably `avm/res/document-db/database-account` at 0.15.0 vs 0.19.0.

**Root cause:** No automated version check on the Bicep and AVM refs. Templates were written months ago against whatever was current at the time and drifted as Microsoft published GA versions.

**Fix:** Refreshed all Bicep resource declarations and AVM references in `accelerator/templates/prototype/infra/` on 2026-07-14 —
- `Microsoft.CognitiveServices/accounts`, `.../accounts/deployments`, `.../accounts/projects` → `2026-03-01` GA
- `Microsoft.Search/searchServices` → `2025-05-01`
- `avm/res/app/managed-environment` → 0.13.3
- `avm/res/app/container-app` → 0.23.0
- `avm/res/document-db/database-account` → 0.19.0
- `avm/res/operational-insights/workspace` → 0.15.1
- `avm/res/insights/component` → 0.7.2
- `avm/res/storage/storage-account` → 0.32.1
- `container-registry.bicep` converted from a raw `Microsoft.ContainerRegistry/registries@2023-07-01` resource to `avm/res/container-registry/registry:0.12.1`, matching the module's docstring and picking up AVM's diagnostic settings + network defaults.
- Added `searchSku` param to `foundry-iq.bicep` (default `basic`, overridable via `azd env set AZURE_SEARCH_SKU standard`) so the recurring BACKLOG #2 "Basic SKU exhausted" pain gets a first-class knob rather than a `.bicep` edit.
- `container-app.bicep`'s `containerImage` default flipped from `mcr.microsoft.com/azuredocs/containerapps-helloworld:latest` to `ghcr.io/PLACEHOLDER_ORG/PLACEHOLDER_REPO:latest` so a bypass of the standard `azd deploy` step fails visibly instead of silently serving the ACA hello-world page.

Validated with `az bicep build` (0 errors) and the full contract test suite (58 tests passing). Phase-2 preflight cold-run stayed at ~37 s; warm caches skip the Bicep build.

**Prevention:** Consider adding a periodic `version-drift.py` script that queries the Bicep resource-type catalog and AVM registry for latest versions and fails CI when the templates drift more than one minor behind. Filed as future BACKLOG.
