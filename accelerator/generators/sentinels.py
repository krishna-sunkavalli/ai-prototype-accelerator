#!/usr/bin/env python3
"""
accelerator/generators/sentinels.py — Build sentinel helpers.

A "sentinel" is a file under generated/build-state/<NN>-<step>.done that
marks a build step complete. Earlier, sentinels were just timestamps. That
let `@devlead build resume` succeed even when the manifest had changed since
the step ran, or when someone hand-edited the output. Both are silent
correctness failures.

This module records, alongside the timestamp:
    - The manifest specChecksum that produced the step's outputs.
    - A SHA-256 over the step's declared output files.

`is_stale` returns True when EITHER changed, signalling that the resume
controller should re-run the step rather than honouring the sentinel.

Incremental rebuild:
    Sentinels additionally record an `inputsHash` — a fingerprint of only
    the manifest sections the step actually consumes (see STEP_INPUTS).
    When the spec changes, is_stale() compares per-step input fingerprints
    instead of the global specChecksum, so a branding-only edit reruns the
    hydration steps while leaving seed data, agents, and docs untouched.
    Over-invalidation is safe; under-invalidation is a bug — when in doubt
    a step lists more inputs than it strictly needs.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import datetime, timezone

# Manifest sections each build step consumes, keyed by the two-digit step
# prefix of its sentinel filename (02-infra-agent.done → "02"). Dotted paths
# select a subsection. "*" means the whole manifest (minus volatile fields).
STEP_INPUTS: dict[str, list[str]] = {
    "01": ["*"],
    # infra: bicepparam carries deployment, resource names, branding env
    # vars, starter questions, and model deployments.
    "02": ["customer", "deployment", "resources", "branding",
           "modelDeployments", "foundryIq", "aiLocation", "mockApiEnabled"],
    # data: seed rows are derived from the table schemas + customer flavour.
    "03": ["customer", "tables"],
    # agents: prompts embed table schemas, skills, docs, and the agent name.
    "04": ["customer", "agents", "tables", "branding.agentName",
           "documents", "mockApiEnabled"],
    # docs: knowledge content follows the document specs + use case.
    "05": ["customer", "documentSpecs", "branding.useCaseTitle", "foundryIq"],
    # backend config: branding, starter questions, agent/table names, mock API.
    "06": ["customer", "deployment", "resources", "branding", "agents",
           "tables", "mockApiEnabled", "mockApiEndpoints"],
    # hooks: document upload arrays + deployment/resource targets.
    "07": ["customer", "deployment", "resources", "documents", "modelDeployments"],
}

# Fields that change on every validator run and must never affect fingerprints.
_VOLATILE_FIELDS = ("buildTimestamp", "specChecksum")


def _get_path(manifest: dict, dotted: str):
    cur = manifest
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def step_from_sentinel(sentinel_path: pathlib.Path) -> str | None:
    """Extract the two-digit step prefix from a sentinel filename."""
    m = re.match(r"^(\d{2})-", sentinel_path.name)
    return m.group(1) if m else None


def step_inputs_hash(manifest: dict, step: str) -> str:
    """Fingerprint of the manifest sections a step consumes."""
    paths = STEP_INPUTS.get(step, ["*"])
    if paths == ["*"]:
        subset = {k: v for k, v in manifest.items() if k not in _VOLATILE_FIELDS}
    else:
        subset = {p: _get_path(manifest, p) for p in paths}
    blob = json.dumps(subset, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _hash_files(paths: list[pathlib.Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        if not p.exists():
            h.update(b"<missing>")
            h.update(str(p).encode("utf-8"))
            continue
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    h.update(str(f.relative_to(p)).encode("utf-8"))
                    h.update(f.read_bytes())
        else:
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def write(
    sentinel_path: pathlib.Path,
    spec_checksum: str,
    outputs: list[pathlib.Path],
    manifest: dict | None = None,
) -> None:
    """Write a sentinel capturing manifest checksum + output hash.

    When the manifest dict is provided, the sentinel also records the
    per-step input fingerprint that enables incremental rebuild.
    """
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "completedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "specChecksum": spec_checksum,
        "outputHash": _hash_files(outputs),
        "outputs": [str(p) for p in outputs],
    }
    step = step_from_sentinel(sentinel_path)
    if manifest is not None and step is not None:
        payload["inputsHash"] = step_inputs_hash(manifest, step)
    sentinel_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read(sentinel_path: pathlib.Path) -> dict | None:
    """Read a sentinel. Returns None if missing or unparseable.

    Accepts both the new JSON format and the legacy plain-timestamp format
    (returned as {"completedAt": <text>, "_legacy": True}).
    """
    if not sentinel_path.exists():
        return None
    text = sentinel_path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"completedAt": text, "_legacy": True}


def is_stale(
    sentinel_path: pathlib.Path,
    current_spec_checksum: str,
    outputs: list[pathlib.Path],
    manifest: dict | None = None,
) -> tuple[bool, str]:
    """Return (stale, reason).

    Stale when:
      - sentinel missing or unparseable → stale, reason "missing"
      - sentinel is legacy format → stale, reason "legacy-format"
      - inputs fingerprint differs (when both sides have one) → "inputs-changed"
      - specChecksum differs (fallback when no fingerprint) → "spec-changed"
      - outputHash differs → stale, reason "outputs-modified"

    Incremental rebuild: when the sentinel carries an inputsHash AND the
    caller passes the current manifest, staleness is judged by the step's
    input fingerprint rather than the global specChecksum — so a spec edit
    only invalidates the steps whose inputs actually changed.

    A legacy sentinel is intentionally treated as stale; otherwise resume
    after an upgrade would honour pre-hash sentinels and skip steps we no
    longer trust.
    """
    payload = read(sentinel_path)
    if payload is None:
        return (True, "missing")
    if payload.get("_legacy"):
        return (True, "legacy-format")

    step = step_from_sentinel(sentinel_path)
    recorded_inputs = payload.get("inputsHash")
    if recorded_inputs and manifest is not None and step is not None:
        if recorded_inputs != step_inputs_hash(manifest, step):
            return (True, "inputs-changed")
    elif payload.get("specChecksum") != current_spec_checksum:
        return (True, "spec-changed")

    if payload.get("outputHash") != _hash_files(outputs):
        return (True, "outputs-modified")
    return (False, "fresh")


def _cli(argv: list[str]) -> int:
    """CLI for specialists to use after producing their outputs.

    Usage:
        py -3 accelerator/generators/sentinels.py write \
            --sentinel generated/build-state/02-infra.done \
            --manifest generated/build-state/manifest.json \
            --output generated/prototype/infra/main.bicepparam \
            --output generated/prototype/infra/modules/foundry-iq.bicep \
            --output generated/prototype/infra/modules/search.bicep

    Exits 1 with a clear message if any output path is missing — the
    sentinel is never written for an incomplete step.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="sentinels.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="Write a hash-aware sentinel")
    w.add_argument("--sentinel", required=True, type=pathlib.Path)
    w.add_argument(
        "--manifest",
        required=True,
        type=pathlib.Path,
        help="Path to manifest.json (reads specChecksum from it)",
    )
    w.add_argument(
        "--output",
        action="append",
        required=True,
        type=pathlib.Path,
        help="One or more output paths produced by this step (repeatable)",
    )
    c = sub.add_parser(
        "check",
        help="Check a sentinel's freshness (exit 0 fresh, 1 stale)",
    )
    c.add_argument("--sentinel", required=True, type=pathlib.Path)
    c.add_argument("--manifest", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)

    if not args.manifest.exists():
        print(f"FAIL: manifest not found: {args.manifest}", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: manifest is not valid JSON: {exc}", file=sys.stderr)
        return 1
    spec_checksum = manifest.get("specChecksum")
    if not spec_checksum:
        print("FAIL: manifest.specChecksum is missing", file=sys.stderr)
        return 1

    if args.cmd == "check":
        payload = read(args.sentinel) or {}
        outputs = [pathlib.Path(p) for p in payload.get("outputs", [])]
        stale, reason = is_stale(args.sentinel, spec_checksum, outputs, manifest=manifest)
        print(f"{'STALE' if stale else 'FRESH'} ({reason}): {args.sentinel.name}")
        return 1 if stale else 0

    missing = [str(p) for p in args.output if not p.exists()]
    if missing:
        print(
            "FAIL: declared outputs do not exist; refusing to write sentinel:",
            file=sys.stderr,
        )
        for p in missing:
            print(f"  - {p}", file=sys.stderr)
        return 1

    write(args.sentinel, spec_checksum, args.output, manifest=manifest)
    print(f"OK: sentinel written → {args.sentinel}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
