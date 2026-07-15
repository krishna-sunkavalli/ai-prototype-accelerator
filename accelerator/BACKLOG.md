# Accelerator Backlog

Long-term fixes and improvements to the accelerator itself, surfaced during real customer builds. Deployment-blocking bugs still live in [KNOWN_ISSUES.md](KNOWN_ISSUES.md); this file is the broader triage list including workflow, UX, and pipeline discipline items.

Update the **Status** column when work starts / ships. Move ✅ shipped items to [RESOLVED.md](RESOLVED.md).

---

## Priority 1 — Every fresh customer hits these

### 1. `azd up` invents env / region / RG instead of reading `manifest.json`
- **Status:** ✅ Shipped 2026-07-14 — [`accelerator/scripts/deploy.py`](scripts/deploy.py) is the manifest-aware `azd up` wrapper; devlead's Step 10 calls it instead of raw `azd up`. Reads `manifest.json`, creates or selects the target azd env, force-sets `AZURE_LOCATION` / `AZURE_RESOURCE_GROUP` / `AZURE_SUBSCRIPTION_ID` / `SKIP_PREPROV_WHATIF`, then runs `azd up --no-prompt`. Closes KNOWN_ISSUES #1 and #3.
- **Symptom (historical):** Without pre-running `azd env new`, `azd up` created env `prototype` in a machine-default region and RG `rg-prototype`, ignoring `manifest.deployment.{environmentName, azureRegion, resourceGroup}`. Resources split across regions, names didn't match the spec.
- **Owner / target:** Done. Move to RESOLVED.md on the next housekeeping pass.

### 2. Silent AI Search regional-capacity fallback
- **Status:** ✅ Shipped 2026-07-14 — detection was already in preflight (`check_search_sku_capacity` using the SKU-availability API). Auto-swap now lives in [`accelerator/scripts/deploy.py`](scripts/deploy.py): on any `InsufficientResourcesAvailable` error targeting a Search service, the wrapper picks the nearest region from a built-in fallback table (`eastus2 → eastus → centralus → …`), calls `azd env set AZURE_SEARCH_LOCATION <region>`, and retries `azd up` once (max 2 swaps per invocation, controllable via `--max-search-swaps`).
- **Symptom (historical):** AI Search Basic SKU capacity-exhausted in the primary region. `azd provision` failed after ~14 min with `InsufficientResourcesAvailable`; the user had to fail once, recognize the pattern, and set `AZURE_SEARCH_LOCATION` manually.
- **Owner / target:** Done. Move to RESOLVED.md on the next housekeeping pass.

### 3. MCAPS Cosmos DB firewall blocks seed
- **Status:** Open — filed as [KNOWN_ISSUES.md #1](KNOWN_ISSUES.md); confirmed on ACCO and PDS Health
- **Symptom:** `postprovision` step 7 sets Cosmos `publicNetworkAccess: Enabled`, MCAPS `MCAPSGovDeployPolicies` silently flips it back to `Disabled` before step 8's `cosmos_seed.py` runs. Seed fails with `Forbidden` from the public-IP firewall check. Every MCAPS-based demo ships with empty SQL tables; agents run but `sql_tool` returns empty rows.
- **Fix proposal:** Optional Cosmos private endpoint + vnet-integrated Container Apps environment behind a spec flag (e.g. `deployment.private_network: true`). When enabled, `cosmos.bicep` provisions a PE in a delegated subnet, the CAE joins that subnet, and postprovision seeds via the private endpoint (bypassing the public-IP policy).
- **Scoping notes (2026-07-15 architecture discussion — not yet started):** This is a bigger change than "add a bicep module," because the flag doesn't just add infra — it removes the current seeding path.
  1. **Data-plane access is lost from the machine running `azd up`.** [`_seed_lib.py`](templates/prototype/db/_seed_lib.py) builds a `CosmosClient` directly with `AzureCliCredential`, called from `postprovision.sh` on whatever host ran `azd up` (this Copilot session's terminal today). A private-endpoint-only Cosmos account is unreachable from outside the VNet — the same class of failure as the current bug, just for a different reason. This also means any *future* ad-hoc Cosmos query/reseed from a Copilot session would stop working directly, not just the initial seed.
  2. **The only viable mitigation for MCAPS is exec-based seeding.** Since the Container App itself would be VNet-joined, seeding has to move to `az containerapp exec --command "python -m db.cosmos_seed"` — which proxies through the Container Apps control plane rather than needing direct network line-of-sight. This requires: (a) `db/` bundled into the deployed image (needs verifying against the current Dockerfile), (b) reordering the build graph so seed runs *after* `azd deploy` pushes the real image, not right after provision like today, and (c) reworking `verify-prototype.py`'s Cosmos-reachability checks the same way.
  3. **Recommendation:** keep this strictly opt-in (never default) given the debugging friction it adds, and don't ship the bicep half without the exec-based seeding rewrite — otherwise the flag trades "seed fails with a clear Forbidden error" for "seed silently can't run at all," which is worse.
- **Owner / target:** Unassigned / dedicated architecture session — this is a meaningful infra addition. Held off 2026-07-15 pending explicit go-ahead given the scoping notes above.

---

## Priority 2 — Pipeline discipline / correctness

### 4. Stale `_KNOWN_CONTAINERS` instruction in [backend-agent.md](../.github/specialists/backend-agent.md)
- **Status:** ✅ Shipped 2026-07-14 — removed the misleading "CRITICAL — update `_KNOWN_CONTAINERS`" section from [.github/specialists/backend-agent.md](../.github/specialists/backend-agent.md). Replaced with a one-liner explaining that `sql_tool.py` discovers containers at runtime via `list_containers()` — no per-prototype edit needed.
- **Symptom (historical):** [backend-agent.md](../.github/specialists/backend-agent.md) told the LLM to hand-edit a static template file that's on the "never modify during build" list, referencing a `_KNOWN_CONTAINERS` symbol that no longer exists in `sql_tool.py`.
- **Owner / target:** Done. Move to RESOLVED.md on the next housekeeping pass.

### 5. `spec.yaml` tool names don't match runtime tool names
- **Status:** ✅ Shipped (verified 2026-07-15) — option (b) is implemented: `check_tools_resolve()` in [`preflight.py`](generators/preflight.py) scans every emitted `generated/prototype/agents/specialists/*/agent.yaml`, loads the canonical name set from `tool_definitions.yaml`, and hard-fails preflight if any tool isn't recognized. Wired into `main()`'s Phase 2 gate list, so a bad translation from the agents-builder specialist is caught before `azd up` rather than surfacing as a late `register_agents.py` runtime error.
- **Symptom (historical):** `spec.yaml` uses friendly names (`[search_tool, sql_tool]`), but the runtime catalog in [`tool_definitions.yaml`](templates/prototype/agents/tools/tool_definitions.yaml) uses canonical names (`[run_sql_query, search_knowledge_base, call_mock_api]`). The [agents-builder specialist](../.github/specialists/agents-builder.md) hand-translates at emission time; a slipped name used to only surface as a late runtime error.
- **Owner / target:** Done. Move to RESOLVED.md on the next housekeeping pass. Option (a) — validating friendly-name → canonical mapping at spec time in `spec-validator.py` — remains a nice-to-have for fail-even-earlier feedback, but is no longer required to prevent bad deploys.

### 6. `preflight.py` soft-skips on expired `az` token
- **Status:** ✅ Shipped 2026-07-13 — [`check_az_logged_in`](generators/preflight.py) in phase-1 preflight runs `az account show` upfront and hard-fails on `AADSTS70043` / refresh-token-expired errors with the exact copy-pasteable `az login --scope https://management.core.windows.net//.default` command. The what-if soft-skip pattern still exists for other cases (RG not created yet, offline), but token expiry now surfaces at the earliest possible point.
- **Symptom (historical):** When the Azure CLI token had expired, `az deployment group what-if` inside preflight failed with `AADSTS70043: The refresh token has expired`. Preflight soft-skipped, then the actual `azd provision` died much later with less useful errors.
- **Owner / target:** Done. Move to RESOLVED.md on the next housekeeping pass.

---

## Priority 3 — Quality of life

### 7. Business-analyst runs a WCAG contrast check on `accent_color`
- **Status:** ✅ Shipped 2026-07-14 — [business-analyst.agent.md](../.github/agents/business-analyst.agent.md) Step 2 branding section now spells out the WCAG bars (`primary_color` ≥ 4.5:1 blocks deploy, `accent_color` < 3:1 warns), provides a quick lookup table of pre-vetted colors by bucket, and includes the sRGB luminance formula. The BA now nudges toward compliant colors up-front rather than surfacing the contrast warning at preflight time.
- **Symptom (historical):** PDS Health accent `#00A6B4` was 2.95:1 against white (below WCAG AA 3:1). Preflight warned but didn't block, and the BA hadn't checked at spec time.
- **Owner / target:** Done. Move to RESOLVED.md on the next housekeeping pass.

### 8. Build metrics distinguish idle vs active time
- **Status:** Partially shipped 2026-07-13 — phase-level split (generation vs provisioning vs verification) added via the new `deploy-start` event. Per-step idle-vs-active tracking still open.
- **Fix proposal:** [`accelerator/scripts/build-metrics.py`](scripts/build-metrics.py) tracks per-step start/end times and reports cumulative "active step time" separately from wall-clock. Useful for accelerator benchmarking; currently the wall-clock number is misleading.
- **Owner / target:** Low priority.

### 11. AI Search serialized behind the Foundry hub during provisioning
- **Status:** ✅ Shipped 2026-07-15 — Search extracted into its own [`search.bicep`](templates/prototype/infra/modules/search.bicep) module with no params/dependency on the Foundry hub. Previously `searchService` lived inside `foundry-iq.bicep`, whose module invocation in `main.bicep` took `hubAccountName: foundry.outputs.aiHubName`, forcing ARM to serialize the *entire* module — including the unrelated Search resource — behind Foundry account creation (which includes subdomain-reservation checks and is often the slowest single resource). Search has no actual ARM dependency on Foundry; the vector-index-to-embedding-model wiring happens later in Python (postprovision). `main.bicep` now declares `module search` alongside `module foundry`/`module openAi` with no cross-dependency, so Azure schedules them in parallel — worth roughly 2-5 min off provisioning time. `foundry-iq.bicep` is now model-deployments-only.
- **Owner / target:** Done.

---

## Priority 4 — Won't do: architectural non-goals

The two items below are the highest-leverage minute savings available on paper, but both require replacing the Copilot-driven build with a headless driver that calls an LLM API directly. **The accelerator is Copilot-centric by design** — the target audience has Copilot access but does not necessarily have their own Azure OpenAI quota, and paying to hit an AOAI endpoint just to accelerate a Copilot workflow inverts the value proposition. Recorded here so the trade-off is documented and doesn't get re-proposed.

If Copilot ever ships parallel-tool-call or overlapping-tool-call support in agent mode, both items become achievable inside the existing flow — revisit at that point.

### 9. Parallel LLM generation (steps 3, 4, 5 concurrent) — DECLINED
- **Status:** Won't do (as designed). Copilot's agent-mode execution loop processes tool calls serially in a chat session; there is no `asyncio.gather` equivalent inside Copilot. The only way to run steps 3, 4, 5 concurrently today is to call an LLM API directly from a Python driver — which requires an AOAI subscription users of this accelerator are not assumed to have.
- **Payoff if we ever did it:** Generation phase drops from 5-10 min → 1-2 min (bounded by the slowest single specialist).
- **Revisit if:** Copilot exposes a parallel-tool-call surface, OR the accelerator's target audience shifts to teams that always have AOAI access.

### 10. Overlap generation with provisioning — DECLINED
- **Status:** Won't do (as designed). Same root cause as #9 — the headless driver required to kick off `azd provision` in a background subprocess (via `asyncio.create_subprocess_exec`) while Copilot continues generating in the same "session" doesn't fit Copilot's agent model. A user can approximate the effect manually by opening a second terminal and running `azd provision` after step 2 completes, but that's an escape hatch, not an accelerator feature.
- **Payoff if we ever did it:** First build ≈ provisioning time alone (~15-25 min).
- **Revisit if:** Copilot lets agent code spawn and monitor long-running subprocesses concurrent with LLM turns.

---

## Bug forensics — issues seen in past runs but already fixed

For historical context, see [RESOLVED.md](RESOLVED.md). Notable recent fixes touching the same code paths as items above:

- **RESOLVED #3** — AI Search standard SKU capacity exhaustion in eastus2 (partial fix: added `AZURE_SEARCH_LOCATION` override; item **#2** above finishes the job by auto-detecting).
- **RESOLVED #18** — Soft-deleted Foundry account holds custom subdomain, blocks redeploy. Purge-on-teardown is now standard; still worth checking if #1 (deploy wrapper) needs to include `az cognitiveservices account purge` on redeploy paths.
- **RESOLVED #19** — Stale azd env silently reused. Preflight now catches this via `check_azd_env_matches_manifest()`; item **#1** above prevents the mismatch from happening at all.
- **RESOLVED #20** — `text-embedding-3-large` defined twice in [foundry-iq.bicep](templates/prototype/infra/modules/foundry-iq.bicep) (fixed 2026-07-12 during PDS Health build).

---

## Triage / review cadence

Review this backlog before each accelerator maintenance release. Move ✅ shipped items to [RESOLVED.md](RESOLVED.md), refresh priorities based on customer builds since last review.
