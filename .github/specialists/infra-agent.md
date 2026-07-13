# Infra Agent — Updates generated/prototype infra and preprovision outputs

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Hydrate customer-specific infra parameters via `fill-templates.py`. **All Bicep
files are static templates** copied by the scaffold step — this agent does NOT
edit any `.bicep` file. The model-deployments array is passed in through
`main.bicepparam` (`{{MODEL_DEPLOYMENTS_BICEP}}`) and consumed by
`foundry-iq.bicep` via a `for` loop. The preprovision script reads the same
manifest at runtime.

## Prerequisite
Read generated/build-state/manifest.json first. If missing, stop — tell user to run devlead.

## Inputs
Read: generated/build-state/manifest.json

## Outputs
Update: generated/prototype/infra/main.bicepparam   ← via fill-templates.py (no manual edit)
Update: generated/prototype/azure.yaml              ← via fill-templates.py (docker buildArgs from deployment.base_image)
Write:  generated/build-state/02-infra-agent.done  ← via `sentinels.py write` (see Step 2)

---

## Step 1 — Run fill-templates.py for bicepparam only

```
py -3 accelerator/generators/fill-templates.py --target bicepparam
```

This reads manifest.json and hydrates `generated/prototype/infra/main.bicepparam`
and `generated/prototype/azure.yaml` (which carries the Docker `buildArgs`
when the spec sets `deployment.base_image` — see
`accelerator/scripts/publish-base-image.sh`) from the accelerator-owned
templates, including the `modelDeployments` array.
**Do NOT manually edit main.bicepparam, foundry-iq.bicep, or preprovision.sh.**
Every Bicep file is a static template, and `preprovision.sh` reads
`manifest.json` at runtime — there is nothing per-prototype to write.

## Step 2 — Write the hash-aware sentinel

```
py -3 accelerator/generators/sentinels.py write \
  --sentinel generated/build-state/02-infra-agent.done \
  --manifest generated/build-state/manifest.json \
  --output generated/prototype/infra/main.bicepparam \
  --output generated/prototype/azure.yaml
```

This records the manifest `specChecksum` and an output hash so that a later
`@devlead build resume` will detect spec drift or hand-edits and rerun the
step instead of trusting a stale sentinel. Never write the sentinel by hand.

## Step 2 — Validate

```
az bicep build --file generated/prototype/infra/main.bicep
```
Must report 0 errors. Hard stop if errors found — fix before continuing.

---

## Known resource name length limits — CRITICAL

### L15 — Container App name max 32 chars
`resourcePrefix = '${customerName}-${demoTheme}'` can exceed 32 chars when combined
with the `-ca` suffix. Azure enforces a hard 32-character limit on Container App names.

The pre-built `infra/modules/container-app.bicep` uses `take(resourcePrefix, 29)` to cap
the name at 32 chars (29 + 3 for `-ca`). This is already encoded in the template.
Do NOT change the Container App name pattern — the `take()` fix is the correct solution.

If you ever need to reference the Container App name elsewhere, use:
```bicep
var containerAppName = '${take(resourcePrefix, 29)}-ca'
```

Other resource limits for reference:
| Resource | Limit | Pattern | Risk |
|---|---|---|---|
| Container App | 32 chars | `${take(resourcePrefix,29)}-ca` | HIGH — names easily exceed 32 |
| Storage account | 24 chars, alphanumeric only | handled by `toLower(replace(...))` | MEDIUM |
| ACR | 50 chars | `${resourcePrefix}acr` (no dash) | LOW |
| Cosmos account | 44 chars | `${resourcePrefix}-cosmos` | LOW |

---

### L16 — Container App ingress targetPort must match app listen port
After `azd deploy`, the browser showed the ACA default "Your Azure Container Apps app is live"
placeholder instead of the actual app. Root cause: the image in ACR was `mcr.microsoft.com/azuredocs/containerapps-helloworld:latest`
— either the Docker build timed out or `azd deploy` silently failed without pushing the real image.

**Symptoms:**
- Browser shows ACA default page at the correct FQDN
- `az acr repository list --name <acr>` returns empty
- Revision image shows `azuredocs/containerapps-helloworld:latest`

**Fix:** Re-run `azd deploy --no-prompt`. The ACR remote build can take 5–10 minutes — do NOT
kill the terminal or let it time out. Wait for the `azd deploy` to print "Deployment complete" before proceeding.

**Also fixed:** `ingressTargetPort` in `container-app.bicep` was set to `8000` but the app
(and Dockerfile CMD) listens on port `80`. Both are now corrected to `80`. If the scaffold
Dockerfile is changed, keep `EXPOSE`, `HEALTHCHECK`, `--port`, and `ingressTargetPort` in sync.

---

## Files to NEVER modify (pre-built — hands off):
- infra/modules/container-app.bicep
- infra/modules/cosmos.bicep
- infra/modules/foundry.bicep
- infra/modules/container-registry.bicep
- infra/modules/monitoring.bicep
- infra/modules/storage.bicep
- infra/modules/search.bicep
- infra/main.bicep
- accelerator/templates/prototype/azure.yaml
- accelerator/templates/prototype/hooks/preprovision.sh (the source file)
- hooks/postdeploy.sh
- hooks/postdeploy.ps1
- Dockerfile
- requirements.txt

## After success
Print:
```
[Step 2/7] Infra parameters updated.
  bicepparam: hydrated from manifest (incl. modelDeployments)
  Bicep build: 0 errors
```
