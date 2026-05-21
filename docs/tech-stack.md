# Tech Stack

Every technology used by a deployed `ai-prototype-accelerator` instance. Versions reflect the templates in [`accelerator/templates/prototype/`](../accelerator/templates/prototype/) at the time of writing — check the actual `requirements.txt` and `*.bicep` files for current pins.

## Application runtime

| Layer | Technology | Where it's used |
|---|---|---|
| Backend web framework | [FastAPI](https://fastapi.tiangolo.com/) `0.115.0` | [`backend/main.py`](../accelerator/templates/prototype/backend/main.py) — REST + WebSocket `/chat` |
| ASGI server | [Uvicorn](https://www.uvicorn.org/) `0.30.0` (standard extras) | Container entrypoint |
| WebSocket transport | [`websockets`](https://websockets.readthedocs.io/) `12.0` | Streamed agent responses |
| Frontend | Vanilla **HTML + CSS + JavaScript** (no framework) | [`frontend/src/`](../accelerator/templates/prototype/frontend/src/) — `app.js`, `styles.css` served statically by FastAPI |
| Config / validation | [Pydantic](https://docs.pydantic.dev/) `2.7.0`, [PyYAML](https://pyyaml.org/) `6.0.1`, [python-dotenv](https://pypi.org/project/python-dotenv/) `1.0.1` | `spec.yaml`, `agent.yaml`, env loading |
| HTTP client | [httpx](https://www.python-httpx.org/) `0.27.0` | Outbound calls in `mock_api_tool`, etc. |

## Agent layer

| Concern | Technology | Notes |
|---|---|---|
| Agent framework | [**Microsoft Agent Framework (MAF)** v1.3.0](https://github.com/microsoft/agent-framework) — `agent-framework-core`, `agent-framework-foundry` | Triage + specialist orchestration, tool-call loop via `FunctionInvocationLayer`, per-user `AgentSession` |
| Agent registry / runtime | [**Azure AI Foundry**](https://learn.microsoft.com/azure/ai-foundry/) — accessed via `azure-ai-projects` `≥ 2.1.0` (`AIProjectClient`) | Agents are pre-registered as **PromptAgent** versions (`AIProjectClient.agents.create_version()`). MAF's `FoundryAgent` connects to them by name via the **OpenAI Responses API** (not classic Assistants) |
| Models | Foundry **model deployments** declared in `spec.yaml` → written to [`infra/modules/foundry-iq.bicep`](../accelerator/templates/prototype/infra/modules/foundry-iq.bicep) | Each agent binds to one deployment by name |
| Responses API client | [`openai`](https://github.com/openai/openai-python) `≥ 1.75.0` | Pulled in transitively by `azure-ai-projects`; used inside MAF |
| Async transport | [`aiohttp`](https://docs.aiohttp.org/) `≥ 3.9.0` | Required by `azure-core` for the async `AIProjectClient` path |

## Data plane

| Service | Library | Used by |
|---|---|---|
| **Azure Cosmos DB for NoSQL** | [`azure-cosmos`](https://pypi.org/project/azure-cosmos/) `≥ 4.7.0` | [`agents/tools/sql_tool.py`](../accelerator/templates/prototype/agents/tools/sql_tool.py) (queries) and [`db/_seed_lib.py`](../accelerator/templates/prototype/db/_seed_lib.py) (seeding) — AAD-only, every projected column uses `c.` prefix |
| **Azure AI Search** | [`azure-search-documents`](https://pypi.org/project/azure-search-documents/) `11.6.0` | [`agents/tools/search_tool.py`](../accelerator/templates/prototype/agents/tools/search_tool.py) — hybrid + semantic with extractive captions/answers |
| **Azure Blob Storage** | [`azure-storage-blob`](https://pypi.org/project/azure-storage-blob/) `12.20.0` | Knowledge documents under `agents/knowledge/**` indexed into AI Search |
| **Mock API** | In-process FastAPI router | Fixture data for prototypes without a real backend |

## Identity & security

| Concern | Technology |
|---|---|
| Auth library | [`azure-identity`](https://pypi.org/project/azure-identity/) `1.17.0` |
| Runtime identity | **User-assigned managed identity** on the Container App. All produced code uses `DefaultAzureCredential(managed_identity_client_id=os.environ["AZURE_CLIENT_ID"])` — bare `DefaultAzureCredential()` is forbidden by [`copilot-instructions.md`](../.github/copilot-instructions.md) |
| Secrets | **None in code or config.** Cosmos and Foundry are AAD-only. No keys, connection strings, or `.env` secrets cross the build boundary |

## Hosting / infrastructure

| Resource | IaC | Notes |
|---|---|---|
| **Azure Container Apps** | [`infra/modules/container-app.bicep`](../accelerator/templates/prototype/infra/modules/container-app.bicep) | Hosts the FastAPI backend + SPA; UAMI attached |
| **Azure Container Registry** | [`infra/modules/container-registry.bicep`](../accelerator/templates/prototype/infra/modules/container-registry.bicep) | Image registry |
| **Azure AI Foundry (hub + project + models)** | [`foundry.bicep`](../accelerator/templates/prototype/infra/modules/foundry.bicep), [`foundry-iq.bicep`](../accelerator/templates/prototype/infra/modules/foundry-iq.bicep) | Project + model deployments |
| **Azure Cosmos DB (NoSQL)** | [`cosmos.bicep`](../accelerator/templates/prototype/infra/modules/cosmos.bicep) | Databases + containers from `spec.yaml` |
| **Azure AI Search** | bundled in [`foundry-iq.bicep`](../accelerator/templates/prototype/infra/modules/foundry-iq.bicep) (Foundry IQ stack) | Index + semantic config |
| **Azure Storage (Blob)** | [`storage.bicep`](../accelerator/templates/prototype/infra/modules/storage.bicep) | Knowledge documents |
| **Application Insights + Log Analytics** | [`monitoring.bicep`](../accelerator/templates/prototype/infra/modules/monitoring.bicep) | Observability backend |
| **Container image** | [`Dockerfile`](../accelerator/templates/prototype/Dockerfile) | Python 3.11 slim base, multi-stage build |
| **Deployment tool** | [Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/) | `azd up` from `generated/prototype/` |
| **Bicep parameter hydration** | `.bicepparam` (`main.bicepparam`) generated from `spec.yaml` via `accelerator/generators/fill-templates.py` | No ARM JSON, no manual params |

## Observability

| Concern | Technology |
|---|---|
| Tracing SDK | [OpenTelemetry SDK](https://opentelemetry.io/docs/languages/python/) `≥ 1.39.0` |
| FastAPI instrumentation | `opentelemetry-instrumentation-fastapi` `≥ 0.50b0` |
| Azure exporter | [`azure-monitor-opentelemetry`](https://pypi.org/project/azure-monitor-opentelemetry/) `≥ 1.6.0` — auto-exports traces, logs, metrics to Application Insights |

## Build pipeline (accelerator-side, not deployed)

| Concern | Technology |
|---|---|
| Spec validation, manifest, template hydration | **Python 3.x** under [`accelerator/generators/`](../accelerator/generators/) — `spec-validator.py`, `fill-templates.py`, `materialize-prototype.py`, `manifest_schema.py`, `model_catalog.py`, `preflight.py`, `sentinels.py` |
| Tests | `unittest` — `py -3 -m unittest discover -s accelerator/tests` (22+ contract tests) |
| Reset / sentinel utilities | PowerShell + Bash under [`accelerator/scripts/`](../accelerator/scripts/) |

## What's deliberately **not** in the stack

- **No PostgreSQL.** Cosmos DB is the only OLTP store. Producing Postgres drivers or env vars is forbidden.
- **No OpenAI Assistants API.** MAF v1.3.0 uses the **Responses API** via `AIProjectClient`; classic `openai.beta.assistants` / `openai.beta.threads` are not used.
- **No React / Vue / Next.js.** The frontend is intentionally vanilla JS to keep prototypes lightweight and dependency-free.
- **No secrets manager / Key Vault for app secrets.** Everything is AAD + managed identity; Key Vault appears only if a feature requires it.
- **No Foundry hosted tools.** All tool execution is client-side Python so data and identity stay inside the Container App — see [`prototype-architecture.md`](prototype-architecture.md) for the rationale.
