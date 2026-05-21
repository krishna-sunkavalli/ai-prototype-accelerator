# summarize_project_outcomes

Summarize the final cost, schedule, and lessons learned for one or more past
projects so an estimator can spot risks before bidding.

## When to use

- Estimator asks "what happened on project X" or "did we have cost overruns".
- The user wants to understand outcomes, lessons learned, or risk patterns
  from a specific project or set of projects.

## Inputs (typical)

- project_id (e.g. "PRJ000042")
- OR a sector + size band to summarize a cohort

## Cosmos SQL pattern

Pull the project record:

```sql
SELECT c.id, c.project_name, c.sector, c.year_completed, c.final_cost_usd,
       c.cost_per_sqft_usd, c.schedule_months, c.scope_summary, c.outcome_notes
FROM c
WHERE c.id = @projectId
```

Pull the trade-level cost breakdown:

```sql
SELECT c.id, c.project_id, c.trade, c.category,
       c.labor_cost_usd, c.material_cost_usd, c.equipment_cost_usd,
       c.total_cost_usd, c.notes
FROM c
WHERE c.project_id = @projectId
ORDER BY c.total_cost_usd DESC
```

Container for the second query: `cost_line_items` (partition key
`/project_id` — pass the project_id for a partition-scoped lookup).

## Output rules

- Always include final cost, schedule, and at least one outcome note.
- Roll up the cost line items into a `cost_summary` object with totals per
  category (HVAC, Plumbing, Controls, Equipment, Commissioning).
- Populate `lessons_learned` with the qualitative outcome notes (one
  bullet per project).
