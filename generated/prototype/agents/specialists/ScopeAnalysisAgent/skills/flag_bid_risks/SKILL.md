# flag_bid_risks

Flag risks, exclusions, and clarifications based on ACCO playbooks.

## When to use

- After a scope is identified (manually or via `extract_scope_items`).
- When the user asks "what risks should I flag" or "what exclusions should
  I include".

## Procedure

1. Search the knowledge base for "RFP Response Playbook risk flags" and
   "common exclusions" and "clarification templates".
2. Map each scope item to potential risks (equipment escalation, schedule
   pressure, owner-furnished items, mechanical/electrical interface, etc.).
3. Apply ACCO Safety Program posture for any high-hazard scope (confined
   space, roof work, energized systems) — call it out as a clarification.

## Output rules

- Populate `response.risks` with `{title, severity, rationale}` items.
  Severity is one of "low", "medium", "high".
- Populate `response.exclusions` with bid exclusion strings drawn from the
  RFP Response Playbook.
- Populate `response.clarifications` with bid clarification strings.
- Cite the playbook(s) used in `data_sources` (e.g. "RFP Response Playbook",
  "ACCO Estimating Standards and Procedures Manual", "ACCO Safety Program
  Overview").
