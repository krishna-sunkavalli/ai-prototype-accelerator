#!/usr/bin/env python3
"""
accelerator/scripts/deploy.py — Manifest-aware azd wrapper.

Wraps `azd up` so that the azd env always matches the current
`generated/build-state/manifest.json`. Prevents the class of failures
where a stale azd env deploys the whole stack to the wrong region /
resource group (KNOWN_ISSUES #1, #3).

Also handles the recurring AI Search Basic-SKU regional-capacity issue
(BACKLOG #2): on an `InsufficientResourcesAvailable` failure for the
Search service, it picks a nearby region from the built-in table, sets
`AZURE_SEARCH_LOCATION` via `azd env set`, and retries `azd up` once.

What the wrapper does, in order:

  1. Read `generated/build-state/manifest.json`.
  2. Ensure the target azd env exists (create it with `azd env new` if
     not).
  3. Select the target env and force AZURE_LOCATION / AZURE_RESOURCE_GROUP
     to the manifest values via `azd env set`.
  4. Set SKIP_PREPROV_WHATIF=true — preflight already validated the
     Bicep; the preprovision hook doesn't need to re-run what-if.
  5. Run `azd up --no-prompt`.
  6. If `azd up` fails and stderr contains
     `Microsoft.Search/.*InsufficientResources` — pick a nearby region,
     `azd env set AZURE_SEARCH_LOCATION`, and rerun `azd up` once. Only
     one auto-swap per invocation.

Exit codes:
    0  azd up succeeded
    1  azd up failed after any auto-recovery attempts
    2  environment / prerequisites problem (no az, no manifest, no azd)

Flags:
    --dry-run              Print each command instead of running it.
    --skip-search-swap     Disable the auto-swap on Search failures.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTO = ROOT / "generated" / "prototype"
MANIFEST = ROOT / "generated" / "build-state" / "manifest.json"

# Nearest-region fallbacks for AI Search when Basic SKU is capacity
# exhausted in the primary region. Ordered by network proximity so the
# swap keeps latency reasonable. Regions not listed fall back to eastus2.
_SEARCH_FALLBACK = {
    "eastus":            ["eastus2", "centralus", "northcentralus"],
    "eastus2":           ["eastus", "centralus", "northcentralus"],
    "westus":            ["westus2", "westus3", "centralus"],
    "westus2":           ["westus3", "westus", "centralus"],
    "westus3":           ["westus2", "westus", "centralus"],
    "centralus":         ["eastus2", "northcentralus", "westus3"],
    "northcentralus":    ["centralus", "eastus2", "westus3"],
    "southcentralus":    ["northcentralus", "eastus2", "westus3"],
    "canadacentral":     ["canadaeast", "eastus2"],
    "canadaeast":        ["canadacentral", "eastus2"],
    "northeurope":       ["westeurope", "uksouth", "swedencentral"],
    "westeurope":        ["northeurope", "uksouth", "swedencentral"],
    "uksouth":           ["westeurope", "northeurope"],
    "swedencentral":     ["westeurope", "northeurope"],
    "francecentral":     ["westeurope", "northeurope"],
    "germanywestcentral":["westeurope", "northeurope"],
    "southeastasia":     ["eastasia", "japaneast", "australiaeast"],
    "eastasia":          ["southeastasia", "japaneast"],
    "japaneast":         ["southeastasia", "eastasia"],
    "australiaeast":     ["southeastasia", "japaneast"],
}
_SEARCH_DEFAULT_FALLBACK = "eastus"

# stderr fragments that indicate the AI Search resource specifically hit
# `InsufficientResourcesAvailable` — not any other regional capacity
# error. Matching too broadly would auto-swap on unrelated failures.
_SEARCH_CAPACITY_MARKERS = (
    "microsoft.search",
    "search service",
    "search services",
    "hudson-hudson-search",  # example wording; substring match on "search" below is the real gate
)
_INSUFFICIENT_MARKER = "insufficientresourcesavailable"


def _fail(msg: str, code: int = 2) -> None:
    print(f"deploy: {msg}", file=sys.stderr)
    sys.exit(code)


def _which(name: str, ext: str = "") -> str | None:
    return shutil.which(name) or shutil.which(name + ext)


def _run(cmd: list[str], cwd: pathlib.Path | None = None, *, dry_run: bool = False,
         capture: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
    """subprocess.run with dry-run + working-directory support."""
    printable = " ".join(cmd)
    if dry_run:
        print(f"  [dry-run] {printable}")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    print(f"  $ {printable}")
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
        shell=False, timeout=timeout,
    )


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        _fail(f"manifest not found at {MANIFEST.relative_to(ROOT)}. "
              f"Run @devlead build (or spec-validator.py) first.")
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"manifest parse failed: {exc}")


def _existing_azd_envs(azd: str, cwd: pathlib.Path) -> list[str]:
    result = _run([azd, "env", "list", "-o", "json"], cwd=cwd, capture=True, timeout=30)
    if result.returncode != 0:
        return []
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [row.get("Name") or row.get("name") for row in rows if row]


def _align_azd_env(azd: str, manifest: dict, *, dry_run: bool) -> None:
    """Create / select / align the azd env with manifest values."""
    deployment = manifest.get("deployment", {})
    env_name = deployment.get("environmentName")
    location = deployment.get("location")
    rg = deployment.get("resourceGroup")
    subscription = (
        manifest.get("subscriptionId")
        or deployment.get("subscriptionId")
        or os.environ.get("AZURE_SUBSCRIPTION_ID")
    )
    if not env_name or not location or not rg:
        _fail("manifest.deployment is missing environmentName / location / resourceGroup")

    envs = _existing_azd_envs(azd, PROTO)
    if env_name not in envs:
        args = [azd, "env", "new", env_name, "--location", location]
        if subscription:
            args.extend(["--subscription", subscription])
        args.append("--no-prompt")
        result = _run(args, cwd=PROTO, timeout=90)
        if result.returncode != 0:
            _fail(f"azd env new {env_name} failed with exit {result.returncode}", code=1)
    else:
        result = _run([azd, "env", "select", env_name], cwd=PROTO, dry_run=dry_run, timeout=30)
        if result.returncode != 0:
            _fail(f"azd env select {env_name} failed with exit {result.returncode}", code=1)

    # Force AZURE_LOCATION + AZURE_RESOURCE_GROUP + SKIP_PREPROV_WHATIF
    # regardless of what a prior build (or the azd default) may have set.
    to_set = {
        "AZURE_LOCATION": location,
        "AZURE_RESOURCE_GROUP": rg,
        "SKIP_PREPROV_WHATIF": "true",
    }
    if subscription:
        to_set["AZURE_SUBSCRIPTION_ID"] = subscription
    for key, value in to_set.items():
        _run([azd, "env", "set", key, value], cwd=PROTO, dry_run=dry_run, timeout=30)


def _looks_like_search_capacity_error(stderr_lower: str) -> bool:
    if _INSUFFICIENT_MARKER not in stderr_lower:
        return False
    return "search" in stderr_lower


def _pick_search_fallback(current: str, tried: set[str]) -> str | None:
    candidates = _SEARCH_FALLBACK.get((current or "").lower(), [])
    for candidate in candidates + [_SEARCH_DEFAULT_FALLBACK]:
        if candidate and candidate not in tried:
            return candidate
    return None


def _run_azd_up(azd: str, *, dry_run: bool) -> subprocess.CompletedProcess:
    """Run `azd up --no-prompt` streaming output; also capture stderr so we
    can inspect it for known auto-recoverable errors."""
    print("  $ azd up --no-prompt")
    if dry_run:
        return subprocess.CompletedProcess([azd, "up"], 0, "", "")
    proc = subprocess.Popen(
        [azd, "up", "--no-prompt"],
        cwd=str(PROTO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    stderr_chunks: list[str] = []
    stdout_chunks: list[str] = []
    # Simple line-streamer that also mirrors stderr into the local buffer.
    import threading

    def _pump(stream, sink_buffer, mirror):
        for line in stream:
            sink_buffer.append(line)
            mirror.write(line)
            mirror.flush()

    t_out = threading.Thread(target=_pump, args=(proc.stdout, stdout_chunks, sys.stdout))
    t_err = threading.Thread(target=_pump, args=(proc.stderr, stderr_chunks, sys.stderr))
    t_out.start(); t_err.start()
    exit_code = proc.wait()
    t_out.join(); t_err.join()
    return subprocess.CompletedProcess(
        [azd, "up"], exit_code, "".join(stdout_chunks), "".join(stderr_chunks)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Manifest-aware azd up wrapper")
    parser.add_argument("--dry-run", action="store_true", help="Print commands but don't run them")
    parser.add_argument("--skip-search-swap", action="store_true",
                        help="Do not auto-swap AZURE_SEARCH_LOCATION on Search capacity failures")
    parser.add_argument("--max-search-swaps", type=int, default=2,
                        help="Max nearest-region swaps on Search capacity failure (default 2)")
    args = parser.parse_args()

    az = _which("az", ".cmd")
    if not az:
        _fail("Azure CLI (az) not on PATH — install and re-run")
    azd = _which("azd", ".exe")
    if not azd:
        _fail("Azure Developer CLI (azd) not on PATH — install from https://aka.ms/azd-install")
    if not PROTO.exists():
        _fail(f"generated/prototype missing — run @devlead build first")

    manifest = _load_manifest()
    print(f"deploy: aligning azd env from {MANIFEST.relative_to(ROOT)}")
    _align_azd_env(azd, manifest, dry_run=args.dry_run)

    tried_regions: set[str] = set()
    swap_attempts = 0

    while True:
        result = _run_azd_up(azd, dry_run=args.dry_run)
        if result.returncode == 0:
            print("deploy: azd up succeeded")
            return 0

        # azd prints the "Failed: Search service: ..." progress line to
        # stdout and the InsufficientResourcesAvailable error detail to
        # stderr — neither stream alone reliably contains both markers, so
        # scan the combined output.
        combined_lower = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
        if (
            args.skip_search_swap
            or swap_attempts >= args.max_search_swaps
            or not _looks_like_search_capacity_error(combined_lower)
        ):
            print(f"deploy: azd up failed with exit {result.returncode}", file=sys.stderr)
            return 1

        # Auto-swap Search region and retry once.
        current = os.environ.get("AZURE_SEARCH_LOCATION") or manifest.get("deployment", {}).get("location", "")
        # Read the current effective AZURE_SEARCH_LOCATION from azd env to
        # respect a manual override the user may have already set.
        result_env = _run(
            [azd, "env", "get-value", "AZURE_SEARCH_LOCATION"],
            cwd=PROTO, capture=True, timeout=30,
        )
        if result_env.returncode == 0:
            actual = (result_env.stdout or "").strip()
            if actual:
                current = actual
        tried_regions.add((current or "").lower())

        fallback = _pick_search_fallback(current, tried_regions)
        if not fallback:
            print("deploy: no more AI Search fallback regions to try", file=sys.stderr)
            return 1
        swap_attempts += 1
        print(
            f"deploy: AI Search capacity error in '{current}' — retrying with "
            f"AZURE_SEARCH_LOCATION={fallback} (attempt {swap_attempts}/"
            f"{args.max_search_swaps})"
        )
        _run(
            [azd, "env", "set", "AZURE_SEARCH_LOCATION", fallback],
            cwd=PROTO, dry_run=args.dry_run, timeout=30,
        )
        tried_regions.add(fallback.lower())


if __name__ == "__main__":
    sys.exit(main())
