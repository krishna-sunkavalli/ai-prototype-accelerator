#!/usr/bin/env python3
"""
db/_seed_lib.py — Reusable Cosmos DB seed plumbing.

Static helper shipped with every prototype. Owns:
    - CosmosClient construction with AzureCliCredential (local) / DefaultAzureCredential (cloud)
    - Container creation with the correct partition-key path
    - Idempotent upserts (re-running the seed never duplicates)
    - Container-name → row-count reporting in a consistent format

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
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Iterable

from azure.cosmos import CosmosClient, PartitionKey
from azure.identity import AzureCliCredential, DefaultAzureCredential


def get_credential():
    """Prefer AzureCliCredential locally; fall back to DefaultAzureCredential."""
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


def get_client(endpoint: str | None = None) -> CosmosClient:
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
        self.database_name = database_name or os.environ["AZURE_COSMOS_DATABASE"]
        self.client = get_client(endpoint)
        self.db = self.client.get_database_client(self.database_name)

    def run(self, specs: list[SeedSpec]) -> None:
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
            count = 0
            for row in spec.rows():
                if "id" not in row:
                    raise ValueError(
                        f"row for container '{spec.container_id}' is missing required 'id' field"
                    )
                container.upsert_item(row)
                count += 1
            print(f"  Seeded {spec.container_id}: {count} items")
        print("Seed complete.")
