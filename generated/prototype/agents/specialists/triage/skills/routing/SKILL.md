# Routing skill

This skill is the triage agent's only responsibility: deciding which specialist
agent should answer the user's message.

## When this skill applies

Every user turn that arrives at the triage agent.

## Inputs

- Free-form user message in natural language.

## Outputs

- Exactly one specialist agent name, with no surrounding prose or punctuation.

## Specialist coverage

| Specialist                | Owns                                                            |
|---------------------------|-----------------------------------------------------------------|
| HistoricalProjectsAgent   | `projects` + `cost_line_items` containers, project lessons      |
| LaborRatesAgent           | `labor_rates` container, productivity guide                     |
| ScopeAnalysisAgent        | Estimating Standards Manual, RFP Playbook, Safety Program       |

## Routing heuristics

1. If the user mentions a specific past project, comparable cost, cost-per-sqft,
   or schedule outcome → `HistoricalProjectsAgent`.
2. If the user mentions a trade rate, productivity factor, crew composition, or
   crew-hour estimate → `LaborRatesAgent`.
3. If the user mentions an RFP, scope, risk, exclusion, clarification, or
   estimating standard → `ScopeAnalysisAgent`.
4. Ambiguous or conversational input → `HistoricalProjectsAgent` (default).
