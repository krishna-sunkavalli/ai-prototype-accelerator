#!/usr/bin/env python3
"""
accelerator/generators/model_catalog.py — Single source of truth for Azure AI
Foundry model deployment metadata used across the accelerator.

Why this exists:
    Earlier the model-version table lived in fill-templates.py only. If any
    other consumer (spec-validator, register_agents, hooks) needs to know
    "what version corresponds to gpt-4o by default" it must reach for the
    same table. Drift here causes real deployment failures (#11 in
    KNOWN_ISSUES). Centralising in one module — imported, never copied —
    eliminates that class of bug.

Update protocol:
    When a new GA model version ships, edit MODEL_VERSION_DEFAULTS below and
    nothing else. All downstream consumers pick up the change.
"""
from __future__ import annotations

# Default model versions used when spec.yaml omits an explicit `version` for
# a model deployment. Update here when a new GA model version ships.
MODEL_VERSION_DEFAULTS: dict[str, str] = {
    "gpt-4o": "2024-11-20",
    "gpt-4o-mini": "2024-07-18",
    "gpt-4.1": "2025-04-14",
    "gpt-4.1-mini": "2025-04-14",
    "text-embedding-3-large": "1",
    "text-embedding-3-small": "1",
}

# Default SKU when a model deployment in spec.yaml omits `sku`.
DEFAULT_SKU = "GlobalStandard"


def resolve_version(model: str, explicit: str | None = None) -> str:
    """Return the version to use for a model, preferring an explicit override."""
    if explicit:
        return explicit
    return MODEL_VERSION_DEFAULTS.get(model, "latest")


def resolve_sku(explicit: str | None = None) -> str:
    return explicit or DEFAULT_SKU
