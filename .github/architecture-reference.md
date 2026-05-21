# Architecture Reference — ai-prototype-accelerator

Canonical architecture source for agent execution decisions.
Use this file when behavior, topology, or file ownership is unclear.

This file is durable and hand-maintained.
Do not treat `.github/architecture.md` as canonical because it can be overwritten during builds.

---

## System Topology (Target State)

- Azure Container Apps hosts the FastAPI chat application.
- Azure AI Foundry project hosts registered agents (triage + specialists).
- Azure OpenAI deployments provide model inference used by Foundry agents.
- Azure Cosmos DB NoSQL stores structured data.
- Azure AI Search indexes operational markdown docs from Blob Storage.
- User-assigned managed identity is used for service-to-service access.

## Runtime Flow (Request Path)

1. User sends a chat request to FastAPI.
2. Orchestrator routes through triage, then to a specialist.
3. Specialist invokes tools:
   - `run_sql_query` for Cosmos containers
   - `search_knowledge_base` for AI Search
   - optional `call_mock_api` only when enabled in spec
4. Specialist returns structured response for UI rendering.

## Repository Boundary

- `accelerator/` is maintained source.
- `generated/build-state/` is build workflow state.
- `generated/prototype/` is the emitted application and deployment root.

The generated prototype must be self-contained enough to become a standalone repository later.

## Build And Deploy Flow

1. `@business-analyst` produces `spec.yaml` from company + website and use-case selection, then self-checks by invoking `accelerator/generators/spec-validator.py`.
2. `@devlead build` first materializes the scaffold from `accelerator/templates/prototype/` into `generated/prototype/` (54 files).
3. Step 1 — `spec-validator.py` validates the spec and writes `generated/build-state/manifest.json` plus a hash-aware `01-spec-validator.done` sentinel.
4. Steps 2-6 generate independent outputs into `generated/prototype/` as a parallel-ready batch; each step writes its own hash-aware sentinel via `accelerator/generators/sentinels.py write`.
5. Step 7 writes deployment hooks into `generated/prototype/hooks/`.
6. **Preflight** — `accelerator/generators/preflight.py` is a hard gate between step 7 and deploy. It checks required paths, manifest schema, unresolved placeholders, py_compile, YAML/JSON parse, tool resolution against `tool_definitions.yaml`, `az bicep build`, and `az deployment group what-if` (soft-skips when not logged in or the resource group does not yet exist).
7. Deployment runs from `generated/prototype/` via `azd up`. Non-zero preflight exit blocks the deploy.

## Three-Layer Authoring Model

Every artifact under `generated/prototype/` belongs to exactly one layer:

- **Static template** — files under `accelerator/templates/prototype/**` with no `.tpl` extension. Copied verbatim by `materialize-prototype.py`. Maintainers only.
- **`.tpl` + placeholders** — files with `.tpl` extension. Hydrated by `fill-templates.py` using `{{UPPER_SNAKE_CASE}}` substitution from `manifest.json`. Unresolved tokens are a hard error.
- **LLM-generated** — produced by specialists in `.github/specialists/*.md`. Allowed only for genuinely creative content (agent personas, seed data, knowledge docs). Plumbing and deterministic structure belong in the two layers above.

## Quality Gates (every gate is non-negotiable)

- Manifest schema (`manifest_schema.py`) enforces required fields, types, and the cross-field rule that every `agents[].model` resolves to a `modelDeployments[].deploymentName`.
- Hash-aware sentinels (`sentinels.py`) carry `specChecksum` + `outputHash`; resume re-runs any step whose spec or outputs have drifted.
- Preflight (`preflight.py`) blocks deploy on any of the checks listed above.
- Contract tests (`py -3 -m unittest discover -s accelerator/tests`) protect the build pipeline from regressions.

## Architecture Guardrails

- Cosmos DB is the structured store; do not switch to PostgreSQL assumptions.
- Agent definitions are generated under `generated/prototype/agents/` and registered to Foundry.
- Runtime uses generated config and registered agents from `generated/prototype/`; avoid introducing parallel stacks.
- Parallel generation is allowed only for steps that read `generated/build-state/manifest.json` and write distinct outputs.
- If uncertain, confirm against:
  1. this file
  2. `generated/build-state/manifest.json`
  3. generated artifacts under `generated/prototype/`
- Do not invent services, endpoints, or files not represented in these sources.

## File Ownership Summary

- Accelerator-owned source: `accelerator/templates/prototype/`, `accelerator/generators/`, `accelerator/scripts/`, `.github/`.
- Generated during execution: `generated/build-state/`, plus emitted prototype files under `generated/prototype/`.
- Overwritten/regenerated: all step-owned files under `generated/prototype/` and all sentinels under `generated/build-state/`.
