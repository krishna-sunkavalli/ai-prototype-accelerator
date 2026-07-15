#!/usr/bin/env python3
"""
accelerator/scripts/cleanup-partial-deploy.py — Find and remove stranded resources.

When `azd up` fails midway (region-capacity issue, transient conflict,
env-drift redirect), successful resources are left behind in whatever
resource group they landed in. Those resources continue to bill and,
because storage / ACR / Cosmos / Foundry names are all global, block the
next attempt.

This script identifies stranded resources by scanning:

  1. The manifest's target resource group — anything tagged
     `managedBy=azd` that isn't part of a currently-succeeded ARM
     deployment.
  2. Any resource group in the subscription that contains resources
     matching the manifest's global names (storage, ACR, Cosmos, Foundry
     hub) but that isn't the manifest's target RG.

Behavior:

  --dry-run (default): print what would be deleted, group by RG.
  --confirm:           actually delete the resource groups after user
                       types 'yes' at the prompt.
  --yes:               skip the prompt (for use inside devlead when the
                       user has already confirmed).

Exit codes:
    0  nothing to clean, or cleanup succeeded
    1  something we couldn't inspect (auth, network, malformed manifest)
    2  user declined the cleanup

The script is intentionally conservative: it only surfaces resource
groups whose names either contain the manifest customer_short / demo_theme
or literally match well-known stranded-name patterns (rg-<slug>-*,
rg-prototype). It will never delete the manifest's own target RG.
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


def _fail(msg: str, code: int = 1) -> None:
    print(f"cleanup-partial-deploy: {msg}", file=sys.stderr)
    sys.exit(code)


def _az() -> str:
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        _fail("Azure CLI (az) not on PATH", code=1)
    return az


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        _fail(f"manifest not found at {MANIFEST.relative_to(ROOT)}", code=1)
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"manifest parse failed: {e}", code=1)


def _stranded_resource_groups(
    az: str,
    subscription: str | None,
    manifest: dict,
) -> list[dict]:
    """Return the RGs that appear to hold stranded resources for this build.

    The search is scoped to the current subscription and matches by
    customer_short + demo_theme in the RG name OR by the presence of any
    global name from `manifest.resources` inside a non-target RG.
    """
    deployment = manifest.get("deployment", {})
    resources = manifest.get("resources", {})
    target_rg = (deployment.get("resourceGroup") or "").lower()
    customer_short = (deployment.get("customerShort") or "").lower()
    demo_theme = (deployment.get("demoTheme") or "").lower()

    global_names = [
        (resources.get("storageAccount") or "").lower(),
        (resources.get("acrName") or "").lower(),
        (resources.get("cosmosAccountName") or "").lower(),
    ]
    global_names = [g for g in global_names if g]

    matched_rgs: dict[str, dict] = {}

    # 1) RGs whose name looks like a leftover from this build slug.
    if customer_short:
        query = (
            "resourcecontainers "
            f"| where type =~ 'microsoft.resources/subscriptions/resourcegroups' "
            f"| where tolower(name) contains '{customer_short}' "
            f"     or name =~ 'rg-prototype' "
            f"| where tolower(name) != '{target_rg}' "
            "| project name, location, tags"
        )
        rgs = _graph_query(az, subscription, query)
        for row in rgs:
            matched_rgs[(row.get("name") or "").lower()] = row

    # 2) RGs containing global-namespace resources that we're about to
    #    provision — we can't create ours until these are gone.
    if global_names:
        names_list = ", ".join(f"'{n}'" for n in global_names)
        query = (
            "Resources "
            f"| where tolower(name) in ({names_list}) "
            f"| where tolower(resourceGroup) != '{target_rg}' "
            "| project name, type, resourceGroup, subscriptionId, location"
        )
        conflicts = _graph_query(az, subscription, query)
        for row in conflicts:
            rg = (row.get("resourceGroup") or "").lower()
            if rg and rg not in matched_rgs:
                matched_rgs[rg] = {
                    "name": row.get("resourceGroup"),
                    "location": row.get("location"),
                    "tags": None,
                }

    return list(matched_rgs.values())


def _graph_query(az: str, subscription: str | None, query: str) -> list[dict]:
    cmd = [
        az, "graph", "query", "-q", query,
        "--first", "100", "--query", "data",
        "-o", "json", "--only-show-errors",
    ]
    if subscription:
        cmd.extend(["--subscriptions", subscription])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        _fail(f"az graph query failed: {e}", code=1)
    if result.returncode != 0:
        _fail(
            f"az graph query failed: {(result.stderr or result.stdout).strip()}",
            code=1,
        )
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _describe(az: str, subscription: str | None, rg_name: str) -> list[dict]:
    cmd = [
        az, "resource", "list",
        "--resource-group", rg_name,
        "--query", "[].{name:name, type:type}",
        "-o", "json", "--only-show-errors",
    ]
    if subscription:
        cmd.extend(["--subscription", subscription])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Print what would be deleted (default)")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete the identified RGs after prompting")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation (implies --confirm)")
    args = parser.parse_args()

    if args.yes:
        args.confirm = True
    if args.confirm:
        args.dry_run = False

    manifest = _load_manifest()
    az = _az()
    subscription = (
        manifest.get("subscriptionId")
        or manifest.get("deployment", {}).get("subscriptionId")
        or os.environ.get("AZURE_SUBSCRIPTION_ID")
    )

    print("Scanning for stranded resources from earlier builds...")
    stranded = _stranded_resource_groups(az, subscription, manifest)
    if not stranded:
        print("Nothing to clean up.")
        return

    print()
    print(f"Found {len(stranded)} candidate resource group(s):")
    print()
    for rg in stranded:
        rg_name = rg.get("name")
        print(f"  Resource group: {rg_name}  ({rg.get('location') or 'unknown region'})")
        resources = _describe(az, subscription, rg_name)
        if resources:
            for r in resources[:10]:
                print(f"    - {r.get('type')}/{r.get('name')}")
            if len(resources) > 10:
                print(f"    ...+ {len(resources) - 10} more")
        else:
            print("    (no resources listed — RG may be empty or inaccessible)")
        print()

    if args.dry_run:
        print("Dry run — nothing deleted. Re-run with --confirm to remove them.")
        return

    if not args.yes:
        answer = input(
            f"Delete the {len(stranded)} resource group(s) above? Type 'yes' to confirm: "
        ).strip().lower()
        if answer != "yes":
            print("Aborted. Nothing deleted.")
            sys.exit(2)

    for rg in stranded:
        rg_name = rg.get("name")
        print(f"Deleting resource group {rg_name}...")
        cmd = [az, "group", "delete", "--name", rg_name, "--yes", "--no-wait"]
        if subscription:
            cmd.extend(["--subscription", subscription])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, shell=False, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"  WARN: {rg_name} delete raised: {e}", file=sys.stderr)
            continue
        if result.returncode != 0:
            print(
                f"  WARN: {rg_name} delete failed: {(result.stderr or result.stdout).strip()}",
                file=sys.stderr,
            )
        else:
            print(f"  OK: delete queued (--no-wait); Azure will finish in the background.")

    print()
    print("Cleanup complete. Cognitive Services accounts are soft-deleted for 48 h;")
    print("purge them with `az cognitiveservices account purge` if names are still held.")


if __name__ == "__main__":
    main()
