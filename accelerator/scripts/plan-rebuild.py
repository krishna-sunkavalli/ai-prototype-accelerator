#!/usr/bin/env python3
"""
accelerator/scripts/plan-rebuild.py — Compute the incremental rebuild plan.

Run AFTER spec-validator has refreshed generated/build-state/manifest.json.
Compares every step sentinel's recorded input fingerprint against the current
manifest and prints which steps are fresh (skip) and which are stale (rerun).

The devlead uses this instead of clearing all sentinels on a spec change:
a branding-only edit reruns just the hydration steps; a new table reruns
data + agents; and so on. Exit code is always 0 — the plan is advisory;
staleness is re-checked per step by sentinels.is_stale() during the build.

Usage:
    py -3 accelerator/scripts/plan-rebuild.py          # human-readable table
    py -3 accelerator/scripts/plan-rebuild.py --json   # machine-readable
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE = ROOT / "generated" / "build-state"
MANIFEST_PATH = STATE / "manifest.json"

sys.path.insert(0, str(ROOT / "accelerator" / "generators"))
import sentinels  # noqa: E402

STEPS: list[tuple[str, str, str]] = [
    ("01", "spec-validator", "Validate spec"),
    ("02", "infra-agent", "Build infrastructure"),
    ("03", "data-agent", "Seed sample data"),
    ("04", "agents-builder", "Configure AI agents"),
    ("05", "docs-agent", "Generate knowledge docs"),
    ("06", "backend-agent", "Configure backend"),
    ("07", "hook-agent", "Write deploy hooks"),
]


def main() -> None:
    as_json = "--json" in sys.argv

    if not MANIFEST_PATH.exists():
        msg = "manifest.json not found — run spec-validator first; full build required."
        print(json.dumps({"error": msg}) if as_json else f"ERROR: {msg}")
        return
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    checksum = manifest.get("specChecksum", "")

    plan: list[dict] = []
    for num, name, label in STEPS:
        sentinel = STATE / f"{num}-{name}.done"
        payload = sentinels.read(sentinel) or {}
        outputs = [pathlib.Path(p) for p in payload.get("outputs", [])]
        stale, reason = sentinels.is_stale(sentinel, checksum, outputs, manifest=manifest)
        plan.append({
            "step": int(num),
            "name": name,
            "label": label,
            "action": "rerun" if stale else "skip",
            "reason": reason,
        })

    if as_json:
        print(json.dumps({"plan": plan}, indent=2))
        return

    print("Incremental rebuild plan")
    print(f"  Manifest checksum: {checksum}")
    for entry in plan:
        glyph = "RERUN" if entry["action"] == "rerun" else "skip "
        print(f"  [{glyph}] step {entry['step']} {entry['label']:<24} ({entry['reason']})")
    stale_steps = [str(e["step"]) for e in plan if e["action"] == "rerun"]
    if stale_steps:
        print(f"  → steps to run: {', '.join(stale_steps)}")
    else:
        print("  → everything fresh; proceed to preflight + deploy.")


if __name__ == "__main__":
    main()
