# extract_scope_items

Extract the major scope items from an RFP description so the estimator can
build the bid breakdown.

## When to use

- The user pastes RFP text and asks "what is the scope here".
- The user describes a project verbally and asks for a scope breakdown.

## Procedure

1. Read the RFP text or description carefully.
2. Group scope by trade or system (HVAC, plumbing, controls, equipment,
   commissioning). Mirror the ACCO Estimating Standards manual structure.
3. Search the knowledge base for the section "Estimate structure" to confirm
   ordering and any required line items.
4. Emit one entry per scope item — concise, declarative, no commentary.

## Output rules

- Populate `response.scope_items` with one string per scope item.
- The summary should state the dominant system (e.g. "Predominantly HVAC
  with chilled-water plant and DOAS scope").
- Cite "ACCO Estimating Standards and Procedures Manual" in `data_sources`.
