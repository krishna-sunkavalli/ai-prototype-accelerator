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
| **Static template** | Copied verbatim by `materialize-prototype.py` | `scripts/register_agents.py`, `infra/modules/foundry-iq.bicep`, `infra/modules/search.bicep`, `src/agents/*.py`, `Dockerfile`, `azure.yaml`, `hooks/preprovision.sh` |
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

---

## 25. `deploy.py` crashed with `UnicodeDecodeError` streaming `azd up` output on Windows

**Symptom:** `py accelerator/scripts/deploy.py` aborted mid-provision with
`UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 in position 5`
raised inside a background pump thread, even though the underlying `azd up`
process was still running fine.

**Root cause:** `_run_azd_up()` opened the `azd up` subprocess with
`subprocess.Popen(..., text=True)` and no explicit `encoding=`. On Windows,
`text=True` decodes using the process's default locale codec (cp1252 on this
workstation), which cannot represent some bytes in azd's unicode progress
output (spinner glyphs, checkmarks). The decode error was raised inside the
`_pump()` thread reading `proc.stdout`, killing that thread and leaving the
wrapper in an inconsistent state.

**Fix:** `accelerator/scripts/deploy.py` now passes `encoding="utf-8",
errors="replace"` to both `subprocess.run()` in `_run()` and
`subprocess.Popen()` in `_run_azd_up()`, so any undecodable byte is replaced
instead of crashing the stream reader.

**Prevention:** Any new subprocess call in `accelerator/scripts/` that shells
out to `az`/`azd` and captures or streams output should set `encoding="utf-8",
errors="replace"` explicitly rather than relying on `text=True` alone —
Windows' default codec is not UTF-8.

---

## 26. AI Search capacity auto-swap never triggered — marker scan was stderr-only

**Symptom:** `azd up` failed with `InsufficientResourcesAvailable` for the AI
Search service in the primary region, but `deploy.py`'s documented auto-swap
(BACKLOG #2 — retry with a fallback `AZURE_SEARCH_LOCATION`) never fired; the
wrapper exited 1 immediately instead of retrying.

**Root cause:** `_looks_like_search_capacity_error()` requires both the
`insufficientresourcesavailable` marker and the word `search` to appear in the
same string, but the call site only scanned `result.stderr`. azd prints its
per-resource progress line (`(x) Failed: Search service: <name>`) to
**stdout**, while the `InsufficientResourcesAvailable` error detail lands in
**stderr** without the word "search" nearby. Neither stream alone satisfied
the marker check, so the gate always evaluated false.

**Fix:** The retry loop in `deploy.py::main()` now builds
`combined_lower = (result.stdout + result.stderr).lower()` and passes that to
`_looks_like_search_capacity_error()` instead of `stderr` alone.

**Prevention:** When gating on azd failure text, always scan combined
stdout+stderr — azd does not consistently put related progress/error lines on
the same stream.

---

## 27. Specialist agents leaked raw technical error detail to end users on tool failure

**Symptom:** When a tool call failed (e.g. Cosmos DB unreachable behind the
MCAPS firewall — see KNOWN_ISSUES #2), the specialist's chat reply exposed
internal remediation language directly to the end user, e.g. "Check the query
syntax or retry the query execution... consult database logs" and "validate
database access credentials and container schema." Confusing and inappropriate
for a business chat UI, and a minor information-disclosure smell (implies
infra detail exists to guess at).

**Root cause:** Two tool functions returned/raised raw exception text that
flows straight into the model's context:
- `search_tool.py`'s `search_knowledge_base()` returned
  `f"Search unavailable: {str(e)}"` — the raw SDK exception string became the
  tool's "result", which the model then paraphrased back to the user.
- `sql_tool.py`'s `run_sql_query()` did a bare `raise` after logging. Per
  `orchestrator.py`'s own docstring, MAF's `FunctionInvocationLayer` handles
  "local Python tool → result string → Foundry" — it catches the raised
  exception and serializes it into the tool-result text sent to the model,
  so the raw Cosmos/SDK exception (firewall detail, IPs, etc.) reached the
  LLM, which then invented a plausible-sounding "IT support" narrative around
  it.
- No instruction existed anywhere in the shared `system_prompt_preamble.md`
  telling specialists how to behave on a tool failure.

**Fix (applied to accelerator, all three layers):**
- `search_tool.py` now returns a generic `"Search unavailable right now."` on
  failure; `logger.error(...)` still captures full detail server-side.
- `sql_tool.py`'s `run_sql_query` now raises
  `RuntimeError("The requested data is temporarily unavailable.") from exc`
  instead of re-raising the raw exception — `from exc` keeps the real
  traceback in server logs without exposing it to the model.
- `system_prompt_preamble.md` gained a new "Tool failures — never expose
  technical details to the user" section (prepended to every specialist
  automatically at runtime by `register_agents.py`) specifying: brief
  plain-language apology, low `confidence` (0.1-0.3), a user-appropriate
  `recommended_action` (never "check logs/credentials/schema/query syntax"),
  full JSON contract preserved, domain array left empty (`[]`).

**Why both tool-layer and prompt-layer fixes:** defense in depth — prompt-only
instructions aren't fully reliable (a model can still leak detail present in
text it was given despite being told not to). Sanitizing the tool's own error
string removes the raw material entirely; the prompt rule catches any
residual case.

**Scope note:** this fixes the template source for all *future* builds. An
already-deployed prototype's Container App image has the old tool files
baked in and needs a rebuild + redeploy to pick this up.

---

## 28. Foundry IQ knowledge source creation fails with 400 — `chatCompletionModel` conflicts with `disableImageVerbalization`

**Symptom:** During postprovision step 10 (Wiring operational documents into
Foundry IQ), the knowledge source PUT call fails:
```
FAILED: Could not create knowledge source -- Response status code does not indicate success: 400 (Bad Request).
```
The generic .NET/PowerShell exception message hides the actual response
body. Replaying the same PUT with `curl` surfaces the real API error:
```json
{"error":{"code":"","message":"ChatCompletionModel must not be set when DisableImageVerbalization is true."}}
```

**Root cause:** Both `postprovision.ps1.tpl` and `postprovision.sh.tpl` set
`ingestionParameters.disableImageVerbalization = true` **and** included an
`ingestionParameters.chatCompletionModel` block on the knowledge source body.
`chatCompletionModel` under `ingestionParameters` is only used for
image-verbalization during ingestion; the Azure AI Search knowledge source
API (`2026-05-01-preview`) rejects the combination outright since the model
would never be invoked. Because `Invoke-WebRequest`/`requests` surface only
the HTTP status on a non-2xx response by default, the actual validation
message never reached the console output, making this look like an opaque
platform failure instead of a malformed request body.

**Fix (applied to accelerator):** Removed the `chatCompletionModel` block
from `ingestionParameters` in both `postprovision.ps1.tpl` and
`postprovision.sh.tpl` — only `embeddingModel` is needed there. The
knowledge *base* (not knowledge *source*) still declares its own `models`
array with a `gpt-4o-mini` chat model for query planning/reasoning, which is
unaffected by this fix.

**Diagnostic tip:** When a Search REST PUT/POST fails with a bare
`(HTTP 400)`/`(HTTP 404)` and no body in postprovision output, replay the
same request with `curl.exe -s -o resp.json -w "%{http_code}"` (or
`curl -s -o resp.json -w "%{http_code}"` on POSIX) against the same
endpoint/payload — `Invoke-WebRequest`'s catch block and Python's
`requests.raise_for_status()` both discard the response body by default.

---

## 29. Foundry IQ knowledge base creation fails with 400 — `outputMode: "extractedData"` is not a valid enum value

**Symptom:** After RESOLVED #28 fixed the knowledge *source* 400, the
knowledge *base* PUT still failed:
```
FAILED: Could not create knowledge base -- Response status code does not indicate success: 400 (Bad Request).
```
Replaying with `curl` surfaced:
```json
{"error":{"code":"","message":"Requested value 'extractedData' was not found."}}
```

**Root cause:** Both `postprovision.ps1.tpl` and `postprovision.sh.tpl` set
`outputMode: "extractedData"` on the knowledge base body. Per the
`2026-05-01-preview` Search REST API (`KnowledgeRetrievalOutputMode`
enum), the only valid values are `extractiveData` (return source data
verbatim) and `answerSynthesis` (synthesize an answer) — `extractedData`
is a typo that doesn't match either.

**Fix (applied to accelerator):** Changed `outputMode` from
`"extractedData"` to `"extractiveData"` in both `postprovision.ps1.tpl`
and `postprovision.sh.tpl`. Verified end-to-end on the Alliant build: with
both #28 and #29 fixed (and Cosmos/Storage public network access allowed),
postprovision completes with `POST-PROVISION COMPLETE` and
`verify-prototype.py` returns a full `PASS` (not `DEGRADED-PASS`) — every
starter scenario routes, calls its tool, and returns real seeded data.

**Diagnostic tip:** Same as #28 — replay the failing PUT with `curl` to see
the real validation message; the enum's valid values are documented under
`KnowledgeRetrievalOutputMode` in the Search REST API reference for the
knowledge bases `create-or-update` operation.

---

## 30. Foundry portal shows no Connection for the knowledge base — required manual "Manage" step

**Symptom:** After a fully successful build (Cosmos seeded, knowledge source
+ knowledge base wired via REST, agents registered, app verified PASS), the
knowledge base is invisible/unmanageable in the Foundry portal's **Knowledge
(Foundry IQ)** blade until a human manually creates a connection from the
Foundry project to the Azure AI Search resource (portal: Knowledge blade >
"Manage" > add the Search resource as a connection).

**Root cause:** Nothing in `infra/modules/foundry.bicep` or `search.bicep`
ever created a `Microsoft.CognitiveServices/accounts/projects/connections`
resource linking the Foundry project to the Search service. The knowledge
source/base themselves are created directly against the Search **service**
via `postprovision`'s REST calls (RESOLVED #28/#29), and `search_tool.py`
also talks to Search directly with the Container App's managed identity —
neither path needs a Foundry "connection" object to function at runtime.
But the Foundry **portal** UI resolves knowledge bases per-project through
the project's registered connections list, so without one the portal has
no way to associate the (perfectly functional) knowledge base with the
project, and any operator opening the portal has to add the connection by
hand before they can see or manage it there.

**Fix (applied to accelerator):** Added
`infra/modules/foundry-search-connection.bicep`, a new module that
references the existing Foundry project and Search service (`existing`
resources — no new identity/RBAC needed) and creates a
`category: 'CognitiveSearch'`, `authType: 'AAD'` connection between them
(no key ever stored, consistent with the rest of the scaffold). Wired into
`main.bicep` as a new module (`foundrySearchConnection`) that depends on
both the `foundry` and `search` module outputs, deployed after both
complete. Verified on the Alliant build: `azd up` shows `(✓) Done: Foundry
project connection: <hub>/<project>/<search-service>` and the connection is
then visible in the portal's Knowledge blade without any manual step.

**Scope note:** this is purely additive infrastructure — it does not change
any runtime code path (`search_tool.py`, `register_agents.py`) and does not
require a Container App redeploy, only a Bicep `azd provision`/`azd up` to
pick up the new resource.

---

## 31. Agents had zero native Foundry IQ attachment — migrated `search_knowledge_base` from FunctionTool to a native MCP tool

**Symptom:** Even with #30 fixed (portal shows the knowledge base under
Knowledge bases), opening any registered specialist agent in the Foundry
portal Playground showed nothing under the agent's own **Knowledge**
section — only the `run_sql_query` / `search_knowledge_base` custom
function tools appeared under **Tools**. The agent functionally worked
(RESOLVED architecture had `search_tool.py` calling the knowledge base's
`/retrieve` REST endpoint directly with the Container App's managed
identity), but there was no way for a portal operator to see or manage a
"Foundry IQ" attachment on the agent itself.

**Root cause:** Azure AI Foundry has two independent ways to give an agent
knowledge access:
1. The way this scaffold used: a custom `FunctionTool` (`search_knowledge_base`)
   whose local Python implementation (`search_tool.py`) called the Search
   REST API directly. Functionally correct, invisible to the portal's
   Knowledge blade.
2. The native way: an `MCPTool` (`type: "mcp"`) on the registered
   `PromptAgentDefinition`, pointed at the knowledge base's MCP endpoint
   (`{search_endpoint}/knowledgebases/{kb}/mcp`) via a project connection
   with `category: RemoteTool`, `authType: ProjectManagedIdentity`. This is
   what the portal's "Connect to Foundry IQ" button creates by hand, and
   what shows up under the agent's Knowledge section. Executed **server-side**
   by Foundry's Responses API via the single MCP tool the knowledge base
   exposes, `knowledge_base_retrieve` — see
   https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect.

**Fix (applied to accelerator, migrated fully to option 2):**
- `infra/modules/foundry-search-connection.bicep` now also creates a
  second connection (`mcpConnection`, name `<searchIndexName>-mcp`,
  `category: 'RemoteTool'`, `authType: 'ProjectManagedIdentity'`, target
  `{searchEndpoint}/knowledgebases/{searchIndexName}-kb/mcp?api-version=2026-05-01-preview`)
  plus a `Search Index Data Reader` role assignment for the **project's**
  system-assigned identity (the MCP call runs as the project, not the
  Container App's MI). `RemoteTool`/`ProjectManagedIdentity` are accepted
  by ARM for `Microsoft.CognitiveServices/accounts/projects/connections`
  but aren't yet reflected in the published Bicep type schema (as of
  2026-07) — the resource uses `properties: any({...})` to bypass Bicep's
  compile-time union check; verified against a live deployment.
- `agents/register_agents.py` special-cases the `search_knowledge_base`
  tool name: instead of looking it up in the FunctionTool catalogue, it
  builds an `MCPTool` (`server_label: "knowledge-base"`,
  `allowed_tools: ["knowledge_base_retrieve"]`,
  `project_connection_id: "<searchIndexName>-mcp"`) from
  `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_INDEX`. The agent.yaml-level tool
  name (`search_knowledge_base`) is unchanged, so no template/spec-level
  translation layer needed to change.
- `agents/orchestrator.py` no longer imports or dispatches
  `search_tool.search_knowledge_base` — MCP tool calls are executed by
  Foundry server-side, so there is nothing for MAF's
  `FunctionInvocationLayer` to invoke locally. `_TOOL_CALLABLES` documents
  the omission explicitly so it doesn't look like an accidental gap.
- `agents/tools/search_tool.py` deleted (dead code); the
  `search_knowledge_base` entry removed from `tool_definitions.yaml`.
- `agents/system_prompt_preamble.md` updated to describe
  `knowledge_base_retrieve` instead of a `search_knowledge_base(query)`
  function signature, in both the tool-signatures section and the
  tool-failure-handling section.

**Verified on the Alliant build:** after redeploy, `register_agents.py`
prints `tools=['run_sql_query', 'knowledge-base']` for specialists and
`tools=['knowledge-base']` for LossControlAgent (no SQL). The agent's
Foundry portal Playground now shows the knowledge base under **Knowledge**
without any manual step, and a direct REST call to
`{kb}/retrieve` returns real grounded content citing the uploaded
documents.

**Scope note:** touches maintained template source
(`orchestrator.py`, `register_agents.py`, `system_prompt_preamble.md`,
`tool_definitions.yaml`) and deletes `search_tool.py` — this is an
accelerator-level architecture change, not a per-prototype specialist
edit. Update `.github/agents/devlead.agent.md` and
`.github/specialists/backend-agent.md`'s "never touch" file lists (done)
if you add new MCP-backed tools in the future.

---

## 32. Three compounding bugs made the knowledge base look "empty" even after #28-#31 were fixed

**Symptom:** After fixing #28 (chatCompletionModel), #29 (outputMode typo),
#30 (portal connection), and #31 (native MCP tool), a fresh `azd up` still
produced an agent that answered "I don't know" for grounded questions, and
`{kb}/retrieve` returned `"[]"`. Investigating showed the knowledge
source's blob container had **zero documents uploaded** despite
postprovision printing `OK: Operational documents uploaded.` every single
run. Three independent bugs stacked to cause this:

**Bug A — doc upload step swallowed its own failure.** In both
`postprovision.ps1.tpl` and `postprovision.sh.tpl`, step 9's
`az storage blob upload-batch --auth-mode login` piped output to
`Out-Null` / `sed | ... || true` and never checked the exit code — it
printed "OK: Operational documents uploaded." unconditionally, even when
the upload failed outright. Fix: capture output, check
`$LASTEXITCODE`/`$?`, set `$FAILED = $true` and print the real error on
non-zero exit; print the actual uploaded file count on success.

**Bug B — the deployer was never granted Storage RBAC.** `--auth-mode
login` authenticates the doc-upload call as the **deployer** (the signed-in
`az` identity), not the Container App's managed identity. Step 4 only ever
assigned `Storage Blob Data Contributor` to the MI (`Assign-Cosmos`/`Search`
steps correctly assign to *both* MI and deployer — Storage was the one
exception). Once Bug A stopped swallowing the failure, the real error
surfaced: `You do not have the required permissions... Storage Blob Data
Contributor/Owner/Reader`. Fix: added
`Assign-ArmRole $DEPLOYER_OID "Storage Blob Data Contributor" $STORAGE_ID "Deployer"`
(and the bash equivalent) alongside the existing MI grant.

**Bug C — `Assign-ArmRole`/`assign_arm_role` hardcoded the wrong principal
type for deployer calls.** The shared RBAC helper always passed
`--assignee-principal-type ServicePrincipal`. `$DEPLOYER_OID` /
`$DEPLOYER_OID` is resolved via `az ad signed-in-user show`, which **only
succeeds for an interactively signed-in human** — if it were a service
principal (CI/CD), that call fails and the script already skips deployer
role assignments entirely (see the "No signed-in user" branch). So
whenever `$DEPLOYER_OID` is non-empty, its principal type is *always*
`User`, never `ServicePrincipal`. Passing the wrong hint isn't ignored —
Azure Resource Manager rejects it outright:
`(UnmatchedPrincipalType) The PrincipalId '...' has type 'User', which is
different from specified PrincipalType 'ServicePrincipal'`. This silently
broke **every** deployer-scoped role assignment in the script (Storage,
and both Search roles) on every interactive `azd up` — invisible until Bug
A's fix started surfacing real exit codes. Fix: added an optional
`$principalType`/`ptype` parameter (default `ServicePrincipal`, correct for
MI calls) to the shared helper in both templates, and pass `"User"`
explicitly on every `$DEPLOYER_OID`-scoped call (Storage Blob Data
Contributor, Search Index Data Contributor, Search Service Contributor).

**Bonus find while fixing Bug C — `$FAILED` PowerShell scoping bug.** The
first attempt at Bug A's exit-code check set `$FAILED = $true` *inside*
`Assign-ArmRole`, but PowerShell functions don't write to the caller's
scope by default — that assignment was local to the function and never
reached the script-level `if ($FAILED) { exit 1 }` gate at the bottom.
Fixed by using `$script:FAILED = $true`. (The bash `assign_arm_role`
equivalent doesn't have this problem — bash functions share the caller's
variable scope unless a var is declared `local`.)

**Bonus find #2 — stale role name.** Once Bugs A-C were fixed and errors
started surfacing for real, step 5b failed with
`ERROR: Role 'Azure AI User' doesn't exist.` Microsoft renamed several
Foundry RBAC roles (Azure AI User → **Foundry User**, Azure AI Owner →
Foundry Owner, Azure AI Account Owner → Foundry Account Owner, Azure AI
Project Manager → Foundry Project Manager); the old names no longer
resolve via `az role definition list`. Fixed both templates to use
"Foundry User".

**Verified end-to-end on the Alliant build:** after all fixes, a clean
`azd up` produces `POST-PROVISION COMPLETE` with every step reporting real
`OK`s (not swallowed failures), `verify-prototype.py` returns a plain
`PASS`, and a direct knowledge-base `/retrieve` call returns real content
from the uploaded documents (not `"[]"`).

**Diagnostic lesson generalized:** any postprovision step that pipes to
`Out-Null` / `| sed ... || true` without checking the exit code is a
silent-failure risk. If a step's user-visible narrative ("OK: X uploaded",
"OK: role assigned") never seems to line up with what's actually observed
downstream (empty containers, missing roles), suspect swallowed exit codes
first before assuming the downstream symptom is the root cause.

---

## 33. Intermittent "I couldn't retrieve..." on `run_sql_query` — `container` parameter was optional, but the SQL-style guide told the model to never write the real container name

**Symptom:** After #28-#32 were all fixed and a verified `PASS` build was
live, repeating the exact same starter question ("What is the risk score
for our top client?") sometimes succeeded and sometimes failed with the
generic tool-failure fallback text (confidence 0.2, "I couldn't retrieve
... at this moment"). Not reproducible on every call — looked like a
flaky backend issue.

**Root cause:** Container App console logs showed the real exception:
```
run_sql_query: could not infer Cosmos container from query. Known
containers: ['risk_assessments', 'loss_control_recommendations', 'clients'].
Pass `container=` explicitly or include the container name in the FROM
clause.
```
Two parts of the prompt/tool contract directly contradicted each other:
- `system_prompt_preamble.md`'s Cosmos SQL rules correctly taught
  `FROM c` (never the literal container name — "Always alias the
  container as `c`").
- `sql_tool.py`'s `run_sql_query` infers the container **only** from either
  (a) a literal container name appearing in the FROM clause, or (b) an
  explicit `container` argument. Since the preamble tells the model to
  never write the literal container name, and `tool_definitions.yaml`
  marked `container` as **optional** (not in `required:`), the model would
  intermittently omit it — most LLM calls include it anyway (present in
  a few-shot pattern elsewhere in context), but not reliably every time.
  When omitted, `_known_containers()` has 3 candidates, can't disambiguate,
  and raises `ValueError` — which `sql_tool.py`'s exception handler
  correctly sanitizes into the generic apology text (RESOLVED #27 working
  exactly as designed, just masking a different root cause).

**Fix (applied to accelerator):**
- `tool_definitions.yaml`: `container` moved into `run_sql_query`'s
  `required: [query, container]` list, with its description rewritten to
  say "REQUIRED" and explain why (the FROM clause deliberately doesn't
  carry the container name). Schema-level `required` is far more reliable
  than a prompt instruction alone for forcing a model to always populate
  an argument.
- `system_prompt_preamble.md`: both the SQL-rules section and the
  Tool-signatures section now explicitly state the `container` argument
  must always be passed, with a worked CORRECT example showing
  `container: "clients"` alongside `FROM c`, and a worked WRONG example
  showing exactly the failure mode observed (`FROM c` with no `container`
  argument).
- Re-registered all specialist agents (`register_agents.py`) to pick up
  the updated tool schema — no infra/Bicep changes needed, just a fresh
  `create_version()` call per agent (version bumped 3 → 4).

**Verified on the Alliant build:** repeated the previously-flaky question
("top client" / "top 5 clients") 6 times back-to-back via a raw WebSocket
script after re-registering; all 6 returned real data with confidence
0.9-1.0 (previously intermittent ~1-in-3 failure rate in casual use).

**Lesson:** when a tool parameter is "optional" purely because the model
*can* infer it from the query text, but the system prompt's own style
guide instructs the model to write the query in a way that removes that
exact inference signal (`c` alias instead of the real container name),
the parameter is not actually optional in practice — make it schema-required
so the model can't intermittently drop it, rather than relying on prompt
wording alone to keep two independent instructions in sync.

---

## 34. Numbered/bulleted lists in agent responses rendered as one dense run-on paragraph

**Symptom:** When a specialist's `summary` (or `recommended_action`) was
naturally a list — e.g. "Resources for understanding OSHA regulations
include: 1. **OSHA's Official Website**: ... 2. **OSHA Training
Institute**: ... 3. ... 4. ..." — the chat UI displayed it as one
unbroken wall of text with literal `1.`/`2.` digits and `**bold**`
asterisks-turned-bold inline, instead of a real bulleted/numbered list.
Several `spec.yaml` agents explicitly declare `response_format:
[bulleted, cite_sources]`, so this defeated the intended formatting for
every list-shaped answer.

**Root cause:** `frontend/public/index.html`'s `renderStructuredJSON()`
rendered `obj.summary` through `renderInlineMd()`, a function whose own
comment says "inline markdown only (code/bold/italic), no block
elements" — it never converts `1. `/`- ` list markers into `<ol>/<ul>`,
and raw `\n` characters are invisible in HTML without `<br>`/list markup
regardless. Two independent gaps compounded: (1) the model sometimes
writes every list item on one physical line with no newlines between
items at all ("1. **A**: foo 2. **B**: bar ..."), and (2) even on the
rare response where the model does put one item per line, a plain `<p>`
still collapses those newlines visually.

**Fix (applied to accelerator):** Added `renderSummaryMd()` in
`frontend/public/index.html` (the only frontend actually served —
`frontend/src/app.js` is dead/unreferenced code, not loaded by
`index.html`). It: (1) inserts a line break before every run-on
numbered (`\d+\.\s+`) or bulleted (`[-*]\s+`) marker so items that were
crammed onto one line become one-per-line first, (2) walks the resulting
lines and groups consecutive list-marker lines into a real `<ol>` or
`<ul>` with `<li>` per item (falling back to `<p>` for non-list prose
lines), and (3) still renders inline bold/italic/code within each
item/line via the existing `renderInlineMd()`. `renderStructuredJSON()`
now calls `renderSummaryMd()` for both `obj.summary` (changed from a
`<p>` to a `<div>` container, since a `<p>` cannot legally contain block
children like `<ol>`) and `obj.recommended_action`.

**Verified:** replayed the exact reported summary text through the
deployed script via a Playwright `page.evaluate()` harness (extracting
`escHtml`/`renderInlineMd`/`renderSummaryMd` from the live served script
and re-invoking them) — output is a `<p>` intro sentence followed by a
real `<ol><li>` list with bold headers preserved, matching the intended
`response_format: bulleted` behavior. Non-list prose responses
(confirmed via live chat) still render as plain paragraphs, unaffected.

**Scope note:** frontend-only change (`azd deploy`, no `azd provision`
needed). Applies to every prototype build going forward since it's fixed
at the template source (`accelerator/templates/prototype/frontend/public/index.html`),
not a per-prototype edit.

---

## 35. `response_format: [bulleted, ...]` from spec.yaml was silently dropped — agents never told to write lists

**Symptom:** Even after #34 fixed the *rendering* of lists, most answers
from `RiskAssessmentAgent`/`LossControlAgent`/`PolicyProgramAgent` (all
three declare `response_format: [bulleted, cite_sources]` in `spec.yaml`)
came back as flowing prose with no list structure at all — e.g. "Various
workplace safety certifications are available... Examples include
OSHA's Outreach Training Program... ISO 45001... Certified Safety
Professional..." — one paragraph, zero bullets. #34's renderer had
nothing to work with because the model never wrote a list in the first
place.

**Root cause:** `spec.yaml`'s `response_format` array conflates two
unrelated things — an **output-key hint** (`cite_sources` → add a
`citations` field, correctly implemented) and a **formatting
instruction** (`bulleted` → tell the model to write lists). The
agents-builder step 4 specialist doc only documented the output-key
translation ("add fields from spec.yaml agents[].response_format" under
Specialist output keys). Nothing anywhere told the LLM specialist writer
to also turn `bulleted` into an explicit system-prompt instruction, so
every one of the three affected agents' `system_prompt` was missing any
mention of list formatting — the model defaulted to prose because
nothing asked for anything else.

**Fix (applied to this build and the accelerator template):**
- Added a `## Response formatting` section to all three affected
  agents' `agent.yaml` `system_prompt` (`RiskAssessmentAgent`,
  `LossControlAgent`, `PolicyProgramAgent`): *"Format the `summary`
  field as a markdown-style list (one item per line, e.g. `- item` or
  `1. item`) whenever the answer covers multiple distinct points...
  Use plain prose only when the answer is a single fact or a short
  narrative statement."* Re-ran `register_agents.py` to publish the
  updated prompts (agent versions bumped 4 → 5; no infra changes
  needed).
- Updated `.github/specialists/agents-builder.md`'s `agent.yaml`
  structure template to add the same `## Response formatting` section,
  explicitly marked **REQUIRED whenever response_format includes
  "bulleted"**, and clarified that `response_format` values are not all
  output-key hints — only `cite_sources`-style values map to an output
  key; everything else (like `bulleted`) needs its own instruction.

**Verified live:** re-asked the exact previously-prose question ("List
the certifications related to workplace safety...") after
re-registering — the response now renders as a real 8-item `<ol>/<ul>`
list with bold headers, confirmed via the browser accessibility snapshot
(`list` node containing 8 `listitem` nodes), combining correctly with
#34's rendering fix.

**Lesson:** when a spec-level array field (like `response_format`) mixes
semantically different instruction types, a doc that only shows how to
translate *one* of those types (output keys) will silently drop the
others (formatting) unless the template explicitly calls out every
value that needs separate handling.

---

## 36. Bold summary wrapper leaked `font-weight:600` into every list item

**Symptom:** Immediately after #35 fixed the missing list-formatting
instruction, the user reported the opposite-looking problem: *everything*
in a list response rendered bold — every certification name and its full
description — with no visual distinction between the `**marked**` term
and the surrounding plain text.

**Root cause:** `renderStructuredJSON()`'s summary wrapper `<div>` still
carried `font-weight:600` — inherited from the pre-#34 design where
`obj.summary` was always a single plain-prose `<p>` styled as a bold
"headline" sentence. `font-weight` cascades to children, so once
`renderSummaryMd()` started emitting real `<ol>/<ul><li>` elements for
list content, every `<li>` (and its full text, not just any `<strong>`
portions) inherited the forced bold from the wrapper — defeating the
whole purpose of `**text**` → `<strong>` emphasis.

**Fix:** removed `font-weight:600` from the summary wrapper `<div>`.
Parameterized `renderSummaryMd(text, boldProse)`: only non-list prose
`<p>` lines get `font-weight:600` when `boldProse` is `true` (preserves
the original bold "headline" look for plain single-sentence answers,
passed only for `obj.summary`); list `<li>` items never get forced bold —
only inline `<strong>` from `**text**` markdown stands out.
`obj.recommended_action` continues to call `renderSummaryMd()` without
`boldProse`, unchanged from its original (already-correct) normal-weight
behavior.

**Verified:** re-asked the same list-producing question after
redeploying (`azd deploy app`); screenshot of the live response confirms
list item text renders at normal weight, with only actual `<strong>`
markdown (when present) standing out — no more uniform bolding.

**Scope note:** frontend-only change (`azd deploy app`, no `azd provision`
needed), fixed at the template source
(`accelerator/templates/prototype/frontend/public/index.html`), so it
applies to every future prototype build automatically.

**Lesson:** when evolving a single-purpose renderer (always-bold summary
paragraph) into a multi-mode renderer (prose *or* list), audit every
CSS property inherited from the old single-mode wrapper — styling that
made sense for one visual treatment (a bold headline sentence) can
silently defeat the semantic distinction (bold vs. normal) that the new
mode (list with selective inline emphasis) depends on.

---

## 37. Foundry documentation review — 4 fixes: dead tracing, missing anti-hallucination instruction, unwired KB retrieval steering, unvalidated hardcoded KB model

**Context:** after RESOLVED #28-36 closed out the Foundry IQ/MCP migration
and UI bugs, a deliberate re-read of current Microsoft Foundry/Azure AI
Search documentation (knowledge base creation, agent-to-KB MCP connection,
agent development lifecycle/tracing) against the live Alliant build turned
up four real, verified gaps — tracked first in a scratch `TEMP_BACKLOG.md`,
then implemented together.

**1. Application Insights + OpenTelemetry fully provisioned but never
initialized.** `monitoring.bicep` creates a real App Insights resource,
`main.bicep` wires `APPLICATIONINSIGHTS_CONNECTION_STRING` into the
Container App, and `requirements.txt` already listed
`azure-monitor-opentelemetry` — but a repo-wide grep confirmed nothing ever
called `configure_azure_monitor()`. This directly explains a recurring
session pain point (repo memory: *"Log Analytics/App Insights had no data
flowing in this environment"*) — it wasn't an environment quirk, telemetry
was never emitted. **Fix:** added a guarded `configure_azure_monitor()`
call at the very top of `backend/main.py`, before the `from fastapi import
FastAPI` import (import order matters for the distro's FastAPI
auto-instrumentation to patch correctly — confirmed via Microsoft's own
OpenTelemetry troubleshooting doc). No-ops when
`APPLICATIONINSIGHTS_CONNECTION_STRING` is absent (local dev).

**2. Missing anti-hallucination fallback instruction for
`knowledge_base_retrieve`.** `system_prompt_preamble.md` told specialists
to invoke the MCP knowledge-base tool for narrative questions, but never
said what to do when retrieval comes back empty — leaving room for a
specialist to quietly answer from its own training data instead. Fixed by
adding an explicit instruction (mirroring Microsoft's own recommended
agent-instruction template at
[foundry-iq-connect](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect#optimize-agent-instructions-for-knowledge-retrieval)):
say so plainly in `summary` rather than guessing; never fabricate policy/
narrative content that didn't come back from retrieval.

**3. No `retrievalInstructions` set on the knowledge base.** The KB
creation payload set `knowledgeSources`/`models`/`outputMode`/
`retrievalReasoningEffort` but never `retrievalInstructions` — a field
that exists specifically to steer which indexed content the KB prioritizes
per query, without touching agent prompts. Fixed by deriving a
`retrievalInstructions` string from `manifest.json`'s existing
`documentSpecs` (title + up to 4 `key_topics` per document — no new
manifest field needed) in `fill-templates.py`, added as a new
`{{KB_RETRIEVAL_INSTRUCTIONS}}` placeholder consumed by both
`postprovision.ps1.tpl`'s `$KB_BODY` and `postprovision.sh.tpl`'s
`KB_JSON` (passed as a `sys.argv` parameter there, `json.dumps`-escaped
automatically — safer than the PowerShell side, which sanitizes quotes/
backticks at generation time in `fill-templates.py` instead, matching the
existing lightweight convention already used for `{{CUSTOMER_NAME}}`).
Applied to the live Alliant build via a direct REST `PUT` (idempotent
create-or-update) rather than re-running the whole postprovision script.

**4. Hardcoded `gpt-4o-mini` KB reasoning model had no validation gate.**
`postprovision.*.tpl` hardcode `deploymentId = "gpt-4o-mini"` for the
knowledge base's query-planning model (confirmed deployed and working),
but `spec-validator.py`'s only cross-field check —
*"every `agents[].model` resolves to a `modelDeployments[].deploymentName`"*
— never covered this hardcoded, non-agent reference. Since `spec.yaml` is
hand-editable, a future edit dropping the `gpt-4o-mini` deployment would
pass validation and preflight cleanly, then fail confusingly at
postprovision step 10. Fixed by adding `check_kb_reasoning_model_deployed()`
to `preflight.py`, hard-failing if `gpt-4o-mini` isn't present in
`manifest.json`'s `modelDeployments[].deploymentName`.

**Bonus regression caught while verifying #4:** running `preflight.py`
after these changes surfaced an unrelated, pre-existing failure from
RESOLVED #31's MCP migration — `check_tools_resolve()` was never updated
to know that `"search_knowledge_base"` in `agent.yaml` resolves to a native
MCPTool in `register_agents.py` (not a `tool_definitions.yaml`
FunctionTool), so every agent declaring it failed preflight with
`tool 'search_knowledge_base' not in tool_definitions.yaml`. This would
have hard-blocked the very next `@devlead build` after #31 shipped. Fixed
by adding `"search_knowledge_base"` to `check_tools_resolve()`'s known-tool
set, matching `register_agents.py`'s `_MCP_TOOL_NAME` special-casing.

**Verified:** all 72 unit tests pass; `preflight.py` exits 0 end-to-end
(previously failed with 3 errors before the bonus fix); re-registered
agents (`register_agents.py`, versions 5→6 with the new anti-hallucination
instruction); redeployed the backend (`azd deploy app`, new revision
confirmed serving 100% traffic); confirmed the live knowledge base's
`retrievalInstructions` field via a REST `GET` after the `PUT`;
`verify-prototype.py` still returns a clean 4/4 PASS post-deploy. Direct
confirmation of trace data landing in Application Insights was not
observed within this session's window (likely ingestion latency, and
`az containerapp logs show` hit a transient "Could not find a replica"
CLI error unrelated to the code change) — recommend a follow-up check of
the App Insights "Transaction search" pane after a few live chat turns.

**Scope note:** all four fixes are template-level
(`backend/main.py`, `system_prompt_preamble.md`, `fill-templates.py`,
`postprovision.ps1.tpl`/`.sh.tpl`, `preflight.py`) plus the bonus
`check_tools_resolve()` fix — every future prototype build gets all of
this automatically.

**Lesson:** a genuinely useful way to find latent bugs in a mature
accelerator is to periodically re-read the current state of the
third-party docs it integrates with (they change — API versions, recommended
patterns, new optional fields) and diff that against what the templates
actually do, rather than only reacting to symptoms as they surface. Three
of these four gaps (#1, #3, #4) were "silently missing," not "visibly
broken" — nothing in the running prototype complained about them.

---

## 38. Telemetry pipeline hardening — dead build context, missing log destination, fragile OTel export

**Context:** direct follow-up to #37's tracing fix. The `configure_azure_monitor()`
call from #37 was deployed and the app worked, but zero telemetry ever
appeared in Application Insights. Chasing that down surfaced two more real,
independently-verified infrastructure bugs plus a scare worth recording.

**1. No `.dockerignore` — deployments were silently at risk of breaking
entirely.** `azd deploy`'s remote-build packaging step tars up the whole
Docker build context. With no `.dockerignore`, that meant the *entire*
`generated/prototype/` directory — including `.azure/` state and a 1.6MB
generated ARM template under `infra/` — was being sent to the remote
builder on every deploy. This produced a **reproducible
`archive/tar: write too long` packaging failure** that blocked deployment
outright (hit 3 times in a row during this session). **Fix:** added
`accelerator/templates/prototype/.dockerignore`, scoping the build context
to only `backend/`, `agents/`, `frontend/`, `db/` (what the Dockerfile
actually `COPY`s), anchoring root-level excludes with a leading `/` so
files legitimately needed deeper in kept directories aren't affected.

**2. Container Apps Environment had no log destination configured.**
`az containerapp env show --query properties.appLogsConfiguration`
returned `{"destination": null, "logAnalyticsConfiguration": null}` —
console/stdout logs were never being forwarded anywhere queryable, which
is also why `az containerapp logs show` kept failing with "Could not find
a replica for this app" throughout the session. `monitoring.bicep` already
provisions a Log Analytics workspace and outputs `logAnalyticsWorkspaceId`,
but `container-app.bicep`'s `managed-environment` module never consumed
it. **Fix:** added a `logAnalyticsWorkspaceResourceId` param to
`container-app.bicep`, wired `appLogsConfiguration = { destination:
'log-analytics', logAnalyticsWorkspaceResourceId: ... }` (empty string
omits the block entirely, non-breaking for older manifests), passed
`monitoring.outputs.logAnalyticsWorkspaceId` from `main.bicep`. Confirmed
the exact AVM parameter shape against the `avm/res/app/managed-environment`
module's README before implementing (discriminated union on
`destination`, `log-analytics` variant requires
`logAnalyticsWorkspaceResourceId`). Requires `azd provision`, not just
`azd deploy` — this is an environment-level, not container-level, change.

**3. `backend/main.py` hardened against telemetry loss on scale-to-zero.**
This template's Container App has `minReplicas: 0`. The OpenTelemetry
SDK's default `BatchSpanProcessor` only exports every few seconds; a
replica killed between requests can lose whatever hasn't been flushed
yet. Added: `configure_azure_monitor()` wrapped in try/except with
`print()` (visible even if `logging` itself is broken); `OTEL_BSP_SCHEDULE_DELAY`
/ `OTEL_BLRP_SCHEDULE_DELAY` reduced from the 5000ms default to 1000ms; a
`_flush_telemetry()` helper calling `force_flush()` on both the tracer and
logger providers, invoked on a 10-second periodic background task *and*
in the FastAPI shutdown lifespan block.

**Scare, caught and recovered:** immediately after the `azd provision` run
that applied fix #2, the container app briefly served Azure Container
Apps' own default "Welcome" placeholder page instead of the real app.
Both the placeholder and the real app return `HTTP 200`, so status-code-only
checks (`curl -w "%{http_code}"`) completely missed this — it was only
caught by reading actual response **body** content. `az containerapp
revision list` reported `"healthState": "Healthy"` the entire time,
which did not reflect the real routing problem. Recovered by forcing a
fresh `azd deploy app` (new image build, new revision); confirmed via
response-body inspection and a full `verify-prototype.py` 4/4 PASS
afterward.

**4. The actual missing piece: no `Microsoft.Insights/diagnosticSettings`
resource existed at all.** Even with `appLogsConfiguration.destination`
correctly set to `"log-analytics"` from fix #2, `az monitor
diagnostic-settings list --resource <container-app-id>` AND `--resource
<environment-id>` both returned `[]`. A user-run "Observability Agent"
report independently confirmed this: *"I also couldn't find an attached
diagnostic setting for this Container App in Resource Graph, which
usually means request/response logs aren't being sent to Log
Analytics."* `appLogsConfiguration` alone is NOT sufficient — Azure
Monitor diagnostic settings must be created as a **separate resource**.
Attempting `az monitor diagnostic-settings create` at the **container
app** level fails outright: `(BadRequest) Category
'ContainerAppConsoleLogs' is not supported` — log categories
(`ContainerAppConsoleLogs`, `ContainerAppSystemLogs`) can only be
configured in a diagnostic setting scoped to the **environment**
resource; the container-app-level diagnostic-settings API only accepts
an `AllMetrics` category. **Fix:** added an `existing` reference to the
managed environment plus a `Microsoft.Insights/diagnosticSettings`
resource scoped to it in `container-app.bicep`, forwarding
`ContainerAppConsoleLogs` and `ContainerAppSystemLogs` to the same Log
Analytics workspace `appLogsConfiguration` already points at. Verified
live via `az monitor diagnostic-settings create` first (confirmed the
fix works), then codified the same resource in bicep and re-applied via
`azd provision` — `az monitor diagnostic-settings list` now returns the
`cae-console-logs` setting with both log categories enabled.

**Second scare, same pattern as before:** re-running `azd provision` for
fix #4 triggered the *exact same* placeholder-page regression as fix #2's
provision run — confirming this is a **reproducible pattern**, not a
one-off fluke: any `azd provision` that touches the Container Apps
Environment resource can leave the Container App momentarily serving
Azure's default "Welcome" page instead of the real app, invisible to
status-code-only health checks. Recovered the same way both times: a
follow-up `azd deploy app` re-establishes correct routing. Given this now
happened twice in a row, treat it as a standing operational rule for this
accelerator: **always follow any `azd provision` with an `azd deploy app`
and a response-body health check**, not just a status-code check.

**Verified:** all four fixes applied and confirmed at the configuration
level — `.dockerignore` lets `azd deploy` complete again;
`appLogsConfiguration.destination` is `"log-analytics"`; the
`cae-console-logs` diagnostic setting exists with both log categories
enabled; `main.py`'s hardened telemetry code is present in the running
image. Full post-provision cycle re-run cleanly to completion (Cosmos
seed, doc upload, Foundry IQ wiring, agent registration all OK) and
`verify-prototype.py` returns a clean 4/4 PASS after the recovery
redeploy. **Still not yet confirmed:** actual telemetry rows appearing in
`ContainerAppConsoleLogs` or Application Insights `requests`/`traces` —
the diagnostic setting is now real and correctly configured, but data had
not yet appeared by the end of this session. See the open follow-up in
`TEMP_BACKLOG.md`.

**Scope note:** all fixes are template-level (`.dockerignore`,
`container-app.bicep`, `main.bicep`, `backend/main.py`) — every future
prototype build gets all of this automatically, including the
previously-missing diagnostic settings resource.

**Lesson:** an HTTP 200 status code alone never proves an application is
actually serving traffic on Azure Container Apps — the platform's own
placeholder page returns 200 too. Always inspect response body content,
especially right after any environment-level (not just container-level)
infrastructure change. Also: `appLogsConfiguration` and
`Microsoft.Insights/diagnosticSettings` are two independent mechanisms in
Azure Container Apps that are easy to conflate — setting one without the
other silently does nothing observable.

## 39. Agent Framework's own GenAI instrumentation needs a separate activation call — and a deeper, still-open workspace-wide ingestion gap

**Context:** direct follow-up to #38. A user-shared documentation link
(`learn.microsoft.com/azure/azure-monitor/app/agents-view`) led through
`app-insights-overview?tabs=agents` to
`learn.microsoft.com/agent-framework/agents/observability`, which
documents a mechanism #37/#38 had missed entirely: **Microsoft Agent
Framework's own GenAI spans (`invoke_agent`, `chat`, `execute_tool` — the
actual agent/model/tool-call telemetry, not generic HTTP request spans)
are gated behind a separate instrumentation switch from
`configure_azure_monitor()`.** `configure_azure_monitor()` only sets up
the OTel *export pipeline*; it does not turn on Agent Framework's own
instrumentation code paths. Activation requires either the
`ENABLE_INSTRUMENTATION=true` environment variable, or an explicit
`enable_instrumentation()` call from `agent_framework.observability`
(their documented "Pattern 3: Third party setup", the exact scenario that
matches this codebase's use of `azure-monitor-opentelemetry` directly).

**Fix:** added
```python
from agent_framework.observability import enable_instrumentation
enable_instrumentation()
```
immediately after `configure_azure_monitor()` succeeds, inside the same
try/except block in `backend/main.py`. Copied to
`generated/prototype/backend/main.py`. 72/72 tests pass. Deployed via
`azd deploy app` (container-level only — does not require `azd provision`,
so it did not trigger the #38 placeholder-page regression). App health
confirmed via response-body curl check; `verify-prototype.py` returned a
clean 4/4 PASS after generating fresh traffic across all 3 specialist
agents.

**This did not resolve visibility, and revealed a much bigger, separate
problem:** re-checking Application Insights immediately after deploying
this fix and generating traffic, `requests`/`traces`/`dependencies` were
still all empty. Broadening the check proved this is *not* an application
gap at all — **the entire Log Analytics workspace
(`alliant-alliant-log`) returned zero rows across every table for the
prior 24 hours**, including `ContainerAppSystemLogs` (100%
platform-generated, has nothing to do with application code) and a
blanket `search * | ago(1d)` across the whole workspace. Every
app/resource-level configuration was re-verified correct: the connection
string is present in the running container's actual environment; the App
Insights component has `ingestionMode: LogAnalytics` and a correct
`workspaceResourceId`; both the workspace's and the component's public
network access flags are `Enabled`; the Container Apps Environment has no
VNet integration (`vnetConfiguration: null`) that could block egress; and
the `cae-console-logs` diagnostic setting from #38 is still present and
correctly scoped to the environment. A related-but-distinct lead:
`az policy state list` surfaced that the Foundry PROJECT and HUB
resources both have NonCompliant diagnostic-settings policies
(`ProjectsAIFoundry_Diagnostics_Enable`,
`CognitiveServices_Diagnostics_Enable`) — Foundry's own server-side
tracing (a separate, portal-connected mechanism, distinct from all
app-level OTel work) was never wired up either, though this doesn't
explain the Log Analytics workspace's own zero-ingestion state.

**Status: open.** This is now understood to be an infrastructure/ingestion
pipeline problem, not a code problem — 24 hours with literally zero data
anywhere in the workspace is well past any normal first-time-destination
delay (Microsoft's own docs cite 10-15 minutes). Next steps: check
tenant/subscription-scoped Azure Policy (not just resource-group-scoped)
for anything touching `Microsoft.OperationalInsights` or
`Microsoft.Insights/diagnosticSettings` resource types broadly; check the
resource group's Activity Log for failed diagnostic-settings delivery
events; try the Portal's Live Metrics stream for
`alliant-alliant-appi`, which bypasses Log Analytics ingestion entirely
and would prove/disprove whether the OTel SDK-side export itself is
working in real time. Full detail in repo memory
(`/memories/repo/ai-prototype-accelerator.md`).

**Lesson:** when every documented app-level telemetry gap has been fixed
and verification still comes back empty, broaden the check to
platform-generated signals that don't depend on application code at all
(e.g. `ContainerAppSystemLogs`, or an unscoped `search *` across the whole
workspace) — this quickly distinguishes "still a code bug" from "an
infrastructure/ingestion bug upstream of any code," rather than
re-auditing the same application layer repeatedly.

## 40. The actual root cause of the whole telemetry saga: a poisoned pip dependency resolution (PR #23, external review)

**Context:** #38 and #39 fixed every app-level gap that direct debugging
surfaced (missing `configure_azure_monitor()` call, missing diagnostic
settings resource, missing `enable_instrumentation()`), yet verification
kept coming back with zero data — #39 concluded this must be a
workspace-wide Log Analytics ingestion outage. That conclusion was wrong,
or at least incomplete: an external review (Claude Code, via PR #23)
found the actual bug by reproducing the app's dependency resolution in a
clean environment instead of trusting the "all app-level gaps are fixed"
assumption.

**The real bug:** `backend/requirements.txt` co-pinned individual
OpenTelemetry packages (`opentelemetry-sdk>=1.39.0`,
`opentelemetry-instrumentation-fastapi>=0.50b0`) alongside the
`azure-monitor-opentelemetry` distro, all with open-ended lower bounds and
no upper bounds. `opentelemetry-sdk 1.44.0` was published 2026-07-16 — the
day before this debugging session — and every `azd deploy app` rebuild on
07-17 picked it up as the newest SDK satisfying `>=1.39.0`. pip's
backtracking resolver then had to find an `azure-monitor-opentelemetry`
version compatible with that fresh SDK, and landed on the OLDER `1.8.2`
(whose own version constraint happened to be wide enough to accept 1.44.0,
despite never having been tested against it). `azure-monitor-opentelemetry
1.8.2`'s log exporter still imports a symbol removed in SDK 1.44.0:

```
ImportError: cannot import name 'LogData' from 'opentelemetry.sdk._logs'
```

That exception fires *inside* `configure_azure_monitor()` — caught by
`main.py`'s defensive try/except (added in #38 specifically to prevent a
telemetry failure from crashing the app), printed to stdout (which itself
went nowhere queryable until #38's console-log fixes), leaving
`_TELEMETRY_CONFIGURED = False`. The app served every request completely
normally, with zero SDK telemetry, no visible error anywhere a normal user
or even careful CLI debugging would look. This is why nothing #38/#39
implemented ever produced visible data — there was no working exporter
underneath any of it.

**Independent verification (not just trusting the PR description):**
reproduced both sides in clean venvs.
- Installing the OLD three-line pin block resolved to exactly
  `opentelemetry-sdk 1.44.0` + `azure-monitor-opentelemetry 1.8.2`, and
  `from azure.monitor.opentelemetry import configure_azure_monitor` raised
  the exact `ImportError` above.
- Installing the fixed single line (`azure-monitor-opentelemetry>=1.8.9`,
  no explicit sub-package pins) resolved to `distro 1.8.9 + sdk 1.43.0 +
  opentelemetry-instrumentation-fastapi 0.64b0`, and the same import
  succeeded cleanly.

**Fix:** dropped the explicit `opentelemetry-sdk`/
`opentelemetry-instrumentation-fastapi` pins from
`accelerator/templates/prototype/backend/requirements.txt` entirely — the
`azure-monitor-opentelemetry` distro already declares and manages its own
compatible OTel stack, including FastAPI instrumentation. Raised the floor
to `azure-monitor-opentelemetry>=1.8.9`. Merged via PR #23 (squash merge
`98a3909`), synced into `generated/prototype/`, redeployed to the live
Alliant build via `azd deploy app`.

**Verified live, end to end:** ran `verify-prototype.py` (4/4 PASS) against
the redeployed app, then queried the Log Analytics workspace directly for
the workspace-native App Insights tables (see the lesson below for why
this matters) and found real, fresh rows exactly matching the test run:
`AppRequests` (7 rows — `HTTP /chat`, `GET /config`, `GET /health`, correct
durations), `AppDependencies` (81), `AppTraces` (9), and — the definitive
proof Agent Framework's own instrumentation is working —
`AppGenAIContent` (4 rows) with real `gen_ai.operation.name: invoke_agent`
spans, `gen_ai.provider.name: microsoft.agent_framework`, real agent names
(`RiskAssessmentAgent-prototype`, `PolicyProgramAgent-prototype`), token
usage counts, and captured tool definitions.

**The #39 "workspace-wide zero ingestion" finding was a compounding
misdiagnosis, not a separate bug:** it had two causes layered together.
(1) The real cause: telemetry genuinely wasn't being generated at all,
because `configure_azure_monitor()` was crashing before it could export
anything. (2) A diagnostic-methodology error on top of that: querying via
`az monitor app-insights query --app <id>` against the classic table
names (`requests`/`traces`/`dependencies`) returns nothing for a
**workspace-based** Application Insights resource (confirmed via
`ingestionMode: LogAnalytics` on the component) — the real Kusto tables
in that mode are the `App*`-prefixed ones (`AppRequests`, `AppTraces`,
`AppDependencies`, `AppExceptions`, `AppMetrics`, `AppGenAIContent`, ...).
Once (1) was fixed, querying the correct table names immediately showed
data that had been flowing normally all along.

**Still genuinely open, separate issue:** `ContainerAppConsoleLogs` (the
platform stdout/stderr pipe via #38's diagnostic setting) remained at 0
rows even an hour after this fix — confirmed with a direct count query.
This is a completely different mechanism from the Application Insights
SDK pipe fixed here, and doesn't block observability in any meaningful
way now that the actual telemetry (requests/traces/GenAI spans) is
flowing. Tracked as a low-priority follow-up in `TEMP_BACKLOG.md` if
console log visibility is ever needed.

**Lessons:**
- An unbounded version pin (`>=X`) on a package that's also a transitive
  dependency of a higher-level "distro" package can let pip's backtracking
  resolver silently pick an incompatible pair neither half was tested
  against — especially when the distro's own version is allowed to
  *decrease* to find compatibility with an unexpectedly-new transitive
  dependency. Don't co-pin packages that a "batteries-included" package
  already manages; let it own its own dependency graph.
- A defensive try/except around a telemetry setup call (added specifically
  so a broken exporter can't crash the whole app) is exactly the kind of
  code that can mask a real, fixable bug for an extended period — the
  exception fired every single time, correctly, and was still invisible
  until console logging itself was fixed (#38) and someone thought to
  actually read the startup output.
- Always verify a query returns *some* rows for *anything* known-good
  before concluding "zero rows = broken pipe" — check whether the query
  is even hitting the right table/schema for the resource's actual
  configuration (classic vs. workspace-based Application Insights use
  different Kusto table names) before escalating to an infrastructure
  investigation.
- When a second, independent set of eyes (a different agent/session)
  offers to review a stuck problem, let them re-derive the diagnosis from
  scratch by reproducing the environment rather than starting from "here's
  what's already been ruled out" — the prior session's conclusion (workspace
  ingestion outage) was wrong, and starting fresh (a clean venv install of
  the actual pinned requirements) is what surfaced the real bug.

## 41. The final piece: `ContainerAppConsoleLogs` was empty because two mutually exclusive log-routing mechanisms were mixed (PR #24, external review)

**Context:** direct follow-up to #40. With #40's fix confirmed, App Insights
telemetry (`AppRequests`/`AppTraces`/`AppGenAIContent`) was flowing
correctly — proving the Log Analytics workspace itself ingests fine and
retiring #39's "workspace-wide outage" theory for good. That left the
`ContainerAppConsoleLogs`/`ContainerAppSystemLogs` gap as a narrower,
config-specific question rather than an infrastructure-wide one.

**The real bug:** `container-app.bicep` mixed two mutually exclusive
Container Apps log-routing mechanisms:
- `appLogsConfiguration.destination: 'log-analytics'` (what the template
  had) makes the platform write console/system logs *directly* to
  **custom** tables (`ContainerAppConsoleLogs_CL` /
  `ContainerAppSystemLogs_CL`). In this mode, diagnostic settings on the
  environment are **ignored entirely**.
- The `cae-console-logs` diagnostic-settings resource (added in #38)
  only routes the **standard** tables (`ContainerAppConsoleLogs` /
  `ContainerAppSystemLogs`) to a workspace, and per Microsoft's own
  Container Apps log-options doc, it only takes effect when the
  destination is **`azure-monitor`**: *"If you selected Azure Monitor as
  your logs destination, you must also configure the diagnostic
  settings."*

With destination `log-analytics` set, the diagnostic setting existed
(confirmed present and correctly scoped) but never emitted anything,
because that setting only wires up under the `azure-monitor` destination.
Every verification check in #38/#39/#40 queried the **standard**
`ContainerAppConsoleLogs` table — which could never populate under that
combination. Zero rows was the expected behavior of the misconfiguration,
not ingestion latency. This rhymes with #40's own root cause: #40 was
classic-vs-`App*` table names; this is standard-vs-`_CL` table names —
both pipes were being queried at addresses the live config never wrote to.

**Independent verification before merging (not just trusting the PR
description):**
1. Confirmed live config: `az containerapp env show` on
   `alliant-alliant-cae` showed `destination: "log-analytics"`, matching
   the claimed misconfiguration exactly.
2. Fetched Microsoft's own log-options doc directly and found the exact
   sentence confirming diagnostic settings are only wired for the
   `azure-monitor` destination.
3. Diffed the actual bicep change: `destination: 'log-analytics'` →
   `destination: 'azure-monitor'`, with a block comment documenting both
   mechanisms.
4. `az bicep build` on the new file compiled cleanly — `azure-monitor` is
   a valid discriminated-union value in AVM's `managed-environment`
   0.13.3.
5. **Live-tested the actual behavior change before merging anything**:
   ran `az containerapp env update --logs-destination azure-monitor`
   directly against the live environment (a fast, reversible CLI-level
   test, no bicep/provision involved yet), confirmed the config flipped,
   restarted the active revision so a fresh replica picked up the new
   log-routing destination (the existing replica appeared to keep
   whatever routing was active at its own startup), generated fresh
   traffic via `verify-prototype.py` (4/4 PASS), then queried
   `ContainerAppConsoleLogs` and found **34 real rows** — genuine uvicorn
   access logs (`GET /health HTTP/1.1" 200 OK`, `WebSocket /chat
   [accepted]`, `connection open`/`connection closed`) with timestamps
   matching the test traffic exactly.

**Fix merged:** PR #24 (squash-merged `c74fa833`), synced into
`generated/prototype/`, 72/72 tests pass. Live environment already
reflects the fix (applied via the CLI test above, itself the destination
value the merged bicep now declares) — a future `azd provision` will
simply confirm this state rather than change it.

**Note on restart behavior:** immediately after `az containerapp revision
restart`, a health check briefly timed out (cold start from `minReplicas:
0` scaling back up) — recovered on the next request with a longer
timeout. Not the RESOLVED #38 placeholder-page regression (this was a
direct CLI-level environment property update, not an `azd provision` /
bicep deployment), just an ordinary scale-from-zero cold start. Still,
the same lesson applies: don't trust the very first request after any
environment/replica-affecting change; retry before concluding failure.

**Lesson:** Azure Container Apps' logging model has (at least) three
independent axes that are easy to conflate: (1) `appLogsConfiguration`'s
`destination` value (`log-analytics` vs `azure-monitor` vs `none`), (2)
whether a `Microsoft.Insights/diagnosticSettings` resource exists at all
(#38), and (3) which Kusto table names the query actually needs to hit
for the current destination (`_CL` custom tables vs. standard names).
Getting any one of these three right while the others are wrong still
produces zero visible rows — treat all three as one interdependent
system, not separately-verifiable checkboxes.
