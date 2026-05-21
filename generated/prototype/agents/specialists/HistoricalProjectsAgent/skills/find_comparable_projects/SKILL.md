# find_comparable_projects

Find past ACCO projects similar in scope, size, and sector to a target bid.

## When to use

- Estimator asks for "similar projects", "comparables", or "what did X cost".
- The user mentions a sector (data center, healthcare, pharma, stadium, etc.)
  or a size band (e.g. "over 100,000 sqft", "around $10M").

## Inputs (typical)

- sector (e.g. "Data Center")
- size_band (e.g. minimum and/or maximum square_footage)
- year_window (e.g. "last 5 years")
- region (optional — derived from `c.location`)

## Cosmos SQL pattern

```sql
SELECT c.id, c.project_name, c.sector, c.location, c.year_completed,
       c.square_footage, c.final_cost_usd, c.cost_per_sqft_usd,
       c.schedule_months, c.scope_summary, c.outcome_notes
FROM c
WHERE c.sector = @sector
  AND c.year_completed >= @minYear
  AND c.square_footage >= @minSqft
ORDER BY c.year_completed DESC
OFFSET 0 LIMIT 20
```

Parameters: `[{"name": "@sector", "value": "Data Center"}, {"name": "@minYear", "value": 2021}, {"name": "@minSqft", "value": 100000}]`

Container: `projects` (partition key `/sector` — passing the sector keeps the
query partition-scoped and fast).

## Output rules

- Always populate `response.projects` with the rows returned (up to 20).
- Always cite project name, year, location, and final cost in the summary.
- If fewer than 3 matches return, broaden the criteria once (drop the size
  floor or extend the year window) and rerun.
