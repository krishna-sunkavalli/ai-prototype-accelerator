#!/usr/bin/env python3
"""
accelerator/scripts/build-metrics.py — Measure spec-to-deployed wall time.

"Accelerate product development" is a quantitative claim; this script makes
every build prove it. Step completion times come from the sentinel files the
build already writes; boundary events (build start, deploy done, verify done)
are recorded explicitly by the devlead into build-state/metrics.json.

Usage:
    py -3 accelerator/scripts/build-metrics.py record build-start
    py -3 accelerator/scripts/build-metrics.py record build-start --keep-existing
    py -3 accelerator/scripts/build-metrics.py record deploy-start
    py -3 accelerator/scripts/build-metrics.py record deploy-done
    py -3 accelerator/scripts/build-metrics.py record verify-done
    py -3 accelerator/scripts/build-metrics.py step-start <sentinel-name>
    py -3 accelerator/scripts/build-metrics.py summary

`record --keep-existing` is for resume: it preserves the original clock
instead of restarting it. `deploy-start` is recorded by devlead immediately
before `azd up`, so `summary` can split generation time from provisioning
time from verification time. `step-start <sentinel-name>` is recorded by
each specialist right after it reads manifest.json and before it starts
producing outputs (e.g. `step-start 02-infra-agent`) — pairing that with
the step's `.done` sentinel `completedAt` timestamp lets `summary` split
each step's own "active" work time from the "idle" time spent waiting
between the previous step finishing and this one starting (Copilot
reasoning, tool round-trips, user reading the progress table, etc).
`summary` prints a per-step timeline, a phase breakdown, an active-vs-idle
breakdown, and the headline total; its last line is designed to drop
straight into the devlead's final summary block.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE = ROOT / "generated" / "build-state"
METRICS_PATH = STATE / "metrics.json"

STEPS: list[tuple[str, str]] = [
    ("01-spec-validator", "Validate spec"),
    ("02-infra-agent", "Build infrastructure"),
    ("03-data-agent", "Seed sample data"),
    ("04-agents-builder", "Configure AI agents"),
    ("05-docs-agent", "Generate knowledge docs"),
    ("06-backend-agent", "Configure backend"),
    ("07-hook-agent", "Write deploy hooks"),
]

_EVENTS = ("build-start", "deploy-start", "deploy-done", "verify-done")
_STEP_NAMES = {sentinel_name for sentinel_name, _ in STEPS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _load_metrics() -> dict:
    if METRICS_PATH.exists():
        try:
            data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
            data.setdefault("events", {})
            data.setdefault("stepStarts", {})
            return data
        except json.JSONDecodeError:
            pass
    return {"events": {}, "stepStarts": {}}


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def record(event: str, keep_existing: bool) -> int:
    if event not in _EVENTS:
        print(f"ERROR: unknown event '{event}' (allowed: {', '.join(_EVENTS)})", file=sys.stderr)
        return 1
    STATE.mkdir(parents=True, exist_ok=True)
    metrics = _load_metrics()
    if keep_existing and metrics["events"].get(event):
        print(f"kept existing {event}: {metrics['events'][event]}")
        return 0
    metrics["events"][event] = _now_iso()
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"recorded {event}: {metrics['events'][event]}")
    return 0


def step_start(sentinel_name: str) -> int:
    if sentinel_name not in _STEP_NAMES:
        print(
            f"ERROR: unknown step '{sentinel_name}' (allowed: {', '.join(sorted(_STEP_NAMES))})",
            file=sys.stderr,
        )
        return 1
    STATE.mkdir(parents=True, exist_ok=True)
    metrics = _load_metrics()
    metrics["stepStarts"][sentinel_name] = _now_iso()
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"recorded step-start {sentinel_name}: {metrics['stepStarts'][sentinel_name]}")
    return 0


def summary() -> int:
    metrics = _load_metrics()
    events = metrics.get("events", {})

    start = _parse_iso(events.get("build-start", ""))
    if start is None:
        # Fall back to the manifest write time — loses step-0 scaffold time only.
        manifest_path = STATE / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                start = _parse_iso(manifest.get("buildTimestamp", ""))
            except json.JSONDecodeError:
                pass
    if start is None:
        print("ERROR: no build-start event and no manifest — nothing to summarize", file=sys.stderr)
        return 1

    print("Build timeline")
    last: datetime = start
    total_active = 0.0
    total_idle = 0.0
    step_starts = metrics.get("stepStarts", {})
    for sentinel_name, label in STEPS:
        path = STATE / f"{sentinel_name}.done"
        completed = None
        if path.exists():
            try:
                completed = _parse_iso(json.loads(path.read_text(encoding="utf-8")).get("completedAt", ""))
            except json.JSONDecodeError:
                pass
        if completed is None:
            print(f"  t+ --:--  {label:<24} (no sentinel)")
            continue
        offset = (completed - start).total_seconds()
        if offset < 0:
            # Sentinel predates the clock (kept fresh across a rebuild) —
            # the step wasn't re-run, so it contributes no time.
            print(f"  t+  0:00  {label:<24} (reused from earlier build)")
            continue
        step_start_ts = _parse_iso(step_starts.get(sentinel_name, ""))
        if step_start_ts is not None and step_start_ts <= completed:
            active_secs = (completed - step_start_ts).total_seconds()
            idle_secs = max(0.0, (step_start_ts - last).total_seconds())
            total_active += active_secs
            total_idle += idle_secs
            print(
                f"  t+{int(offset // 60):3d}:{int(offset % 60):02d}  {label:<24} "
                f"active {_fmt_duration(active_secs)}, idle {_fmt_duration(idle_secs)}"
            )
        else:
            print(f"  t+{int(offset // 60):3d}:{int(offset % 60):02d}  {label}")
        last = max(last, completed)

    for event, label in (("deploy-start", "Deploy started"), ("deploy-done", "Deploy to Azure"), ("verify-done", "Verify deployment")):
        ts = _parse_iso(events.get(event, ""))
        if ts is not None:
            offset = (ts - start).total_seconds()
            print(f"  t+{int(offset // 60):3d}:{int(offset % 60):02d}  {label}")
            last = max(last, ts)

    # ── Phase breakdown ────────────────────────────────────────────────
    # Only render when deploy-start is recorded; older builds without it
    # still get the timeline + headline.
    deploy_start = _parse_iso(events.get("deploy-start", ""))
    deploy_done  = _parse_iso(events.get("deploy-done", ""))
    verify_done  = _parse_iso(events.get("verify-done", ""))
    if deploy_start is not None:
        print()
        print("Phase breakdown")
        gen_secs = max(0, (deploy_start - start).total_seconds())
        print(f"  Generation   : {_fmt_duration(gen_secs)}")
        if deploy_done is not None:
            prov_secs = max(0, (deploy_done - deploy_start).total_seconds())
            print(f"  Provisioning : {_fmt_duration(prov_secs)}")
            if verify_done is not None:
                verify_secs = max(0, (verify_done - deploy_done).total_seconds())
                print(f"  Verification : {_fmt_duration(verify_secs)}")
        print()

    # ── Active vs idle breakdown ─────────────────────────────────────
    # Only rendered for steps that had a recorded step-start; older
    # metrics.json files (no stepStarts) or steps whose specialist
    # skipped the step-start call fall back silently — no error, just
    # no line here.
    if total_active or total_idle:
        print("Active vs idle (steps with a recorded start)")
        print(f"  Active work  : {_fmt_duration(total_active)}")
        print(f"  Idle/waiting : {_fmt_duration(total_idle)}")
        print()

    total = (last - start).total_seconds()
    if _parse_iso(events.get("verify-done", "")):
        headline = f"Spec -> deployed, verified product: {_fmt_duration(total)}"
    elif _parse_iso(events.get("deploy-done", "")):
        headline = f"Spec -> deployed product: {_fmt_duration(total)}"
    else:
        headline = f"Spec -> generated prototype: {_fmt_duration(total)}"
    print(headline)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "record" and len(args) >= 2:
        return record(args[1], keep_existing="--keep-existing" in args)
    if args[0] == "step-start" and len(args) >= 2:
        return step_start(args[1])
    if args[0] == "summary":
        return summary()
    print(f"ERROR: unknown command {args[0]!r} — use 'record <event>', 'step-start <name>', or 'summary'", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
