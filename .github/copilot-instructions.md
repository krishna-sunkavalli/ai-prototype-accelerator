# GitHub Copilot Instructions — ai-prototype-accelerator

Enterprise-grade Azure AI prototype accelerator. One `spec.yaml` in, one fully provisioned, validated, deployable prototype out under `generated/prototype/`.

This file is the entry point. Build intelligence lives in the agents and specialists referenced below; do not duplicate it here.

---

## Architecture source of truth (read before assumptions)

- Canonical reference: [.github/architecture-reference.md](.github/architecture-reference.md)
- Generated/non-canonical reference: [.github/architecture.md](.github/architecture.md) (can be overwritten on build)
- If details are unclear during execution, check sources in this order:
  1. `.github/architecture-reference.md`
  2. `generated/build-state/manifest.json`
  3. Generated artifacts under `generated/prototype/`

Do not assume services, files, or behavior that are not supported by these sources.

---

## Repository model

- `accelerator/` — maintained source owned by the accelerator team.
  - `accelerator/templates/prototype/**` — static templates + `.tpl` files (single source of truth for the emitted app).
  - `accelerator/generators/**` — deterministic Python pipeline (validate, hydrate, sentinel, preflight).
  - `accelerator/scripts/**` — reset and sentinel-management utilities.
  - `accelerator/tests/**` — contract tests for the build pipeline.
- `generated/build-state/` — manifest + hash-aware build sentinels (gitignored).
- `generated/prototype/` — emitted application, deployment root for `azd up`.
- Never treat files under `generated/` as long-term source-of-truth edits. They are rewritten on every `@devlead build`.

---

## Three-layer architecture

Every artifact under `generated/prototype/` falls into exactly one of three layers. The layer determines who is allowed to author/modify it.

| Layer | Source | Who writes it | Notes |
|---|---|---|---|
| Static template | `accelerator/templates/prototype/**` (no `.tpl`) | Accelerator maintainers only | Copied verbatim by `materialize-prototype.py`. Bugs are fixed here so the fix propagates to every future build. |
| `.tpl` + placeholders | `accelerator/templates/prototype/**/*.tpl` | Accelerator maintainers (template) + `fill-templates.py` (hydration) | Placeholders are `{{UPPER_SNAKE_CASE}}`. Unresolved tokens are a hard error. |
| LLM-generated | `.github/specialists/*.md` | Devlead's specialists during a build | Only genuinely creative content (agent personas, seed data, knowledge docs). Plumbing belongs in the layers above. |

---

## Workflow

### Step 1 — Generate spec.yaml (if you don't have one yet)

```text
@business-analyst Contoso https://contoso.com
```

The [business-analyst agent](.github/agents/business-analyst.agent.md) researches the company, proposes 4 tailored use cases, writes `spec.yaml`, and validates it by invoking `spec-validator.py` before declaring it ready.

When done: `@business-analyst generate spec`.

### Step 2 — Build the prototype

```text
@devlead build
```

The [devlead agent](.github/agents/devlead.agent.md) executes the dependency-aware build graph end-to-end: materialize → spec-validate → parallel-ready batch (steps 2-6) → hooks (step 7) → **preflight (hard gate)** → `azd up`.

- Fresh build: `@devlead build`
- Resume after failure: `@devlead build resume`
- Redo one step: `@devlead rebuild step 3`

### Step 3 — Reset (start over)

```text
@reset
```

Previews what will be cleared. Type `reset confirm` to actually clear. See [reset agent](.github/agents/reset.agent.md). Preserves `accelerator/`, `.github/`, `spec.yaml`.

---

## Quality gates (every gate must pass — do not bypass)

| Gate | Where it runs | What it enforces |
|---|---|---|
| Manifest schema | [accelerator/generators/manifest_schema.py](accelerator/generators/manifest_schema.py) inside `spec-validator.py` and `fill-templates.py` | Required fields, types, list-item shape, cross-field rule (every `agents[].model` resolves to a `modelDeployments[].deploymentName`). |
| Unresolved placeholders | [accelerator/generators/fill-templates.py](accelerator/generators/fill-templates.py) | Any leftover `{{PLACEHOLDER}}` token after hydration causes exit 1. |
| Hash-aware sentinels | [accelerator/generators/sentinels.py](accelerator/generators/sentinels.py) (CLI: `sentinels.py write …`) | Sentinels carry `specChecksum` and `outputHash`. Resume re-runs a step if the spec or its emitted outputs have changed. |
| Preflight | [accelerator/generators/preflight.py](accelerator/generators/preflight.py) — runs between step 7 and `azd up` | Required paths exist; manifest matches schema; no `{{PLACEHOLDER}}` left; every emitted `.py` compiles; every `.yaml`/`.json` parses; every agent.yaml tool resolves against `tool_definitions.yaml`; `az bicep build` succeeds; `az deployment group what-if` passes (soft-skip when not logged in or RG not yet created). |
| Contract tests | `py -3 -m unittest discover -s accelerator/tests` | 22+ unit tests covering schema, model catalog, sentinels, tool definitions, and end-to-end template hydration. Run these locally before publishing changes to the accelerator. |

---

## Security and runtime non-negotiables (apply to every emitted artifact)

- Identity: use a user-assigned managed identity. **Never** instantiate bare `DefaultAzureCredential()` in emitted Azure runtime code — always pass `managed_identity_client_id=os.environ["AZURE_CLIENT_ID"]`.
- Secrets: no secrets, connection strings, or keys in `spec.yaml`, `manifest.json`, generated code, or hooks. Cosmos and Foundry use AAD only.
- Foundry endpoints: `AZURE_AI_PROJECT_ENDPOINT` for `AIProjectClient`; `AZURE_FOUNDRY_ENDPOINT` for `AzureOpenAI`. Do not swap them.
- Foundry SDK: use `azure-ai-projects ≥ 2.1.0` with `AIProjectClient.agents.create_version(...)`. Never use `openai.beta.assistants` or `openai.beta.threads`.
- Cosmos DB NoSQL: every projected column in agent-authored SQL must be prefixed with `c.` (e.g. `SELECT c.id, c.status FROM c`). Partition keys must start with `/`. Never use `delete_item` in `cosmos_seed.py` (causes a Windows DNS failure).
- PostgreSQL: not part of the architecture. Do not emit any PostgreSQL connection code, drivers, or env vars.
- Sentinels: always written via `py -3 accelerator/generators/sentinels.py write …` so they capture the manifest checksum and an output hash. Never write a `.done` sentinel as a plain timestamp.
- Destructive operations: never delete sentinels by hand from inside an agent prompt — use `py -3 accelerator/scripts/clear-sentinels.py --step N` or `--all`.

---

## Build graph (concise)

1. Materialize `accelerator/templates/prototype/` → `generated/prototype/` (54 files).
2. Step 1 — `spec-validator.py` validates `spec.yaml`, writes `manifest.json`.
3. Steps 2-6 — parallel-ready batch (infra, data, agents, docs, backend config) from `manifest.json`.
4. Step 7 — hooks (`postprovision.{sh,ps1}`) after the batch.
5. Preflight — hard gate. Non-zero exit blocks deploy.
6. `azd up` from `generated/prototype/`.

See [.github/agents/devlead.agent.md](.github/agents/devlead.agent.md) for the full graph, resume logic, and progress format.

---

## File lifecycle (concise)

**Accelerator-owned source (edit here; never inside `generated/`)**
- `accelerator/templates/prototype/**`
- `accelerator/generators/**`
- `accelerator/scripts/**`
- `accelerator/tests/**`
- `.github/**`

**Generated during execution (rewritten on every build)**
- `generated/build-state/manifest.json`, `generated/build-state/*.done`
- `generated/prototype/infra/main.bicepparam`
- `generated/prototype/infra/modules/foundry-iq.bicep`
- `generated/prototype/infra/modules/search.bicep`
- `generated/prototype/db/cosmos_seed.py`
- `generated/prototype/agents/**`
- `generated/prototype/agents/knowledge/**`
- `generated/prototype/backend/config.py`
- `generated/prototype/hooks/postprovision.sh`, `generated/prototype/hooks/postprovision.ps1`

---

## What gets built (from spec.yaml)

| Artifact | Step |
|---|---|
| `generated/build-state/manifest.json` | 1 — validated, derived resource names |
| `generated/prototype/infra/main.bicepparam` | 2 — branding + model params |
| `generated/prototype/infra/modules/foundry-iq.bicep` | 2 — model deployments |
| `generated/prototype/infra/modules/search.bicep` | 2 — AI Search (provisions in parallel with Foundry, no dependency) |
| `generated/prototype/db/cosmos_seed.py` | 3 — domain rows only (plumbing in `_seed_lib.py`) |
| `generated/prototype/agents/**` | 4 — agent YAML + SKILL.md + schemas.py |
| `generated/prototype/agents/register_agents.py` | static template (copied by scaffold) |
| `generated/prototype/agents/knowledge/**` | 5 — knowledge base documents |
| `generated/prototype/backend/config.py` | 6 — env vars + branding constants |
| `generated/prototype/hooks/postprovision.{sh,ps1}` | 7 — full provisioning hooks |

---

## Operating the accelerator (links)

- [.github/agents/business-analyst.agent.md](.github/agents/business-analyst.agent.md) — spec authoring
- [.github/agents/devlead.agent.md](.github/agents/devlead.agent.md) — build + deploy graph
- [.github/agents/reset.agent.md](.github/agents/reset.agent.md) — reset emitted prototype
- [.github/specialists/](.github/specialists/) — per-step generators read fresh by devlead
- [accelerator/KNOWN_ISSUES.md](accelerator/KNOWN_ISSUES.md) — open issues only
- [accelerator/RESOLVED.md](accelerator/RESOLVED.md) — archive of fixed issues with template-level fix notes
- [accelerator/tests/](accelerator/tests/) — contract tests for the pipeline

When something breaks during a build, first check [accelerator/RESOLVED.md](accelerator/RESOLVED.md) for a prior incident. If you cannot find one, file a new entry in [accelerator/KNOWN_ISSUES.md](accelerator/KNOWN_ISSUES.md) and patch the underlying template — never patch the emitted output alone.
