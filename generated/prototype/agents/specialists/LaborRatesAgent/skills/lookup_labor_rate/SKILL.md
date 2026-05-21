# lookup_labor_rate

Look up the current ACCO labor rate for a trade in a given region.

## When to use

- Estimator asks "what is the sheet metal rate in LA" or similar.
- Estimator names a trade plus a region (or one of the two and asks for the
  full table).

## Inputs (typical)

- region (e.g. "Los Angeles, CA")
- trade (e.g. "Sheet Metal", "Pipefitter")

## Cosmos SQL pattern

Single-trade, single-region:

```sql
SELECT c.region, c.trade, c.rate_usd_per_hour, c.productivity_factor,
       c.effective_date, c.source
FROM c
WHERE c.region = @region
  AND c.trade = @trade
ORDER BY c.effective_date DESC
OFFSET 0 LIMIT 5
```

All trades for one region:

```sql
SELECT c.region, c.trade, c.rate_usd_per_hour, c.productivity_factor,
       c.effective_date
FROM c
WHERE c.region = @region
ORDER BY c.trade ASC
```

Container: `labor_rates` (partition key `/region` — scope to the partition
when the region is known).

## Output rules

- Always show region, trade, rate, and effective date.
- Populate `response.labor_rates` with every returned row (do not collapse
  into prose).
