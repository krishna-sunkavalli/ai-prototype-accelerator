#!/usr/bin/env python3
"""
accelerator/generators/preflight_env.py — Phase 1 preflight.

Runs immediately after Step 1 (spec-validator) and before any LLM specialist
work in Steps 2-6. Its job is to fail fast on:

  - Authentication (az login expired / azd not installed)
  - Active subscription drift vs manifest
  - Active azd env drift vs manifest (name / AZURE_LOCATION / AZURE_RESOURCE_GROUP)
  - Regional capacity + quota (model TPM, AI Search SKU, Cosmos region + AZ)
  - Provider registration (Microsoft.App)
  - Foundry subdomain reservation (soft-deleted account holds the name)

Everything checked here depends ONLY on `generated/build-state/manifest.json`
and the state of the Azure subscription. It does not need any of the emitted
prototype artifacts, so failing here saves the 5-10 min of LLM work that
Steps 2-6 spend generating agents, docs, config, and hooks.

Artifact-level checks (Bicep build/what-if, Python compile, YAML/JSON parse,
unresolved placeholders, tool resolution, seed dry-run, agent outputs,
knowledge docs, brand contrast) still run in the phase-2 preflight.py after
Step 7.

Contract: exit 0 on success, exit 1 on any hard failure, print each failure
on its own line under a `FAILED — N issue(s):` banner.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


def _load_preflight_module():
    """Import preflight.py as a module without executing its main()."""
    here = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "preflight_lib", here / "preflight.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_parallel(checks: list) -> list[str]:
    """Run independent az-CLI checks concurrently and collate their errors.

    Each check mutates its own local list, so there's no shared-state race.
    ThreadPoolExecutor is safe here because every check is I/O-bound
    (`subprocess.run` on `az`), not CPU-bound.
    """
    aggregated: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(checks), 8)) as pool:
        futures = {}
        for fn in checks:
            local: list[str] = []
            futures[pool.submit(fn, local)] = local
        for fut in as_completed(futures):
            fut.result()  # re-raise any exception
            aggregated.extend(futures[fut])
    return aggregated


def main() -> None:
    m = _load_preflight_module()

    print("preflight_env.py — Phase 1 (environment, auth, capacity)")
    errors: list[str] = []

    # Order matters: fail on tooling/auth before anything hits Azure so the
    # user gets one clear remediation rather than a cascade of "not logged in"
    # noise from every capacity probe.
    m.check_azd_installed(errors)
    m.check_az_logged_in(errors)

    # If az isn't installed or we're not logged in, the Azure-side probes will
    # all soft-skip and produce nothing useful — cut the run short.
    if errors:
        _fail(errors)

    # Manifest schema is a pure-python check; run it inline.
    m.check_manifest_schema(errors)

    # The remaining probes each spawn one `az` subprocess. They read from the
    # same manifest and don't mutate shared state, so run them concurrently.
    parallel_checks = [
        m.check_az_subscription_matches_manifest,
        m.check_azd_env_matches_manifest,
        m.check_provider_registered,
        m.check_foundry_subdomain_available,
        m.check_global_name_collisions,
        m.check_model_quota,
        m.check_search_sku_capacity,
        m.check_cosmos_regional_capacity,
        m.check_container_image_not_placeholder,
    ]
    errors.extend(_run_parallel(parallel_checks))

    if errors:
        _fail(errors)
    print("  OK — environment and capacity checks passed.")


def _fail(errors: list[str]) -> None:
    print(f"  FAILED — {len(errors)} issue(s):", file=sys.stderr)
    for e in errors:
        print(f"    - {e}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
