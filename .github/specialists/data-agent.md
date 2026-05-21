# Data Agent — Generates generated/prototype/db/cosmos_seed.py

## Architecture guardrail

If behavior or file ownership is unclear, check `.github/architecture-reference.md` first.
Then verify against `generated/build-state/manifest.json` and generated artifacts.
Do not assume services, files, or runtime behavior that are not documented.

## Purpose
Generate the **domain-specific seed data** for the Cosmos DB containers.

All plumbing — `CosmosClient`, credential, container creation, upsert loops,
print formatting — lives in `db/_seed_lib.py` (static template).
**Do not import `azure.cosmos` directly.** Do not re-implement upsert loops.
Use `SeedRunner` + `SeedSpec` as shown below.

Database: Azure Cosmos DB NoSQL API, Serverless SKU.

## Prerequisite
Read `generated/build-state/manifest.json` first. If missing, stop.

## Inputs
Read: `generated/build-state/manifest.json`

## Output
Write: `generated/prototype/db/cosmos_seed.py`
Write sentinel: `generated/build-state/03-data-agent.done` — via the sentinel CLI:

```
py -3 accelerator/generators/sentinels.py write \
  --sentinel generated/build-state/03-data-agent.done \
  --manifest generated/build-state/manifest.json \
  --output generated/prototype/db/cosmos_seed.py
```

Never write the sentinel as a plain timestamp.

The static helper `db/_seed_lib.py` is already in place via
`materialize-prototype.py`. **Do not regenerate it.**

---

## cosmos_seed.py required structure

```python
#!/usr/bin/env python3
"""Cosmos DB seed data — {customer.name} prototype.

Domain data only. All Cosmos plumbing lives in _seed_lib.SeedRunner.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from _seed_lib import SeedRunner, SeedSpec

# Deterministic randomness so seed data is stable across runs.
RNG = random.Random(20260518)

# ── Domain constants (lists, weights, name pools, etc.) ───────────────────
# ... whatever the spec scenario requires ...

# ── Helpers (id formatters, weighted_choice, timestamps) ──────────────────
def now_utc() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0)

def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Per-container row generators ──────────────────────────────────────────
# One generator function per manifest.tables[] entry. Each yields dicts that
# include 'id' (deterministic) and the partition-key field.

def rows_<container_name>():
    for i in range(1, <tables[].seedCount> + 1):
        yield {
            "id": "<PREFIX>{:06d}".format(i),
            "<partition_key_field>": "<value>",
            # ... domain fields matching tables[].seedScenario ...
        }

# ── Seed spec ─────────────────────────────────────────────────────────────
SEED_SPECS = [
    SeedSpec(
        container_id="<container_name>",
        partition_key="<tables[].partitionKey>",
        rows=rows_<container_name>,
    ),
    # one SeedSpec per manifest.tables[] entry
]


def main() -> None:
    SeedRunner().run(SEED_SPECS)


if __name__ == "__main__":
    main()
```

---

## Rules — every one is mandatory, no exceptions

### R1 — Do NOT import azure.cosmos or azure.identity
All Cosmos and credential logic lives in `_seed_lib`. Importing the SDK
here defeats the purpose of the static helper and re-opens the regional-
endpoint / DNS bugs we already fixed (#3 in KNOWN_ISSUES).

### R2 — Do NOT call upsert_item, create_container_if_not_exists, or get_database_client
These live in `_seed_lib.SeedRunner`. Just yield rows.

### R3 — Deterministic, zero-padded IDs
IDs must be predictable so re-running the seed overwrites the same item:
- CORRECT: `"ACC{:06d}".format(i)` → "ACC000001"
- WRONG:   `str(uuid.uuid4())`        → random UUID every run

### R4 — One SeedSpec per manifest.tables[] entry
- `container_id`  = `manifest.tables[].name`
- `partition_key` = `manifest.tables[].partitionKey` (must start with `/`)
- Row count       ≥ `manifest.tables[].seedCount`

### R5 — Every row dict MUST include the partition-key field
If `partitionKey` is `/branch_office` then every row needs
`"branch_office": "..."`. SeedRunner does not infer this — Cosmos rejects
items without the PK field.

### R6 — Timestamps: ISO-8601, deterministic
```python
def days_ago(n: float) -> str:
    return iso(now_utc() - timedelta(days=n))
```
Use a frozen `now_utc()` (not `datetime.utcnow()`) so timestamps are stable
across runs.

### R7 — Seed scenario fidelity
`manifest.tables[].seedScenario` describes what the data should look like.
Generate data that matches the scenario exactly — the AI agents will query
this during demos. It must look real for `manifest.customer.industry`.

---

## After success
Print:
```
[Step 3/7] Cosmos seed script generated.
  generated/prototype/db/cosmos_seed.py written
  Containers: <list from manifest.tables[]>
```
