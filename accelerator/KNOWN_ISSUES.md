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

**Symptom:** After `@devlead build` and running `cd generated/prototype; azd up
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
