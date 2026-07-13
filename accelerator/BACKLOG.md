# Accelerator Backlog

Long-term fixes and improvements to the accelerator itself, surfaced during real customer builds. Deployment-blocking bugs still live in [KNOWN_ISSUES.md](KNOWN_ISSUES.md); this file is the broader triage list including workflow, UX, and pipeline discipline items.

Update the **Status** column when work starts / ships. Move ✅ shipped items to [RESOLVED.md](RESOLVED.md).

---

## Priority 1 — Every fresh customer hits these

### 1. `azd up` invents env / region / RG instead of reading `manifest.json`
- **Status:** Open — filed as [KNOWN_ISSUES.md #2](KNOWN_ISSUES.md) on 2026-07-12
- **Symptom:** Without pre-running `azd env new`, `azd up` creates env `prototype` in a machine-default region and RG `rg-prototype`, ignoring `manifest.deployment.{environmentName, azureRegion, resourceGroup}`. Resources split across regions, names don't match the spec.
- **Repro:** Hit twice this session (ACCO, PDS Health). PDS Health's first deploy landed 8 resources in the wrong RG (`rg-prototype` / `southcentralus`) before failing on Cosmos zone-redundancy in that region.
- **Fix proposal:** New `accelerator/scripts/deploy.py` wrapper that reads `manifest.json`, runs `azd env new <name> --location <region> --subscription <sub>` (skip if exists), `azd env set AZURE_RESOURCE_GROUP <rg>`, then `azd up`. Wire into [`.github/agents/devlead.agent.md`](../.github/agents/devlead.agent.md) Step 9 so devlead always calls the wrapper.
- **Owner / target:** Unassigned / next accelerator maintenance release.

### 2. Silent AI Search regional-capacity fallback
- **Status:** Open — recurring per [RESOLVED.md #3](RESOLVED.md); hit again this session on PDS Health
- **Symptom:** AI Search Basic SKU is capacity-exhausted in the primary region (`eastus2` for PDS Health, previously `eastus2` for ACCO). `azd provision` fails after ~14 min with `InsufficientResourcesAvailable`. User has to fail once, recognize the pattern, and set `AZURE_SEARCH_LOCATION` manually.
- **Fix proposal:** [`accelerator/generators/preflight.py`](generators/preflight.py) probes AI Search SKU availability in `AZURE_LOCATION` (via `az search service create --dry-run` or the SKU-availability API) and, on detected capacity issues, sets `AZURE_SEARCH_LOCATION` to the next-nearest region automatically (with a printed note). The nearest-region lookup table already exists in [`.github/agents/business-analyst.agent.md`](../.github/agents/business-analyst.agent.md#step-26) — reuse it.
- **Owner / target:** Unassigned / next accelerator maintenance release.

### 3. MCAPS Cosmos DB firewall blocks seed
- **Status:** Open — filed as [KNOWN_ISSUES.md #1](KNOWN_ISSUES.md); confirmed on ACCO and PDS Health
- **Symptom:** `postprovision` step 7 sets Cosmos `publicNetworkAccess: Enabled`, MCAPS `MCAPSGovDeployPolicies` silently flips it back to `Disabled` before step 8's `cosmos_seed.py` runs. Seed fails with `Forbidden` from the public-IP firewall check. Every MCAPS-based demo ships with empty SQL tables; agents run but `sql_tool` returns empty rows.
- **Fix proposal:** Optional Cosmos private endpoint + vnet-integrated Container Apps environment behind a spec flag (e.g. `deployment.private_network: true`). When enabled, `cosmos.bicep` provisions a PE in a delegated subnet, the CAE joins that subnet, and postprovision seeds via the private endpoint (bypassing the public-IP policy).
- **Owner / target:** Unassigned / dedicated architecture session — this is a meaningful infra addition.

---

## Priority 2 — Pipeline discipline / correctness

### 4. Stale `_KNOWN_CONTAINERS` instruction in [backend-agent.md](../.github/specialists/backend-agent.md)
- **Status:** Open — surfaced 2026-07-12 (PDS Health build)
- **Symptom:** [backend-agent.md](../.github/specialists/backend-agent.md) has a "**CRITICAL** — you MUST update `sql_tool.py`'s `_KNOWN_CONTAINERS`" instruction, but the actual [`sql_tool.py`](templates/prototype/agents/tools/sql_tool.py) template already discovers containers at runtime via `list_containers()`. A less-cautious builder would follow the doc and hand-patch a static template file (which is on the "never modify during build" list).
- **Fix proposal:** Delete the `_KNOWN_CONTAINERS` section from [`.github/specialists/backend-agent.md`](../.github/specialists/backend-agent.md). Add a one-liner: "sql_tool.py discovers containers from the live Cosmos DB — no per-prototype edit needed."
- **Owner / target:** Trivial — can ship immediately.

### 5. `spec.yaml` tool names don't match runtime tool names
- **Status:** Open
- **Symptom:** `spec.yaml` uses friendly names (`[search_tool, sql_tool]`), but the runtime catalog in [`tool_definitions.yaml`](templates/prototype/agents/tools/tool_definitions.yaml) uses canonical names (`[run_sql_query, search_knowledge_base, call_mock_api]`). The [agents-builder specialist](../.github/specialists/agents-builder.md) hand-translates at emission time. If an LLM slips through a wrong name in the emitted `agent.yaml`, [`register_agents.py`](templates/prototype/agents/register_agents.py) fails at runtime with a helpful but late error, and no earlier gate catches it.
- **Fix proposal:** Two options — pick one.
  - **(a)** [`spec-validator.py`](generators/spec-validator.py) maps spec-level tool names to runtime names and rejects unknowns at spec time; manifest carries canonical names.
  - **(b)** [`preflight.py`](generators/preflight.py) scans `generated/prototype/agents/specialists/*/agent.yaml` and rejects unknown tool names before deploy.
- **Owner / target:** Unassigned. Option (a) is cleaner (fail early); option (b) is a smaller change.

### 6. `preflight.py` soft-skips on expired `az` token
- **Status:** Open — surfaced 2026-07-12 (PDS Health build)
- **Symptom:** When the Azure CLI token has expired, `az deployment group what-if` inside preflight fails with `AADSTS70043: The refresh token has expired`. Preflight currently soft-skips this class of failure (to avoid blocking spec authoring), then the actual `azd provision` dies much later with less useful errors.
- **Fix proposal:** In [`preflight.py`](generators/preflight.py), detect `AADSTS70043` / `az login` / `no subscription` in stderr and fail hard with a copy-pasteable remediation: `Run: az login --tenant <detected-tenant> --scope https://management.core.windows.net//.default`.
- **Owner / target:** Small change — can ship with #4.

---

## Priority 3 — Quality of life

### 7. Business-analyst runs a WCAG contrast check on `accent_color`
- **Status:** Open — cosmetic; PDS Health accent `#00A6B4` was 2.95:1 against white (below WCAG AA 3:1). Preflight warned but didn't block.
- **Fix proposal:** In [`.github/agents/business-analyst.agent.md`](../.github/agents/business-analyst.agent.md) Step 2 (Branding), compute contrast on the accent color against white before offering it, and either reject or nudge to a nearby compliant variant.
- **Owner / target:** Low priority; nice-to-have.

### 8. Build metrics distinguish idle vs active time
- **Status:** Partially shipped 2026-07-13 — phase-level split (generation vs provisioning vs verification) added via the new `deploy-start` event. Per-step idle-vs-active tracking still open.
- **Fix proposal:** [`accelerator/scripts/build-metrics.py`](scripts/build-metrics.py) tracks per-step start/end times and reports cumulative "active step time" separately from wall-clock. Useful for accelerator benchmarking; currently the wall-clock number is misleading.
- **Owner / target:** Low priority.

---

## Priority 4 — Orchestration wins (require headless `build.py` driver)

The accelerator today runs the build through GitHub Copilot in agent mode. Copilot executes steps 3, 4, 5 (data / agents / docs specialists) serially even though the dependency graph marks them parallel-ready, and the deploy phase always waits for all generation to finish. Both constraints disappear in a headless Python driver that calls the model APIs directly. The two items below are the highest-leverage minutes savings available, but neither can ship until the driver exists.

### 9. Parallel LLM generation (steps 3, 4, 5 concurrent)
- **Status:** Open — blocked on headless `build.py` driver
- **Symptom:** Steps 3 (data-agent → `cosmos_seed.py`), 4 (agents-builder → agent yaml + skills), 5 (docs-agent → knowledge markdown) are declared parallel-ready in the [build graph](../.github/agents/devlead.agent.md) but Copilot's agent mode drives them serially. Total generation time ≈ 5-10 min; if run truly concurrent it drops to the slowest single step (~1-2 min).
- **Design:**
  1. New `accelerator/scripts/build.py` — a headless driver that reads `manifest.json`, dispatches specialist LLM calls via the Foundry-deployed gpt-4o (or an alternate Azure OpenAI endpoint set by env var), and manages sentinels the same way today's Copilot flow does.
  2. Steps 3, 4, 5 launched via `asyncio.gather(run_step_3(...), run_step_4(...), run_step_5(...))`. Each step reads its specialist markdown (`.github/specialists/<name>.md`) as system prompt and the manifest as user context; uses function-calling for file writes so the driver stays in control of what lands on disk.
  3. Each step still writes its own hash-aware sentinel via `sentinels.py write` on success. Same failure semantics as today (any step non-zero → whole build fails, remaining tasks cancelled).
  4. Devlead agent.md gets an alternate invocation path: `@devlead build --headless` (or a config flag) that shells out to `build.py` instead of driving specialists inline.
- **Payoff:** Generation phase drops from 5-10 min → 1-2 min (bounded by the slowest single specialist).
- **Owner / target:** Blocked on driver scaffold; land the scaffold first, then this is a small delta.

### 10. Overlap generation with provisioning
- **Status:** Open — blocked on headless `build.py` driver
- **Symptom:** `azd provision` today waits for all seven generation steps to finish before starting. But `azd provision` reads only step 2's outputs (`main.bicepparam`) — steps 3-5's outputs are consumed by *postprovision* hooks (seed, index, register). Provisioning could start immediately after step 2 completes, in parallel with 3-5.
- **Design:**
  1. In the headless driver (see #9), after step 2 sentinel is written and preflight-what-if passes, kick off `azd provision` as a background subprocess (`asyncio.create_subprocess_exec`).
  2. Concurrently run steps 3, 4, 5 (parallel per #9), then step 7 (hooks).
  3. `await` provision + generation before `azd deploy`. Provisioning is typically 15-25 min; generation is 1-2 min under #9 — so generation completes entirely inside the provisioning window, contributing zero to end-to-end time.
  4. Risk: preflight's `az deployment group what-if` step needs to run *before* kicking off provision, so preflight becomes part of the pre-provision gate rather than a separate step.
- **Payoff:** First build ≈ provisioning time alone (15-25 min becomes the floor).
- **Owner / target:** Blocked on #9 driver scaffold.

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
