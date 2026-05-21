# Documentation

Architecture and reference documentation for `ai-prototype-accelerator`.

## Architecture

- **[Prototype architecture](prototype-architecture.md)** — How a deployed prototype works end-to-end: request flow, component layout, and the client-side tool-call pattern that keeps data inside the Container App.
- **[Accelerator architecture](accelerator-architecture.md)** — How the build system itself works: spec → manifest → materialize → hydrate → preflight → `azd up`, plus the three-layer authorship model and resume/rebuild semantics.

## Reference

- **[Tech stack](tech-stack.md)** — Every technology used by a deployed prototype, with versions and links to the files where each one is used.

## Diagrams

All diagrams live as Mermaid source inside the architecture docs above and are pre-rendered to SVG under [images/](images/) for reliable preview rendering. To regenerate after editing the Mermaid source:

```pwsh
npx -y @mermaid-js/mermaid-cli -i <source.mmd> -o docs/images/<name>.svg --backgroundColor transparent
```

| SVG | Source doc |
|---|---|
| [prototype-sequence.svg](images/prototype-sequence.svg) | [prototype-architecture.md](prototype-architecture.md) |
| [prototype-components.svg](images/prototype-components.svg) | [prototype-architecture.md](prototype-architecture.md) |
| [accelerator-pipeline.svg](images/accelerator-pipeline.svg) | [accelerator-architecture.md](accelerator-architecture.md) |
| [accelerator-layout.svg](images/accelerator-layout.svg) | [accelerator-architecture.md](accelerator-architecture.md) |

## See also

- [`.github/architecture-reference.md`](../.github/architecture-reference.md) — canonical execution-time architectural reference (source of truth)
- [`accelerator/KNOWN_ISSUES.md`](../accelerator/KNOWN_ISSUES.md) — open issues
- [`accelerator/RESOLVED.md`](../accelerator/RESOLVED.md) — archive of resolved incidents and template-level fixes
