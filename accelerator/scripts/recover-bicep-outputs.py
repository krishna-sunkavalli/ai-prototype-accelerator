#!/usr/bin/env python3
"""
accelerator/scripts/recover-bicep-outputs.py — Restore azd env values from Bicep outputs.

When `azd up` reports a provisioning failure but the underlying ARM
deployment has still emitted its outputs (e.g. one late model deployment
returned `RequestConflict` and Bicep aborted after all other resources
succeeded, Hudson Advisors build 2026-07-13), azd never writes those
outputs to `.azure/<env>/.env`. Subsequent `azd deploy` then fails
because `AZURE_CONTAINER_REGISTRY_ENDPOINT`, `AZURE_AI_PROJECT_ENDPOINT`,
`AZURE_CLIENT_ID`, and the rest are missing.

This helper reads the manifest to locate the target resource group and
subscription, finds the latest ARM deployment in that RG, extracts the
UPPER_SNAKE_CASE outputs (which are the ones `azd env` cares about), and
calls `azd env set` for each one.

Usage:
    py -3 accelerator/scripts/recover-bicep-outputs.py [--rg <name>] [--dry-run]

If --rg is omitted, the manifest's `deployment.resourceGroup` is used.
--dry-run prints what would be set without invoking `azd env set`.

Exit codes:
    0  outputs recovered (or none needed)
    1  manifest missing / ARM deployment not found
    2  az CLI unavailable
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "generated" / "build-state" / "manifest.json"
PROTO = ROOT / "generated" / "prototype"


def _fail(msg: str, code: int = 1) -> None:
    print(f"recover-bicep-outputs: {msg}", file=sys.stderr)
    sys.exit(code)


def _az() -> str:
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        _fail("Azure CLI (az) not on PATH", code=2)
    return az


def _azd() -> str:
    azd = shutil.which("azd") or shutil.which("azd.exe")
    if not azd:
        _fail("Azure Developer CLI (azd) not on PATH", code=2)
    return azd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rg", help="Resource group override (default: manifest.deployment.resourceGroup)")
    parser.add_argument("--dry-run", action="store_true", help="Print outputs but don't run azd env set")
    args = parser.parse_args()

    if not MANIFEST.exists():
        _fail(f"manifest not found at {MANIFEST.relative_to(ROOT)}", code=1)
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"manifest parse failed: {e}", code=1)

    deployment = manifest.get("deployment", {})
    rg = args.rg or deployment.get("resourceGroup")
    subscription = (
        manifest.get("subscriptionId")
        or deployment.get("subscriptionId")
        or os.environ.get("AZURE_SUBSCRIPTION_ID")
    )
    if not rg:
        _fail("manifest is missing deployment.resourceGroup and --rg was not passed", code=1)

    az = _az()

    # Find the latest ARM deployment in the RG. azd names its deployments
    # "<envName>-<epoch>" but we can't assume a naming convention across
    # tool versions, so pick the most recent by timestamp.
    print(f"Locating latest ARM deployment in resource group '{rg}'...")
    list_cmd = [
        az, "deployment", "group", "list",
        "--resource-group", rg,
        "--query", "sort_by([], &properties.timestamp)[-1].name",
        "-o", "tsv", "--only-show-errors",
    ]
    if subscription:
        list_cmd.extend(["--subscription", subscription])
    try:
        listing = subprocess.run(list_cmd, capture_output=True, text=True, shell=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        _fail(f"az deployment group list failed: {e}", code=1)
    if listing.returncode != 0:
        _fail(
            f"az deployment group list failed: {(listing.stderr or listing.stdout).strip()}",
            code=1,
        )
    deployment_name = (listing.stdout or "").strip()
    if not deployment_name:
        _fail(f"no ARM deployments found in resource group '{rg}'", code=1)

    print(f"Found deployment: {deployment_name}")

    # Fetch its outputs
    show_cmd = [
        az, "deployment", "group", "show",
        "--resource-group", rg,
        "--name", deployment_name,
        "--query", "properties.outputs",
        "-o", "json", "--only-show-errors",
    ]
    if subscription:
        show_cmd.extend(["--subscription", subscription])
    try:
        shown = subprocess.run(show_cmd, capture_output=True, text=True, shell=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        _fail(f"az deployment group show failed: {e}", code=1)
    if shown.returncode != 0:
        _fail(
            f"az deployment group show failed: {(shown.stderr or shown.stdout).strip()}",
            code=1,
        )
    try:
        outputs = json.loads(shown.stdout or "{}")
    except json.JSONDecodeError:
        _fail("could not parse deployment outputs as JSON", code=1)

    if not isinstance(outputs, dict) or not outputs:
        _fail("deployment has no outputs; nothing to recover", code=1)

    # azd env consumes UPPER_SNAKE_CASE names. Filter to those keys.
    upper_outputs: dict[str, str] = {}
    for key, value in outputs.items():
        if not isinstance(key, str) or key != key.upper():
            continue
        if not isinstance(value, dict):
            continue
        val = value.get("value")
        if val is None:
            continue
        upper_outputs[key] = str(val)

    if not upper_outputs:
        print("No UPPER_SNAKE_CASE outputs found; nothing to recover.")
        return

    print(f"Recovering {len(upper_outputs)} outputs into azd env...")
    if args.dry_run:
        for k, v in sorted(upper_outputs.items()):
            print(f"  {k} = {v}")
        return

    azd = _azd()
    os.chdir(PROTO)
    for k, v in sorted(upper_outputs.items()):
        try:
            result = subprocess.run(
                [azd, "env", "set", k, v],
                capture_output=True, text=True, shell=False, timeout=30,
            )
            if result.returncode != 0:
                print(f"  WARN: azd env set {k} failed: {(result.stderr or result.stdout).strip()}", file=sys.stderr)
            else:
                print(f"  OK: {k}")
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"  WARN: azd env set {k} raised: {e}", file=sys.stderr)

    print("Recovery complete.")


if __name__ == "__main__":
    main()
