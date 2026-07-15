# ai-prototype-accelerator

[![CI](https://github.com/krishna-sunkavalli/ai-prototype-accelerator/actions/workflows/ci.yml/badge.svg)](https://github.com/krishna-sunkavalli/ai-prototype-accelerator/actions/workflows/ci.yml)
![Stars](https://img.shields.io/github/stars/krishna-sunkavalli/ai-prototype-accelerator?style=for-the-badge&logo=github)
![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Azure](https://img.shields.io/badge/azure-ready-0078D4?style=for-the-badge&logo=microsoftazure&logoColor=white)

`ai-prototype-accelerator` is a GitHub Copilot-driven scaffold that takes you from idea to deployed AI prototype in under 30 minutes.

Describe a scenario. Let GitHub Copilot orchestrate the build through specialist agents. Get a deployable prototype on Microsoft Foundry.

**What you get:** a multi-agent app hosted on Azure Container Apps, with agents (triage + specialists) powered by Microsoft Foundry, calling Azure Cosmos DB and Azure AI Search through the Microsoft Agent Framework — all authenticated with a single managed identity, provisioned by Bicep/AVM, and deployed with `azd up`.

**Architecture pattern:** the accelerator follows the *triage + specialists* (router + experts) pattern — one triage agent classifies intent and routes to exactly one domain specialist. It is one of several valid patterns for multi-agent chat assistants; see [docs/prototype-architecture.md](docs/prototype-architecture.md) for the rationale and trade-offs.

**Implementation:** built with the [Microsoft Agent Framework (MAF)](https://github.com/microsoft/agent-framework). Agents are registered as versioned **PromptAgent** definitions in Microsoft Foundry (instructions, model binding, tool schema, traces, evaluations) but **executed in-process** by the orchestrator in your Container App via the Foundry Responses API — not run as separately-hosted agent containers inside Foundry. See [docs/tech-stack.md](docs/tech-stack.md) for additional details.

> [!IMPORTANT]
> This accelerator helps teams go from idea to working prototype faster using AI-assisted development. It is intended to support rapid iteration, art-of-the-possible demos, and MVP development. Generated infrastructure, agent prompts, and tool implementations should be reviewed against your organization's security, compliance, and AI governance policies before use with customer, production, or regulated data.

---

## How It Works

The accelerator uses two Copilot agents that hand off in sequence.

**Step 1 — Research your scenario**

```text
@business-analyst Contoso https://contoso.com
```

`@business-analyst` researches the company, identifies a high-value AI use case, and generates a `spec.yaml` — the single source of truth that drives everything downstream. No manual scaffolding.

**Step 2 — Build and deploy**

```text
@devlead build
```

`@devlead` reads `spec.yaml` and orchestrates the full build: configures the Bicep/AVM infrastructure, registers Microsoft Foundry PromptAgents, generates backend and frontend code, and produces a deploy-ready prototype. After deploy it runs an acceptance smoke test (every starter question through the live app) and reports the measured spec-to-deployed build time.

**Step 3 — Iterate at spec speed**

Edit `spec.yaml` and run `@devlead build` again. The build is **incremental**: per-step input fingerprints (see `accelerator/scripts/plan-rebuild.py`) rerun only the steps your edit touches — a branding change rehydrates config without regenerating seed data, agents, or documents, and derived Azure resource names stay stable across iterations.

**Step 4 — Graduate the prototype**

```text
@export ../<product-name>
```

`@export` lifts `generated/prototype/` into a standalone repository with its own README, git history, and the originating spec preserved as `docs/spec.yaml` — the seed of the real product, deployable with `azd up` and no dependency on the accelerator.

---

## Documentation

- **[Prototype architecture](docs/prototype-architecture.md)** — request flow, components, tool-call pattern of a deployed prototype
- **[Accelerator architecture](docs/accelerator-architecture.md)** — build pipeline, repo layout, three-layer authorship model
- **[Tech stack](docs/tech-stack.md)** — every technology used, with versions

See [docs/README.md](docs/README.md) for the full documentation index.

---

## Source Of Truth

For execution-time architectural decisions, use [.github/architecture-reference.md](.github/architecture-reference.md).

Do not treat `.github/architecture.md` as canonical. It is generated and can be overwritten.

---

## Repo Model

### Accelerator-owned source

- `accelerator/templates/prototype/` — maintained prototype scaffold
- `accelerator/generators/` — scaffold materialization and template hydration
- `accelerator/scripts/` — accelerator utilities
- `.github/` — Copilot agents, specialists, and execution instructions

### Generated output

- `generated/build-state/` — manifest and per-step sentinels
- `generated/prototype/` — the produced application root

The produced app is designed to look like a normal application of its own. That makes future export-to-new-repo work straightforward.

---

## Quick Start

### Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- [Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
- [GitHub Copilot](https://github.com/features/copilot) with agent mode enabled in VS Code
- Azure subscription quota for the required OpenAI models
- Python 3.10+ on `PATH` as `py -3` (Windows) or `python3` (macOS/Linux), with `pyyaml` installed:

  ```bash
  py -3 -m pip install pyyaml
  ```

### 1. Clone the accelerator

```bash
git clone https://github.com/<your-org>/ai-prototype-accelerator.git
cd ai-prototype-accelerator
code .
```

Open the cloned folder in VS Code so GitHub Copilot agent mode can pick up the agents under `.github/agents/`.

### 2. Create or update the spec

Use the business-analyst agent:

```text
@business-analyst Contoso https://contoso.com
```

The agent researches the company, proposes a use case, and writes `spec.yaml` automatically once you select one.

### 3. Build the prototype

Use the build coordinator:

```text
@devlead build
```

Resume if needed:

```text
@devlead build resume
```

Redo one step if needed:

```text
@devlead rebuild step 4
```

`@devlead` runs preflight validation and `azd up` for you from `generated/prototype/`. When it finishes, the final line of the build log is the deployed Container App's public URL.

### 4. Verify

```bash
curl https://<containerAppFqdn>/health
```

### 5. Start a new idea

If you want to keep the accelerator repo but discard the prototype produced by a previous run, reset the generated output and build again.

Preferred chat workflow:

```text
@reset
```

That clears only generated output and preserves the maintained accelerator source.

On macOS/Linux:

```bash
bash accelerator/scripts/reset-generated.sh
```

On Windows PowerShell:

```powershell
pwsh -File accelerator/scripts/reset-generated.ps1
```

Then update `spec.yaml` and run:

```text
@devlead build
```

This preserves the accelerator source under `accelerator/` and removes only the produced prototype under `generated/`.

---

## What Gets Generated

Only these files are produced per build. Everything else under `generated/prototype/` is a **static template** copied verbatim from `accelerator/templates/prototype/`.

| Step | Layer | Output |
|---|---|---|
| 0 | scaffold copy | All 54 files under `generated/prototype/` (static templates + `.tpl` sources) |
| 1 | generator | `generated/build-state/manifest.json` |
| 2 | `.tpl` hydrate | `generated/prototype/infra/main.bicepparam` |
| 3 | LLM (domain rows only) | `generated/prototype/db/cosmos_seed.py` (plumbing lives in static `db/_seed_lib.py`) |
| 4 | LLM | `generated/prototype/agents/**` (YAML + SKILL.md + schemas.py per agent) |
| 5 | LLM | `generated/prototype/agents/knowledge/*.md` |
| 6 | `.tpl` hydrate | `generated/prototype/backend/config.py` |
| 7 | `.tpl` hydrate | `generated/prototype/hooks/postprovision.sh`, `generated/prototype/hooks/postprovision.ps1` |

Static templates that look generated but are **not** (fix bugs at the template source so they propagate to every future build): `infra/main.bicep`, `infra/modules/*.bicep` including `foundry-iq.bicep`, `hooks/preprovision.{sh,ps1}`, `hooks/postdeploy.{sh,ps1}`, `agents/register_agents.py`, `agents/tools/tool_definitions.yaml`, `db/_seed_lib.py`, and everything under `backend/`, `frontend/`, and `agents/` (except generator-owned files listed above).

---

## Quality Gates

| Gate | Where | What it enforces |
|---|---|---|
| Manifest schema | `accelerator/generators/manifest_schema.py` | Required fields, types, cross-field rule (every `agents[].model` resolves to a `modelDeployments[].deploymentName`) |
| Unresolved placeholders | `accelerator/generators/fill-templates.py` | Any leftover `{{PLACEHOLDER}}` after hydration is a hard error |
| Hash-aware sentinels | `accelerator/generators/sentinels.py` | `specChecksum` + `outputHash` per step; resume reruns drifted steps |
| Preflight | `accelerator/generators/preflight.py` | Runs between step 7 and `azd up`; blocks deploy on any failure |
| Contract tests | `py -3 -m unittest discover -s accelerator/tests` | 22+ tests on schema, model catalog, sentinels, tool definitions, end-to-end hydration |

Report open accelerator issues in [accelerator/KNOWN_ISSUES.md](accelerator/KNOWN_ISSUES.md); archived incidents with template-level fixes live in [accelerator/RESOLVED.md](accelerator/RESOLVED.md).

---

## Deployment Architecture

The produced prototype deploys the same logical system as before:

- Azure Container Apps hosts the FastAPI application.
- Azure AI Foundry hosts registered prompt agents.
- Azure OpenAI deployments provide model inference.
- Azure Cosmos DB stores structured scenario data.
- Azure AI Search indexes generated operational documents.
- User-assigned managed identity is used for service-to-service access.

### Agent runtime — Microsoft Agent Framework + Foundry (non-negotiable)

The produced application **must** orchestrate agents through Microsoft Agent Framework (MAF) bound to Foundry-registered PromptAgents. This is a hard architectural rule for every generated prototype.

- **Runtime (the app):** `agents/orchestrator.py` uses `agent_framework.foundry.FoundryAgent` and `agent_framework.AgentSession`. MAF is the orchestration layer; `FoundryAgent` connects each specialist to its Foundry-registered PromptAgent definition; `AgentSession` maintains per-specialist per-user state.
- **One-time bootstrap:** `agents/register_agents.py` runs once during `azd up` (postprovision hook) and creates the agent definitions in the Foundry project via `azure-ai-agents`' `AgentsClient`. This is Foundry's management plane — MAF reads what this script writes.
- **SDK split:**
  - `agent_framework` + `agent_framework.foundry` — runtime orchestration.
  - `azure-ai-agents` (`AgentsClient`) — one-time agent registration. Used instead of `azure-ai-projects.AIProjectClient.agents` because in `azure-ai-projects>=2.1.0` that namespace manages agent **versions**, not runtime agents.
  - `azure-ai-projects` — still required transitively by MAF's async `AIProjectClient`.

Do not replace MAF with direct OpenAI Assistants, direct REST calls, or hand-rolled chat loops in generated prototypes.

---

## Repo Structure

```text
ai-prototype-accelerator/
├── .github/                         # Copilot agents, specialists, architecture docs
├── accelerator/
│   ├── generators/                  # Materialize scaffold, hydrate templates
│   ├── scripts/                     # Accelerator utilities
│   └── templates/
│       └── prototype/               # Maintained scaffold source
├── generated/
│   ├── build-state/                 # Manifest + *.done sentinels
│   └── prototype/                   # Produced app root (deploy from here)
├── spec.yaml                        # User-authored prototype spec
├── README.md
└── CONTRIBUTING.md
```

---

## Working Rules

1. Never hand-edit files under `generated/` as a source change.
2. Source changes belong under `accelerator/` or `.github/`.
3. The generated prototype should stay self-contained and deployable from its own root.
4. The build state belongs under `generated/build-state/` only.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributor guidance.

---

## Code of Conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com).

## Security

To report a security vulnerability, see [SECURITY.md](SECURITY.md). Please **do not** open public GitHub issues for security reports.

## Support

This is a sample/accelerator project; see [SUPPORT.md](SUPPORT.md) for how to get help.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.
