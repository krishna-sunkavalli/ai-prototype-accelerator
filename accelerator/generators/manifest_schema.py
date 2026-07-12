#!/usr/bin/env python3
"""
accelerator/generators/manifest_schema.py — Authoritative shape of manifest.json.

This module declares the schema for `generated/build-state/manifest.json` and
provides a pure-stdlib validator. Every downstream consumer (fill-templates,
register_agents, hooks, agents-builder specialist) must read the manifest
through this contract.

Rationale:
    Earlier, spec-validator wrote the manifest and consumers reached in for
    fields by string. A field rename (or a typo on the read side) only blew
    up at runtime — sometimes mid-deploy. This module makes the shape
    explicit and validation a single function call.

Validation policy:
    - "required" means: the field must be present and non-empty.
    - Type checks are conservative (str / list / dict / int / bool).
    - This validator is stdlib-only on purpose; we don't want a `jsonschema`
      package install gate on the accelerator's first run.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any


# ── Schema declaration ──────────────────────────────────────────────────────
# Each entry is (path, expected_type, required). Path is dotted, list items
# are denoted with [].
SCHEMA: list[tuple[str, type | tuple[type, ...], bool]] = [
    ("specChecksum", str, True),
    ("buildTimestamp", str, True),

    ("customer", dict, True),
    ("customer.slug", str, True),
    ("customer.name", str, True),
    ("customer.website", str, False),

    ("deployment", dict, True),
    ("deployment.environmentName", str, True),
    ("deployment.resourceGroup", str, True),
    ("deployment.location", str, True),
    ("deployment.suffix", str, True),
    ("deployment.customerShort", str, True),
    ("deployment.demoTheme", str, True),

    ("resources", dict, True),
    ("resources.cosmosAccountName", str, True),
    ("resources.cosmosDatabaseName", str, True),
    ("resources.searchIndexName", str, True),
    ("resources.storageAccount", str, True),
    ("resources.miName", str, True),
    ("resources.acrName", str, True),

    ("branding", dict, True),
    ("branding.agentName", str, True),
    ("branding.primaryColor", str, True),
    ("branding.accentColor", str, True),
    ("branding.useCaseTitle", str, True),
    ("branding.starterQuestions", list, True),
    ("branding.personaName", str, True),
    ("branding.personaRole", str, True),

    ("tables", list, True),
    ("tables[].name", str, True),
    ("tables[].partitionKey", str, True),
    ("tables[].seedCount", int, True),

    ("agents", list, True),
    ("agents[].name", str, True),
    ("agents[].model", str, True),
    ("agents[].tools", list, False),

    ("modelDeployments", list, True),
    ("modelDeployments[].deploymentName", str, True),
    ("modelDeployments[].model", str, True),
    ("modelDeployments[].capacity", int, True),

    ("documents", list, True),

    ("foundryIq", dict, True),
    ("foundryIq.indexName", str, True),

    ("aiLocation", str, True),
]


def _get(obj: Any, dotted: str) -> tuple[bool, Any]:
    """Walk a dotted path with [] list-item segments. Returns (found, value)."""
    cur = obj
    parts = dotted.split(".")
    for part in parts:
        if part.endswith("[]"):
            field = part[:-2]
            if field:
                if not isinstance(cur, dict) or field not in cur:
                    return (False, None)
                cur = cur[field]
            if not isinstance(cur, list):
                return (False, None)
            # Validate every list item against the remainder of the path.
            return (True, cur)
        else:
            if not isinstance(cur, dict) or part not in cur:
                return (False, None)
            cur = cur[part]
    return (True, cur)


def validate(manifest: dict) -> list[str]:
    """Validate manifest. Returns a list of human-readable error strings.

    An empty list means the manifest conforms.
    """
    errors: list[str] = []

    for path, expected_type, required in SCHEMA:
        if "[]" in path:
            # Validate every item in the parent list.
            head, _, tail = path.partition("[].")
            found, parent = _get(manifest, head + "[]")
            if not found:
                if required:
                    errors.append(f"missing required path: {head}")
                continue
            for i, item in enumerate(parent):
                ok, value = _get(item, tail)
                if not ok:
                    if required:
                        errors.append(f"{head}[{i}].{tail}: missing")
                    continue
                if not isinstance(value, expected_type):
                    errors.append(
                        f"{head}[{i}].{tail}: expected {expected_type.__name__}, got {type(value).__name__}"
                    )
                if required and isinstance(value, (str, list, dict)) and not value:
                    errors.append(f"{head}[{i}].{tail}: required but empty")
            continue

        found, value = _get(manifest, path)
        if not found:
            if required:
                errors.append(f"missing required path: {path}")
            continue
        if not isinstance(value, expected_type):
            errors.append(
                f"{path}: expected {expected_type.__name__}, got {type(value).__name__}"
            )
        if required and isinstance(value, (str, list, dict)) and not value:
            errors.append(f"{path}: required but empty")

    # Cross-field rule: every agent's model must reference a known deployment.
    deployments = {
        d.get("deploymentName") for d in manifest.get("modelDeployments", []) or []
    }
    for i, a in enumerate(manifest.get("agents", []) or []):
        if a.get("model") and a["model"] not in deployments:
            errors.append(
                f"agents[{i}] '{a.get('name')}': model "
                f"'{a.get('model')}' not in modelDeployments"
            )

    return errors


def load_and_validate(manifest_path: pathlib.Path) -> dict:
    """Load + validate or raise SystemExit with a clear message."""
    if not manifest_path.exists():
        raise SystemExit(
            f"manifest.json not found at {manifest_path}. "
            "Run spec-validator (@devlead build) first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = validate(manifest)
    if errors:
        msg = "manifest.json failed schema validation:\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        raise SystemExit(msg)
    return manifest


if __name__ == "__main__":
    import sys
    root = pathlib.Path(__file__).resolve().parents[2]
    path = root / "generated" / "build-state" / "manifest.json"
    try:
        load_and_validate(path)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(f"OK: {path.relative_to(root)} conforms to schema.")
