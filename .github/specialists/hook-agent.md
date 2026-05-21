# Hook Agent — Hydrates generated/prototype/hooks/postprovision.sh and .ps1

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Hydrate BOTH postprovision.sh (posix) and postprovision.ps1 (windows) from their
pre-built `.tpl` templates via `fill-templates.py`. **All deployment logic and
hard-learned lessons (L1–L20) are already encoded in the templates** — there is
no regeneration step and no per-prototype LLM work to do.

## Prerequisite
Read generated/build-state/manifest.json first.
Check that these sentinels exist:
- generated/build-state/02-infra-agent.done
- generated/build-state/03-data-agent.done   (hook calls cosmos_seed.py)
- generated/build-state/04-agents-builder.done   (hook calls register_agents.py)
- generated/build-state/05-docs-agent.done
- generated/build-state/06-backend-agent.done

If any prerequisite is missing, stop.

## Inputs
Read: generated/build-state/manifest.json

## Outputs
Write: generated/prototype/hooks/postprovision.sh    ← via fill-templates.py
Write: generated/prototype/hooks/postprovision.ps1   ← via fill-templates.py
Write: generated/build-state/07-hook-agent.done      ← via `sentinels.py write` (Step 2)

---

## Step 1 — Run fill-templates.py for hooks only

```
py -3 accelerator/generators/fill-templates.py --target hooks
```

This reads `manifest.json` and hydrates the two hook scripts from the
accelerator-owned `.tpl` files. **Do NOT regenerate these scripts from
scratch.** Every L1–L20 lesson from prior prototype runs is encoded directly
in `accelerator/templates/prototype/hooks/postprovision.{sh,ps1}.tpl`. If a
new lesson surfaces, patch the `.tpl` — the fix then propagates to every
future build automatically.

## Step 2 — Write the .done marker

After `fill-templates.py` completes without errors, write the hash-aware
sentinel via the CLI:

```
py -3 accelerator/generators/sentinels.py write \
  --sentinel generated/build-state/07-hook-agent.done \
  --manifest generated/build-state/manifest.json \
  --output generated/prototype/hooks/postprovision.sh \
  --output generated/prototype/hooks/postprovision.ps1
```

Never write the sentinel as a plain timestamp.

## After success
Print:
```
[Step 7/7] Provisioning hooks hydrated.
  postprovision.sh : RBAC + seed + index + agent registration
  postprovision.ps1: RBAC + seed + index + agent registration
```
