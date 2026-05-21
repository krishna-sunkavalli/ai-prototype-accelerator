#!/usr/bin/env python3
"""
accelerator/scripts/clear-sentinels.py — Atomic, auditable sentinel cleaner.

Replaces the prose instruction "delete every generated/build-state/*.done
sentinel" that previously lived inside devlead.agent.md. Hand-rolling deletes
from an agent prompt is destructive and untracked; this script makes the
operation explicit, listable, and reviewable.

Usage:
    # Show what would be cleared without touching anything
    py -3 accelerator/scripts/clear-sentinels.py --list

    # Clear every step sentinel (spec changed → full rebuild)
    py -3 accelerator/scripts/clear-sentinels.py --all

    # Clear a specific step's sentinel plus the manifest sentinel
    py -3 accelerator/scripts/clear-sentinels.py --step 3

    # Clear several steps explicitly
    py -3 accelerator/scripts/clear-sentinels.py --step 3 --step 4 --step 7

The manifest.json itself is NEVER deleted — only the *.done sentinels under
generated/build-state/. The script exits non-zero if no mode flag is given.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE = ROOT / "generated" / "build-state"

# Step number → sentinel filename. Keep in sync with devlead.agent.md.
STEP_SENTINELS = {
    1: "01-spec-validator.done",
    2: "02-infra-agent.done",
    3: "03-data-agent.done",
    4: "04-agents-builder.done",
    5: "05-docs-agent.done",
    6: "06-backend-agent.done",
    7: "07-hook-agent.done",
}


def _list_sentinels() -> list[pathlib.Path]:
    if not STATE.exists():
        return []
    return sorted(STATE.glob("*.done"))


def _delete(paths: list[pathlib.Path]) -> int:
    removed = 0
    for p in paths:
        if p.exists():
            p.unlink()
            print(f"  removed: {p.relative_to(ROOT)}")
            removed += 1
    return removed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="clear-sentinels.py")
    parser.add_argument("--list", action="store_true", help="List sentinels, do not delete")
    parser.add_argument("--all", action="store_true", help="Delete every *.done sentinel")
    parser.add_argument(
        "--step",
        action="append",
        type=int,
        default=[],
        help="Delete a specific step sentinel (repeatable). Valid: 1-7.",
    )
    args = parser.parse_args(argv)

    if not (args.list or args.all or args.step):
        parser.error("specify --list, --all, or one or more --step N")

    existing = _list_sentinels()

    if args.list:
        if not existing:
            print("No *.done sentinels under generated/build-state/.")
            return 0
        print("Sentinels currently on disk:")
        for p in existing:
            print(f"  - {p.relative_to(ROOT)}")
        return 0

    targets: list[pathlib.Path] = []
    if args.all:
        targets = existing
    else:
        for s in args.step:
            if s not in STEP_SENTINELS:
                print(f"FAIL: unknown step {s}; valid: {sorted(STEP_SENTINELS)}", file=sys.stderr)
                return 1
            targets.append(STATE / STEP_SENTINELS[s])

    if not targets:
        print("Nothing to remove.")
        return 0

    print(f"Removing {len(targets)} sentinel(s):")
    removed = _delete(targets)
    print(f"Done. {removed} file(s) removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
