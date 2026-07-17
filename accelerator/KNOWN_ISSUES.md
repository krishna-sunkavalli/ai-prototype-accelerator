# Known Issues

> [!NOTE]
> Resolved issues live in [RESOLVED.md](RESOLVED.md). File a new entry here only when a deployment-blocking issue is still open and has no template-level fix yet.

Open issues only. Resolved issues are archived in [RESOLVED.md](RESOLVED.md).

## How to use this file

- File a new entry here only when a deployment-blocking issue is still open
  (no template fix yet).
- Once a template-level fix is applied and validated by a clean `@devlead build`,
  move the entry to `RESOLVED.md`.

## Entry template

```md
## N. Short title

**Symptom:** What the user sees (error message, broken behavior).

**Root cause:** Where in the pipeline the bug lives.

**Workaround:** Manual steps to unblock today, if any.

**Planned fix:** What template change will close it. Owner / target build.
```

## Currently open

## 1. `azd up` env auto-defaults override `manifest.deployment.*`

**Status:** ✅ Fixed 2026-07-14 — devlead no longer calls `azd up` directly. It runs [`accelerator/scripts/deploy.py`](../accelerator/scripts/deploy.py), which reads the manifest, creates or selects the target azd env, force-sets `AZURE_LOCATION` / `AZURE_RESOURCE_GROUP` / `AZURE_SUBSCRIPTION_ID` / `SKIP_PREPROV_WHATIF` before invoking `azd up --no-prompt`, and additionally auto-swaps `AZURE_SEARCH_LOCATION` on Basic-SKU capacity failures (see BACKLOG #2). The env-drift class of failures is gone; every build deploys to the region and RG the spec asks for.

**Symptom (historical):** After `@devlead build` and running `cd generated/prototype; azd up
--no-prompt`, azd creates a fresh environment named `prototype` in whatever
region it defaults to (observed: `southcentralus`), and derives
`AZURE_RESOURCE_GROUP=rg-prototype`. This ignores the manifest's
`deployment.environment_name` (e.g. `pds-health-prototype`),
`deployment.azure_region` (e.g. `eastus2`), and `deployment.resource_group`
(e.g. `rg-pds-health-prototype`). Foundry hub lands in `eastus2` (the module
default in `foundry.bicep`), so resources split across two regions and the
resource group name is wrong. Deployment either fails on regional capacity
mismatches or succeeds with misnamed resources that are hard to correlate to
the spec.

**Root cause:** `azd up` runs before any hook fires. `azure.yaml` declares no
`metadata.name`/env defaults, so `azd init` (triggered implicitly on first
`azd up`) picks the working directory basename (`prototype`) and prompts for a
region — under `--no-prompt` that prompt collapses to a machine default. The
preprovision hook then correctly reads `AZURE_LOCATION` and `AZURE_RESOURCE_GROUP`
from the azd env — which are already the wrong values.

**Workaround:** Before `azd up`, run in `generated/prototype/`:

```powershell
$env_name = (Get-Content ../../generated/build-state/manifest.json | ConvertFrom-Json).deployment.environmentName
$region   = (Get-Content ../../generated/build-state/manifest.json | ConvertFrom-Json).deployment.location
$rg       = (Get-Content ../../generated/build-state/manifest.json | ConvertFrom-Json).deployment.resourceGroup
if (-not (Test-Path ".azure/$env_name")) { azd env new $env_name --location $region --subscription <subid> }
azd env select $env_name
azd env set AZURE_LOCATION $region
azd env set AZURE_RESOURCE_GROUP $rg
azd up --no-prompt
```

**Planned fix:** Add a small `accelerator/scripts/deploy.py` wrapper that reads
`manifest.json`, runs the `azd env new`/`azd env set` sequence, then invokes
`azd up`. Wire it into `.github/agents/devlead.agent.md` Step 9 so devlead
always calls the wrapper instead of raw `azd up`. Owner: next accelerator
maintenance release.

**Reproduced:** Hudson Advisors build (2026-07-13). Spec said
`eastus2`/`rg-hudson-advisors-prototype`; the stale `prototype` env from a
prior PDS Health build deployed to `southcentralus`/`rg-prototype` and got
about 8 resources in before Cosmos + Search failed on regional capacity. See
issue #3 for the "preflight doesn't detect the mismatch" angle and issue #4
for the region-capacity failure pattern that stranded partial resources.

## 2. Cosmos DB seeding (and now Foundry IQ knowledge wiring) blocked by MCAPS governance policy

**Symptom:** During postprovision, step 8 (`cosmos_seed.py`) fails with
`(Forbidden) Request originated from IP <ip> through public internet. This is
blocked by your Cosmos DB account firewall settings.` The failure repeats even
after `az cosmosdb update -p Enabled --ip-range-filter <ip>` — the API silently
resets `publicNetworkAccess` to `Disabled`. Running the seed from inside the
deployed Container App via `az containerapp exec` fails the same way.

**Updated 2026-07-16 (Alliant build):** The same governance pattern also hits
the Storage account, breaking step 10 (Foundry IQ knowledge source/knowledge
base wiring). Symptom: `az storage container list --account-name <acct>
--auth-mode login` (or any data-plane blob call, including the Search
service's system-assigned identity reading the `prototype-data` container)
fails with `The request may be blocked by network rules of storage account.`
`az storage account show -n <acct> --query publicNetworkAccess` returns
`Disabled` with `networkRuleSet.defaultAction: Deny`, even though
`bypass: AzureServices` is set — that bypass alone is not sufficient for
Search's managed-identity blob reads. The earlier `az storage blob
upload-batch --auth-mode login` call in step 9 of the *same* postprovision
run still succeeds, which points to a timing race: bicep/the deployer's own
upload happens before the `StorageAccount_PublicNetwork_Modify` policy
remediation re-flips `publicNetworkAccess` back to `Disabled`, and by the
time step 10 runs a few minutes later the account is locked down again.
Confirmed via `az policy state list --resource <storage-account-id>`, which
shows `StorageAccount_PublicNetwork_Modify` (and
`StorageAccount_DisableLocalAuth_Modify`,
`StorageAccount_BlobAnonymousAccess_Modify`) with effect `modify`.

**Root cause:** Microsoft-CAPS subscriptions carry an assignment named
`MCAPSGovDeployPolicies` with `Modify` effects
(`CosmosDB_PublicNetwork_Modify`, `CosmosDB_LocalAuth_Modify`,
`StorageAccount_PublicNetwork_Modify`, `StorageAccount_DisableLocalAuth_Modify`,
`StorageAccount_BlobAnonymousAccess_Modify`) that continuously force
`publicNetworkAccess: Disabled` / `disableLocalAuth: true` on every Cosmos
account **and** Storage account, regardless of what the bicep template
declares. Container Apps and Azure AI Search traffic are still classified as
"public internet" by these resources because neither the CA environment nor
Search has vnet integration, so the traffic never gains the trusted-service
exception in practice.

**Workaround:** None automated. To seed the demo data and populate Foundry IQ
you must either (a) request an exemption to `MCAPSGovDeployPolicies` for the
RG so `publicNetworkAccess: Enabled` sticks on both Cosmos and Storage, then
re-run `azd provision --no-prompt` from a machine whose outbound IP you can
pin, or (b) run the accelerator on a non-MCAPS subscription. The prototype is
otherwise fully deployed: Foundry agents register, Container App and
health/config/websocket smoke tests all pass, operational docs upload to
Blob successfully (the step-9 race window). Only the domain SQL rows are
missing (`sql_tool` queries return empty) and the Foundry IQ knowledge
source/base are not wired (`search_knowledge_base` has nothing indexed).

**Confirmed working (Alliant build, 2026-07-16):** Option (a) works end to
end when you have `Owner`/`Resource Policy Contributor` on the subscription.
Steps that closed the loop:
1. Find the assignment: `az policy state list --resource <res-id> --query
   "[?policyDefinitionName=='StorageAccount_PublicNetwork_Modify'].{a:policyAssignmentId}"`
   (repeat for the Cosmos account) — on MCAPS this resolves to
   `MCAPSGovDeployPolicies` assigned at the Tenant Root Group, not the
   subscription, so `az policy assignment list` alone won't show it.
2. Get the initiative's reference IDs: `az policy set-definition show
   --name MCAPSGovDeployPolicies --management-group <root-mg-id> --query
   "policyDefinitions[].policyDefinitionReferenceId"` — the ones needed are
   `StorageAccountPublicNetworkModify`, `StorageAccountDisableLocalAuth`,
   `ModifyAllowBlobAnonymousAccess`, `CosmosDBPublicNetworkModify`,
   `ModifyCosmosDBLocalAuth`.
3. Create the exemption: `az policy exemption create -n <name> --scope
   /subscriptions/<subId> --policy-assignment <assignmentId>
   --exemption-category Waiver --policy-definition-reference-ids <the 5
   above> --expires-on <date>` (an expiry keeps this from becoming permanent
   drift).
4. Immediately re-enable public access — the exemption stops the *policy*
   from re-locking it, but you still have to flip the setting yourself once:
   `az cosmosdb update --name <acct> -g <rg> --public-network-access
   Enabled` and `az storage account update --name <acct> -g <rg>
   --public-network-access Enabled --default-action Allow`.
5. Re-run `py accelerator/scripts/deploy.py` (or `azd provision`) — the
   postprovision hook now seeds Cosmos and wires Foundry IQ successfully.
   `verify-prototype.py` returns a plain `PASS`, not `DEGRADED-PASS`.

Watch for policy remediation racing your manual `az ... update` — if the
exemption doesn't cover every reference ID the specific resource is gated
by, the setting can flip back to `Disabled` within minutes.

**Planned fix:** Add optional Cosmos + Storage private endpoints and a
vnet-integrated Container Apps environment / Search service behind a spec
flag (e.g. `deployment.private_network: true`). When enabled, cosmos.bicep
and storage.bicep provision PEs in a delegated subnet, the CAE and Search
join or peer with that subnet, and postprovision seeds/wires data via the
private endpoint instead of the public data plane. Owner: next accelerator
maintenance release.

## 3. Preflight does not detect azd env / manifest drift

**Status:** ✅ Fixed 2026-07-14 — two-layer fix. Detection lives in
`check_azd_env_matches_manifest` (preflight, phase 1) which hard-fails on
env name, `AZURE_LOCATION`, or `AZURE_RESOURCE_GROUP` mismatch. Auto-repair
lives in [`accelerator/scripts/deploy.py`](../accelerator/scripts/deploy.py) which
force-sets those values from the manifest before `azd up` runs, so the
drift can't recur even if the user pokes at `azd env set` between builds.
Detection + repair together close issue #1 as well.

**Symptom:** `preflight.py` reports `OK — generated prototype is ready to
deploy`, but `azd up` immediately provisions to a different region and RG
than `spec.yaml` specifies. On the Hudson Advisors build the spec asked for
`eastus2` / `rg-hudson-advisors-prototype`; the deploy went to
`southcentralus` / `rg-prototype` because a stale `prototype` azd env from a
prior build was still selected. The user only finds out ~15 minutes into
provisioning when the wrong region hits a capacity error and the ARM
deployment fails partway through.

**Root cause:** `preflight.py` validates artifacts (paths, Python compile,
YAML/JSON parse, unresolved placeholders, tool resolution, `az bicep build`,
`az deployment group what-if`, manifest schema, Foundry subdomain
availability). It never opens `generated/prototype/.azure/<env>/.env` to
compare `AZURE_LOCATION` / `AZURE_RESOURCE_GROUP` / env name against
`manifest.deployment.{location,resourceGroup,environmentName}`. The `what-if`
check runs against the manifest RG, so it soft-skips with
`ResourceGroupNotFound` even when the RG in the active azd env is different.
The mismatch is only surfaced by the actual `azd up`, after Bicep starts
allocating resources in the wrong region.

**Workaround:** Before running `azd up`, run inside `generated/prototype/`:

```powershell
$manifest = Get-Content ../../generated/build-state/manifest.json | ConvertFrom-Json
$envName  = $manifest.deployment.environmentName
$region   = $manifest.deployment.location
$rg       = $manifest.deployment.resourceGroup
$active   = azd env get-values | Select-String -Pattern '^AZURE_ENV_NAME=|^AZURE_LOCATION=|^AZURE_RESOURCE_GROUP='
$active
Write-Host "Expected: $envName / $region / $rg"
# If mismatched:
#   azd env new $envName --location $region --subscription <subId>
#   azd env select $envName
#   azd env set AZURE_LOCATION $region
#   azd env set AZURE_RESOURCE_GROUP $rg
```

**Planned fix:** Two-part change.

1. Add a `check_azd_env_matches_manifest()` step to `preflight.py` that
   reads `generated/prototype/.azure/<active-env>/.env` and hard-fails if
   `AZURE_ENV_NAME`, `AZURE_LOCATION`, or `AZURE_RESOURCE_GROUP` don't match
   the manifest. Soft-skip only when no azd env exists yet.
2. Have the planned `accelerator/scripts/deploy.py` wrapper (see issue #1)
   re-run the env alignment step every time before `azd up`, so a drifted
   env is corrected automatically rather than only detected.

Owner: next accelerator maintenance release, paired with the issue #1 fix.

## 4. Partial ARM deploys strand resources when a region hits capacity mid-provision

**Status:** Substantially fixed 2026-07-13 — the recovery + prevention
loop is now complete:

- `preflight_env.py` runs six probes before any azd work:
  `check_provider_registered`, `check_model_quota`,
  `check_search_sku_capacity` (SKU-availability probe, not just quota
  headroom), `check_cosmos_regional_capacity`,
  `check_foundry_subdomain_available` (soft-deleted account check), and
  the new `check_global_name_collisions` (Resource Graph query for live
  cross-RG conflicts on Storage / ACR / Cosmos / Foundry subdomain).
- [`recover-bicep-outputs.py`](../accelerator/scripts/recover-bicep-outputs.py) reads
  the latest ARM deployment's outputs and injects them into the azd env
  when Bicep exited non-zero — unblocks `azd deploy` after a "1 resource
  failed but 10 succeeded" outcome (Hudson Advisors 2026-07-13).
- [`cleanup-partial-deploy.py`](../accelerator/scripts/cleanup-partial-deploy.py)
  scans for stranded resource groups (by slug match + global-name
  collision) and prints or deletes them.
- Also fixed at the template level: [`foundry-iq.bicep`](templates/prototype/infra/modules/foundry-iq.bicep)
  restored `dependsOn: llmDeployments` on the embedding deployment so the
  Foundry parent-resource race that caused every late model deployment
  to fail is gone; [`Dockerfile`](templates/prototype/Dockerfile) simplified
  the `FROM` line so ACR remote-build's dependency scanner doesn't
  reject the shell-style `${VAR:-default}` syntax.

Transient regional capacity shortages that don't show up in either the
quota API or the SKU-availability probe (very short-lived Azure-side
capacity crunches) still slip through — the recovery scripts unwind them
cleanly.

**Symptom:** During `azd up`, ARM starts provisioning ~10 resources in
parallel. Some succeed (Foundry hub, ACR, Log Analytics, App Insights,
Storage, first model deployments) before others fail on regional capacity:

```
(x) Failed: Search service — InsufficientResourcesAvailable
(x) Failed: Azure Cosmos DB — ServiceUnavailable
    (high demand for zonal redundant accounts in South Central US)
(x) Failed: text-embedding-3-large — RequestConflict on Foundry parent
```

`azd up` exits non-zero, but the successfully provisioned resources are
left behind in the RG. On the next `@devlead build` the accelerator has no
knowledge of them: they don't appear in any sentinel, they don't show up in
manifest state, and they aren't cleaned up on retry. The user is now paying
for ~$40/mo of orphaned Log Analytics + App Insights + Foundry hub every
time this happens.

**Root cause:** Two independent things converge.

1. Bicep pins some AI-adjacent resources (Search, embeddings) to specific
   regions inside the module, so even when the caller asks for `eastus2`
   the AI Search module resolves elsewhere and can hit a different quota
   surface than the rest of the deployment.
2. Neither preflight nor devlead checks for a prior partial-deploy state
   in the target RG. There is no "resume the aborted provision" or "roll
   back the aborted provision" path. `azd up` retry recomputes from
   scratch and re-hits any name conflicts + capacity issues.

**Workaround:** For any transient capacity failure still not caught by
preflight, run `recover-bicep-outputs.py` to unblock `azd deploy`, then
`cleanup-partial-deploy.py --yes` to clear stranded resources before
retrying.

**Planned fix:** Three template-level changes.

1. **Preflight — regional capacity probe.** Before `azd up`, call
   `az quota list` (Cosmos DB free-tier + regular, Search basic SKU) and
   `az cognitiveservices usage list` for the manifest region and hard-fail
   if headroom is insufficient. Today preflight only checks *model* quota
   in preprovision.{sh,ps1} — extend it to Cosmos and Search too.
2. **Bicep — single-region deployment.** Audit each `modules/*.bicep` for
   hard-coded `location` values; force everything to `${resourceGroup().location}`
   unless the spec explicitly allows a multi-region AI split via a new
   flag.
3. **Cleanup helper.** Add `accelerator/scripts/cleanup-partial-deploy.py`
   that lists resources in the target RG whose tags include
   `managedBy=azd` but which aren't in the latest successful sentinel set,
   and prints a `--dry-run` deletion plan. Wire it into
   `@devlead build resume` so the user is told about orphaned resources
   before another provision starts.

Owner: next accelerator maintenance release.


