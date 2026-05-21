# Accelerator Architecture

How the `ai-prototype-accelerator` itself works — the pipeline that turns one `spec.yaml` into a fully provisioned, validated, deployable prototype under `generated/prototype/`.

For the architecture of the **produced prototype**, see [prototype-architecture.md](prototype-architecture.md). This document covers the **build system**.

> Diagrams are pre-rendered to SVG under [images/](images/) for reliable preview rendering. Mermaid source is kept in collapsible blocks below each image. To regenerate after editing, run:
>
> ```pwsh
> npx -y @mermaid-js/mermaid-cli -i <source.mmd> -o docs/images/<name>.svg --backgroundColor transparent
> ```

## Build pipeline (sequence)

![Accelerator build pipeline](images/accelerator-pipeline.svg)

<details>
<summary>Mermaid source</summary>

```mermaid
sequenceDiagram
    participant User
    participant BA as @business-analyst
    participant Spec as spec.yaml
    participant DL as @devlead
    participant Mat as materialize-prototype.py
    participant SV as spec-validator.py
    participant FT as fill-templates.py
    participant Specs as Specialists<br/>(.github/specialists/*.md)
    participant Sent as sentinels.py
    participant PF as preflight.py
    participant Out as generated/prototype/
    participant AZD as azd up

    User->>BA: Company + website
    BA->>BA: Research, propose 4 use cases
    BA->>SV: Validate proposed spec
    SV-->>BA: OK
    BA->>Spec: Write spec.yaml

    User->>DL: build
    DL->>Mat: Step 0 — materialize templates
    Mat->>Out: Copy 54 static files (no .tpl)
    DL->>SV: Step 1 — validate spec.yaml
    SV->>Out: Write manifest.json
    SV->>Sent: write 01-spec-validator.done

    par Steps 2–6 (parallel-ready batch)
        DL->>FT: Step 2 — infra (main.bicepparam, foundry-iq.bicep)
        FT->>Out: Hydrate .tpl placeholders
        FT->>Sent: write 02-infra-agent.done
    and
        DL->>Specs: Step 3 — data (cosmos_seed.py)
        Specs->>Out: Domain rows
        DL->>Sent: write 03-data-agent.done
    and
        DL->>Specs: Step 4 — agents (agent.yaml + SKILL.md + schemas.py)
        Specs->>Out: agents/**
        DL->>Sent: write 04-agents-builder.done
    and
        DL->>Specs: Step 5 — docs (knowledge/**)
        Specs->>Out: Knowledge documents
        DL->>Sent: write 05-docs-agent.done
    and
        DL->>FT: Step 6 — backend config.py
        FT->>Out: Hydrate placeholders
        FT->>Sent: write 06-backend-agent.done
    end

    DL->>FT: Step 7 — hooks (postprovision.{sh,ps1})
    FT->>Out: Hydrate placeholders
    FT->>Sent: write 07-hook-agent.done

    DL->>PF: Preflight (hard gate)
    PF->>Out: Validate manifest, placeholders, py compile,<br/>yaml/json parse, tool refs, bicep build, what-if
    PF-->>DL: PASS or FAIL

    DL->>AZD: azd up from generated/prototype/
    AZD-->>User: Live prototype
```

</details>

## Repository layout (flowchart)

![Accelerator repository layout](images/accelerator-layout.svg)

<details>
<summary>Mermaid source</summary>

```mermaid
flowchart LR
    Spec[spec.yaml]

    subgraph Source[accelerator/ — maintained source]
        subgraph Templates[templates/prototype/]
            Static[Static files<br/>54 plain files]
            Tpl[".tpl files<br/>UPPER_SNAKE placeholders"]
        end
        subgraph Generators[generators/]
            G1[materialize-prototype.py]
            G2[spec-validator.py]
            G3[fill-templates.py]
            G4[manifest_schema.py]
            G5[model_catalog.py]
            G6[preflight.py]
            G7[sentinels.py]
        end
        Tests[tests/<br/>22+ unittest cases]
        Scripts[scripts/<br/>reset · clear-sentinels]
    end

    subgraph Agents[.github/]
        BAagent[agents/business-analyst]
        DLagent[agents/devlead]
        RSagent[agents/reset]
        Spcs[specialists/*.md]
    end

    subgraph Build[generated/build-state/ — gitignored]
        Manifest[manifest.json]
        Sentinels[01–07 *.done<br/>hash-aware]
    end

    subgraph Output[generated/prototype/ — deploy root]
        OutStatic[Static templates copied verbatim]
        OutHydrated[Hydrated .tpl outputs]
        OutLLM[LLM-generated agents/data/docs]
        AzdYaml[azure.yaml]
        Infra[infra/**]
    end

    AZD([azd up])
    Azure([Azure subscription])

    Spec --> G2
    Static --> G1
    Tpl --> G3
    G4 --> G2
    G4 --> G3
    G5 --> G2
    G7 -. writes .-> Sentinels
    G2 --> Manifest
    G1 --> OutStatic
    G3 --> OutHydrated
    Spcs --> OutLLM
    DLagent --> G1
    DLagent --> G2
    DLagent --> G3
    DLagent --> Spcs
    BAagent --> Spec
    Manifest --> G3
    Manifest --> Spcs
    Manifest --> G6
    G6 --> Output
    Output --> AZD
    AZD --> Azure
    RSagent -. clears .-> Build
    RSagent -. clears .-> Output
```

</details>

## The three layers of produced output

Every artifact under `generated/prototype/` falls into exactly one of three layers:

| Layer | Source | Who writes it | Mutability |
|---|---|---|---|
| **Static template** | `accelerator/templates/prototype/**` (no `.tpl`) | Accelerator maintainers only | Copied verbatim by `materialize-prototype.py`. Fix bugs at the template — never patch the generated file alone. |
| **`.tpl` + placeholders** | `accelerator/templates/prototype/**/*.tpl` | Maintainers (template) + `fill-templates.py` (hydration) | Placeholders are `{{UPPER_SNAKE_CASE}}`. Unresolved tokens = hard error. |
| **LLM-generated** | `.github/specialists/*.md` invoked by `@devlead` | Specialists at build time | Only genuinely creative content: agent personas, seed data, knowledge docs. Plumbing belongs in the layers above. |

## Quality gates

Each gate must pass — none can be bypassed.

| Gate | Implementation | What it enforces |
|---|---|---|
| **Manifest schema** | [`manifest_schema.py`](../accelerator/generators/manifest_schema.py) — invoked by `spec-validator.py` and `fill-templates.py` | Required fields, types, list-item shape, cross-field rule (every `agents[].model` resolves to a `modelDeployments[].deploymentName`) |
| **Unresolved placeholders** | [`fill-templates.py`](../accelerator/generators/fill-templates.py) | Any leftover `{{PLACEHOLDER}}` after hydration → exit 1 |
| **Hash-aware sentinels** | [`sentinels.py`](../accelerator/generators/sentinels.py) | Sentinels carry `specChecksum` + `outputHash`. Resume re-runs a step if either changed. |
| **Preflight** | [`preflight.py`](../accelerator/generators/preflight.py) | Required paths exist · manifest matches schema · no `{{PLACEHOLDER}}` left · every `.py` compiles · every `.yaml`/`.json` parses · agent tool refs resolve · `az bicep build` succeeds · `az deployment group what-if` passes (soft-skip if not logged in) |
| **Contract tests** | `py -3 -m unittest discover -s accelerator/tests` | 22+ unit tests for schema, model catalog, sentinels, tool definitions, end-to-end hydration |

## Resume & rebuild semantics

The build graph is **dependency-aware** and **idempotent**:

- **Fresh build** — `@devlead build` runs every step from scratch.
- **Resume** — `@devlead build resume` skips steps whose sentinel matches the current `specChecksum` + `outputHash`. Touch `spec.yaml` → invalidates step 1 → cascades to dependents.
- **Targeted rebuild** — `@devlead rebuild step 3` clears that step's sentinel (and any missing prerequisites), then re-runs only what's needed.
- **Reset** — `@reset` previews what will be cleared; `@reset confirm` wipes `generated/` while preserving `accelerator/`, `.github/`, and `spec.yaml`.

## Agents (orchestration layer)

The build is driven by three Markdown-defined agents under `.github/agents/`:

| Agent | Role |
|---|---|
| [`business-analyst`](../.github/agents/business-analyst.agent.md) | Researches a company, proposes 4 tailored AI use cases, writes & validates `spec.yaml` |
| [`devlead`](../.github/agents/devlead.agent.md) | Executes the build graph end-to-end: materialize → validate → parallel batch → hooks → preflight → `azd up` |
| [`reset`](../.github/agents/reset.agent.md) | Resets the produced prototype + build state without touching maintained source |

Each step in the graph delegates content generation to a **specialist** prompt under [`.github/specialists/`](../.github/specialists/), which devlead reads fresh on every invocation.

## Source-of-truth precedence

When in doubt during a build, consult in this order:

1. [`.github/architecture-reference.md`](../.github/architecture-reference.md) — canonical
2. `generated/build-state/manifest.json` — current build's resolved values
3. Generated artifacts under `generated/prototype/` — last-known produced state

Never treat files under `generated/` as long-term source-of-truth edits — they are rewritten on every `@devlead build`.
