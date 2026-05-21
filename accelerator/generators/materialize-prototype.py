#!/usr/bin/env python3
"""
accelerator/generators/materialize-prototype.py — Copy the maintained scaffold into generated/prototype.

This makes the generated prototype self-contained before step-specific generators
write their outputs. It copies scaffold-owned files from accelerator/templates/prototype
into generated/prototype without deleting any generated artifacts already present.
"""
from __future__ import annotations

import pathlib
import shutil

WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TEMPLATE_ROOT = WORKSPACE_ROOT / "accelerator" / "templates" / "prototype"
OUTPUT_ROOT = WORKSPACE_ROOT / "generated" / "prototype"
BUILD_STATE_ROOT = WORKSPACE_ROOT / "generated" / "build-state"


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    BUILD_STATE_ROOT.mkdir(parents=True, exist_ok=True)

    for relative_dir in [
        pathlib.Path("agents") / "specialists",
        pathlib.Path("agents") / "knowledge",
        pathlib.Path("db"),
    ]:
        target_dir = OUTPUT_ROOT / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)

    for keep_file in [
        OUTPUT_ROOT / "agents" / "specialists" / ".gitkeep",
        OUTPUT_ROOT / "agents" / "knowledge" / ".gitkeep",
        BUILD_STATE_ROOT / ".gitkeep",
    ]:
        keep_file.touch(exist_ok=True)

    if not TEMPLATE_ROOT.exists():
        raise SystemExit(f"Template root not found: {TEMPLATE_ROOT}")

    copied = 0
    for source in TEMPLATE_ROOT.rglob("*"):
        relative = source.relative_to(TEMPLATE_ROOT)
        destination = OUTPUT_ROOT / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1

    print("materialize-prototype.py — Scaffold copied")
    print(f"  Source : {TEMPLATE_ROOT.relative_to(WORKSPACE_ROOT)}")
    print(f"  Output : {OUTPUT_ROOT.relative_to(WORKSPACE_ROOT)}")
    print(f"  Files  : {copied}")


if __name__ == "__main__":
    main()
