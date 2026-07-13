#!/usr/bin/env python3
"""
db/_seed_lib.py — Reusable Cosmos DB seed plumbing.

Static helper shipped with every prototype. Owns:
    - CosmosClient construction with AzureCliCredential (local) / DefaultAzureCredential (cloud)
    - Container creation with the correct partition-key path
    - Idempotent upserts (re-running the seed never duplicates)
    - Container-name → row-count reporting in a consistent format
    - Dry-run validation (SEED_DRY_RUN=1): executes the row generators
      without touching Cosmos and enforces the seed contract (unique 'id',
      partition-key field present on every row). Preflight uses this to
      verify the LLM-generated cosmos_seed.py before any deploy.

The per-prototype cosmos_seed.py is responsible only for *data*: deterministic
RNG, domain-specific row generators, and the SEED_SPECS list passed to
SeedRunner.run().

Why this exists:
    The LLM specialist used to emit the full seed script per prototype. The
    plumbing (connect / create container / upsert loop) was identical every
    time, and the small variations between builds (forgetting upsert_item,
    using create_item instead, missing partition_key on a container) caused
    silent reseed failures. Centralising plumbing here eliminates that
    drift class.

Azure SDK imports are lazy: dry-run must work on build hosts that only have
the accelerator's minimal dependencies installed.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable

# CosmosClient is thread-safe; the SDK retries 429 throttling automatically.
# 15 workers turns ~850 sequential upserts (2-3 minutes) into ~15 seconds.
_SEED_WORKERS = 15


def _is_dry_run() -> bool:
    return os.environ.get("SEED_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def get_credential():
    """Prefer AzureCliCredential locally; fall back to DefaultAzureCredential."""
    from azure.identity import AzureCliCredential, DefaultAzureCredential

    try:
        cred = AzureCliCredential()
        # Force a token acquisition so we fail fast if `az login` is missing.
        cred.get_token("https://cosmos.azure.com/.default")
        return cred
    except Exception:
        client_id = os.environ.get("AZURE_CLIENT_ID")
        # Always pass managed_identity_client_id (None when unset locally) so
        # we never instantiate a bare DefaultAzureCredential() — see
        # .github/copilot-instructions.md security non-negotiables.
        return DefaultAzureCredential(managed_identity_client_id=client_id)


def get_client(endpoint: str | None = None):
    from azure.cosmos import CosmosClient

    endpoint = endpoint or os.environ["AZURE_COSMOS_ENDPOINT"]
    return CosmosClient(endpoint, get_credential())


@dataclass
class SeedSpec:
    """Describes one container to seed.

    container_id: Cosmos container name.
    partition_key: Partition-key path, e.g. '/branch_office'. Must start with '/'.
    rows: A zero-arg callable returning an iterable of dict rows. Each row
          MUST include 'id' for upsert idempotency.
    """

    container_id: str
    partition_key: str
    rows: Callable[[], Iterable[dict]]


class SeedRunner:
    def __init__(self, database_name: str | None = None, endpoint: str | None = None):
        self.dry_run = _is_dry_run()
        if self.dry_run:
            self.database_name = database_name or os.environ.get(
                "AZURE_COSMOS_DATABASE", "(dry-run)"
            )
            self.client = None
            self.db = None
        else:
            self.database_name = database_name or os.environ["AZURE_COSMOS_DATABASE"]
            self.client = get_client(endpoint)
            self.db = self.client.get_database_client(self.database_name)

    def run(self, specs: list[SeedSpec]) -> None:
        if self.dry_run:
            self._run_dry(specs)
            return

        from azure.cosmos import PartitionKey

        print(f"Seeding Cosmos DB: {self.database_name}")
        for spec in specs:
            if not spec.partition_key.startswith("/"):
                raise ValueError(
                    f"partition_key for '{spec.container_id}' must start with '/' "
                    f"(got {spec.partition_key!r})"
                )
            container = self.db.create_container_if_not_exists(
                id=spec.container_id,
                partition_key=PartitionKey(path=spec.partition_key),
            )
            # Upserts run in parallel; the id contract is validated on the
            # main thread while submitting so a bad generator still fails
            # with a clear error before flooding the pool.
            futures = []
            with ThreadPoolExecutor(max_workers=_SEED_WORKERS) as pool:
                for row in spec.rows():
                    if "id" not in row:
                        raise ValueError(
                            f"row for container '{spec.container_id}' is missing required 'id' field"
                        )
                    futures.append(pool.submit(container.upsert_item, row))
                for future in futures:
                    future.result()  # surface the first upsert failure
            print(f"  Seeded {spec.container_id}: {len(futures)} items")
        print("Seed complete.")

    def _run_dry(self, specs: list[SeedSpec]) -> None:
        """Validate every generator against the seed contract, no Cosmos writes.

        Fails (SystemExit 1) on: partition key not starting with '/', a row
        missing 'id', duplicate ids within a container, or a row missing the
        partition-key field. Ensures a generated cosmos_seed.py that passes
        dry-run cannot fail the real seed for contract reasons.
        """
        print(f"Seed DRY RUN — validating {len(specs)} container spec(s), no Cosmos writes")
        failures: list[str] = []
        for spec in specs:
            if not spec.partition_key.startswith("/"):
                failures.append(
                    f"{spec.container_id}: partition_key must start with '/' "
                    f"(got {spec.partition_key!r})"
                )
                continue
            pk_field = spec.partition_key.lstrip("/").split("/")[0]
            seen_ids: set = set()
            count = 0
            container_failures = 0
            for row in spec.rows():
                count += 1
                if container_failures >= 3:
                    break  # enough evidence for this container
                if not isinstance(row, dict):
                    failures.append(f"{spec.container_id}: row {count} is not a dict")
                    container_failures += 1
                    continue
                if "id" not in row:
                    failures.append(f"{spec.container_id}: row {count} missing 'id'")
                    container_failures += 1
                elif row["id"] in seen_ids:
                    failures.append(
                        f"{spec.container_id}: duplicate id {row['id']!r} at row {count}"
                    )
                    container_failures += 1
                else:
                    seen_ids.add(row["id"])
                if pk_field not in row:
                    failures.append(
                        f"{spec.container_id}: row {count} missing partition-key "
                        f"field '{pk_field}'"
                    )
                    container_failures += 1
            status = "FAIL" if container_failures else "OK"
            print(f"  {status}: {spec.container_id} — {count} rows")
        if failures:
            print("Dry run FAILED:")
            for f in failures:
                print(f"  - {f}")
            raise SystemExit(1)
        print("Dry run passed — seed contract satisfied.")
