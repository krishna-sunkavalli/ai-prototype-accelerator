# Prototype Architecture

High-level flow of a deployed `ai-prototype-accelerator` instance.

## Design pattern: triage and specialists (router + experts)

Multi-agent chat assistants can be designed many ways — sequential pipelines, hierarchical planner-and-workers, peer-to-peer collaborative agents, plan-and-execute (ReAct), single monolithic agents with many tools, and so on. Each pattern trades off latency, predictability, reasoning depth, and operational complexity differently.

**This accelerator follows the *triage + specialists* pattern** (also called *router + experts* or *intent-based routing*):

- A single **triage agent** receives every user message, classifies intent in one short turn, and routes to exactly one **specialist agent**.
- Each **specialist** is a narrow, domain-scoped agent with its own instructions, knowledge, and tool list (SQL, AI Search, mock API).
- The orchestrator runs the selected specialist with per-user conversation history (`AgentSession`) until it produces a grounded answer.
- Off-topic or out-of-scope prompts are refused at triage, never reaching a specialist.

**Why this pattern for prototypes:**

- **Predictable cost and latency** — each turn is at most two agent calls (triage + one specialist), with no planner loops.
- **Bounded blast radius** — a specialist only sees the tools it needs, so tool-call surface is smaller and easier to evaluate.
- **Easy to extend** — adding a use case means adding one specialist + a routing keyword, with no changes to other agents.
- **Maps cleanly to evaluation** — each specialist can be evaluated against its own golden dataset in Foundry.

**Trade-offs (what this pattern is *not* good at):**

- Cross-domain questions that need two specialists working together — triage must pick one.
- Multi-step plans that span tools across specialists — needs a planner pattern instead.
- Open-ended research where the right specialist isn't knowable up front.

If your use case needs those, swap the orchestrator for a planner-and-workers or peer-to-peer pattern. The data plane, identity, and infrastructure layers stay the same.

---

> Diagrams are pre-rendered to SVG under [images/](images/) so the markdown preview displays them reliably even if the Mermaid preview extension misbehaves. The Mermaid source is kept in collapsible blocks below each image as the source of truth. To regenerate after editing, run:
>
> ```pwsh
> npx -y @mermaid-js/mermaid-cli -i <source.mmd> -o docs/images/<name>.svg --backgroundColor transparent
> ```

## Request flow (sequence)

![Request flow sequence diagram](images/prototype-sequence.svg)

<details>
<summary>Mermaid source</summary>

```mermaid
sequenceDiagram
    participant User
    participant SPA as SPA (Container App)
    participant API as FastAPI Backend
    participant Orch as Agent Orchestrator
    participant Triage as Triage Agent (Foundry)
    participant Specialist as Specialist Agent (Foundry)
    participant Tools as Tool Callables<br/>(SQL · Search · Mock)
    participant Data as Cosmos / AI Search / Mock

    User->>SPA: Ask a question
    SPA->>API: POST /chat
    API->>Orch: Dispatch
    Orch->>Triage: Classify intent
    Triage-->>Orch: Route to specialist
    Orch->>Specialist: Run with user prompt
    Specialist-->>Orch: Tool-call request
    Orch->>Tools: Invoke (FunctionInvocationLayer)
    Tools->>Data: Query
    Data-->>Tools: Rows / chunks / fixtures
    Tools-->>Orch: Tool result
    Orch->>Specialist: Submit tool output
    Specialist-->>Orch: Grounded answer
    Orch-->>SPA: Stream response
    SPA-->>User: Rendered answer
    Note over Orch,Triage: Off-topic prompts are refused at triage.
```

</details>

## Component layout (flowchart)

![Component layout flowchart](images/prototype-components.svg)

<details>
<summary>Mermaid source</summary>

```mermaid
flowchart LR
    User([User])

    subgraph ACA[Container App]
        SPA[Static SPA]
        API[FastAPI Backend]
        Orch[Agent Orchestrator]
        Tools[Tool Callables<br/>SQL · Search · Mock API]
    end

    subgraph Foundry[Azure AI Foundry]
        Agents[Triage and Specialist Agents]
        Models[Model Deployments]
    end

    subgraph DataPlane[Data and Knowledge]
        Cosmos[(Cosmos DB)]
        Search[(AI Search)]
        Blob[(Blob Storage)]
    end

    subgraph PlatformPlane[Platform]
        MI[Managed Identity]
        ACR[Container Registry]
        AppI[Application Insights]
    end

    User --> SPA
    SPA --> API
    API --> Orch
    Orch -->|run agent| Agents
    Agents --> Models
    Agents -.->|tool call request| Orch
    Orch -->|invoke| Tools
    Tools --> Cosmos
    Tools --> Search
    Tools -->|fixtures| API
    Search --> Blob
    ACA --> MI
    ACA --> ACR
    ACA --> AppI
```

</details>
