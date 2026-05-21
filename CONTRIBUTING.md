# Contributing

> [!WARNING]
> Never commit changes inside `generated/`. That folder is rewritten on every `@devlead build`. All source changes belong under `accelerator/`, `.github/`, or root-level files.

## Before you start

- This repo is a scaffold for Azure Solution Engineers. Changes should keep it easy for a non-developer SE to use.
- All infrastructure must use [Azure Verified Modules (AVM)](https://azure.github.io/Azure-Verified-Modules/) — no hand-rolled Bicep for resources that have AVM equivalents.
- No secrets, passwords, or connection strings in code. Managed identity only.

## Source vs generated

- `accelerator/` is the maintained source.
- `generated/` is the produced output of the build pipeline.
- Do not submit source changes by hand-editing files under `generated/`.
- If a change belongs in the produced app, update the accelerator-owned source in `accelerator/templates/prototype/`, `accelerator/generators/`, or the specialist prompts under `.github/`.

## What to contribute

Good areas:
- New Bicep modules for additional Azure services under `accelerator/templates/prototype/infra/modules/`
- Improvements to the specialist agent prompts in `.github/specialists/`
- Bug fixes in `accelerator/templates/prototype/backend/` (FastAPI app) and `accelerator/templates/prototype/agents/` (orchestrator, tools)
- Additional tooling in `accelerator/templates/prototype/agents/tools/`
- Improvements to `accelerator/generators/` or `accelerator/scripts/`

Out of scope:
- Customer-specific content (agents, data, docs) — these are produced per customer
- Changes to produced files under `generated/` — edit the scaffold source or specialist prompts instead

## Development setup

```bash
py -3 -m pip install -r accelerator/templates/prototype/backend/requirements.txt
copy accelerator\templates\prototype\.env.sample generated\prototype\.env
# Fill in .env with values from a provisioned dev resource group
```

Materialize the scaffold when needed:

```bash
py -3 accelerator/generators/materialize-prototype.py
```

Reset the produced prototype when switching to a new idea:

```bash
bash accelerator/scripts/reset-generated.sh
```

On Windows:

```powershell
pwsh -File accelerator/scripts/reset-generated.ps1
```

Run the produced app locally from `generated/prototype/`:

```bash
cd generated/prototype
py -3 -m uvicorn backend.main:app --host 0.0.0.0 --port 80 --reload
```

## Submitting changes

1. Fork the repo and create a feature branch.
2. Make your changes under `accelerator/` or `.github/` only.
3. Run the contract test suite — it must stay green:

   ```bash
   py -3 -m unittest discover -s accelerator/tests
   ```

4. Re-materialize and run preflight to confirm an end-to-end build is still valid:

   ```bash
   py -3 accelerator/generators/materialize-prototype.py
   py -3 accelerator/generators/preflight.py
   ```

5. If you changed scaffold Bicep: `az bicep build --file accelerator/templates/prototype/infra/main.bicep` must pass with no errors.
6. Open a pull request with a clear description of what changed and why. Reference any related entry in [accelerator/KNOWN_ISSUES.md](accelerator/KNOWN_ISSUES.md) and move resolved entries to [accelerator/RESOLVED.md](accelerator/RESOLVED.md) as part of the PR.

## Code conventions

- Python: follow existing style (no formatter enforced yet — match surrounding code)
- Bicep: AVM modules only, `br/public:avm/res/...`
- Commit messages: `fix:`, `feat:`, `docs:`, `refactor:` prefix preferred
- No `TODO` comments left in committed code
