# estimate_crew_hours

Estimate crew hours for a scope using ACCO productivity factors.

## When to use

- Estimator describes a scope ("install 800 linear feet of 6-inch chilled
  water piping in San Diego") and asks for a labor budget.
- The user supplies a quantity, a unit, and a trade.

## Inputs (typical)

- trade (e.g. "Pipefitter", "Sheet Metal")
- region (e.g. "San Diego, CA")
- quantity + unit (e.g. 800 lf, 12,000 sqft of ductwork, 30 fixtures)

## Procedure

1. Pull the rate and productivity factor for the trade/region from
   `labor_rates` (use `lookup_labor_rate`).
2. Pull the per-unit productivity baseline from the Trade Productivity
   Reference Guide via `search_knowledge_base("<trade> productivity <unit>")`.
3. Compute base hours = quantity × baseline hours-per-unit.
4. Adjust = base hours × productivity_factor.
5. Compute labor cost = adjusted hours × rate_usd_per_hour.

## Output rules

- Populate `crew_hour_estimate` with trade, hours, optional crew_size, and
  the assumptions used (baseline + productivity_factor + region).
- Also include the underlying rate row in `labor_rates`.
- State assumptions explicitly in the summary so the estimator can override.
