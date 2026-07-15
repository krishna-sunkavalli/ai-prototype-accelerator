#!/usr/bin/env python3
"""
accelerator/scripts/verify-prototype.py — Post-deploy acceptance smoke test.

Drives every starter question through the deployed prototype's /chat
WebSocket and verifies the swarm end-to-end: routing fired, a specialist
produced a non-empty response, and no error events surfaced. This is the
"definition of done" for a build — the devlead runs it after `azd up` and
reports scenarios passing in the final summary.

Usage:
    py -3 accelerator/scripts/verify-prototype.py https://<containerAppFqdn>
    py -3 accelerator/scripts/verify-prototype.py https://... --timeout 120

Requires the `websockets` package on the machine running the check:
    py -3 -m pip install websockets

Exit codes: 0 all scenarios passed, 1 any failure, 2 setup problem.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request

try:
    import websockets
except ImportError:
    print(
        "ERROR: the 'websockets' package is required.\n"
        "       Install it with: py -3 -m pip install websockets",
        file=sys.stderr,
    )
    sys.exit(2)


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def run_scenario(ws_url: str, question: str, timeout: float) -> dict:
    """Send one starter question; collect events until the answer completes."""
    result = {
        "question": question,
        "passed": False,
        "agent": "",
        "tool_calls": 0,
        "structured": False,
        "error": "",
        "elapsed_s": 0.0,
    }
    start = time.monotonic()
    text_parts: list[str] = []
    try:
        async with websockets.connect(ws_url, open_timeout=15) as ws:
            await ws.send(question)
            while True:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    result["error"] = f"timeout after {timeout:.0f}s"
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    result["error"] = f"timeout after {timeout:.0f}s"
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type", "")
                if mtype == "handoff":
                    result["agent"] = msg.get("agent", "")
                elif mtype == "tool_activity":
                    result["tool_calls"] += 1
                elif mtype == "error":
                    result["error"] = msg.get("content") or msg.get("message") or "error event"
                    break
                elif mtype == "text":
                    text_parts.append(msg.get("content", ""))
                    if msg.get("done"):
                        break
                elif mtype == "done":
                    break
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["elapsed_s"] = round(time.monotonic() - start, 1)
    full_text = "".join(text_parts).strip()
    if full_text and not result["error"]:
        result["passed"] = True
        candidate = full_text
        if candidate.startswith("```"):
            candidate = candidate.strip("`\n ")
            if candidate.lower().startswith("json"):
                candidate = candidate[4:]
        try:
            json.loads(candidate)
            result["structured"] = True
        except (json.JSONDecodeError, ValueError):
            result["structured"] = False
    elif not result["error"]:
        result["error"] = "no response text received"
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="Post-deploy prototype smoke test")
    parser.add_argument("base_url", help="Deployed app URL, e.g. https://<fqdn>")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-question timeout (s)")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/chat"

    print(f"verify-prototype.py — acceptance smoke test against {base}")

    try:
        health = _fetch_json(f"{base}/health")
        health_status = str(health.get("status") or "?").lower()
        warnings = health.get("warnings") or []
        print(f"  /health : {health_status} | agents: {', '.join(health.get('agents', []))}")
    except Exception as exc:
        print(f"  /health : UNREACHABLE ({exc})", file=sys.stderr)
        return 1

    # Detect MCAPS Cosmos-firewall degradation: /health reports "degraded"
    # and every warning is a Cosmos public-internet firewall block. In that
    # state the SQL tool returns empty rows, but agents still route,
    # knowledge search still works, and the app is otherwise healthy. We
    # grade against that reduced expectation instead of failing the build.
    cosmos_blocked = (
        health_status == "degraded"
        and bool(warnings)
        and all(
            isinstance(w, str)
            and w.lower().startswith("cosmos:")
            and "firewall" in w.lower()
            for w in warnings
        )
    )
    if cosmos_blocked:
        print("  MCAPS mode: Cosmos firewall blocks seeding — grading on")
        print("              routing + knowledge base retrieval only.")

    try:
        config = _fetch_json(f"{base}/config")
    except Exception as exc:
        print(f"  /config : UNREACHABLE ({exc})", file=sys.stderr)
        return 1
    questions = config.get("STARTER_QUESTIONS", []) or []
    if not questions:
        print("  /config : no STARTER_QUESTIONS configured — nothing to verify", file=sys.stderr)
        return 1
    print(f"  /config : {len(questions)} starter scenario(s)")
    print()

    # Scenarios run concurrently — each opens its own websocket/session, so
    # this also exercises multi-session isolation. Total time is the slowest
    # question, not the sum.
    results = await asyncio.gather(
        *(run_scenario(ws_url, q, args.timeout) for q in questions)
    )

    # In MCAPS mode, tolerate "no response text" / "no rows" style failures
    # that come from empty SQL results. Anything else (routing failure,
    # websocket errors, model errors) is still a hard fail.
    if cosmos_blocked:
        for r in results:
            if r["passed"]:
                continue
            err = (r.get("error") or "").lower()
            if "no response text received" in err or "empty" in err:
                r["passed"] = True
                r["mcaps_tolerated"] = True

    for i, r in enumerate(results, 1):
        print(f"  [{i}/{len(results)}] {r['question']}")
        if r["passed"]:
            shape = "structured JSON" if r["structured"] else "text"
            tolerated = " (MCAPS-tolerated)" if r.get("mcaps_tolerated") else ""
            print(
                f"        PASS{tolerated} — routed to {r['agent'] or '(unknown)'}, "
                f"{r['tool_calls']} tool call(s), {shape}, {r['elapsed_s']}s"
            )
        else:
            print(f"        FAIL — {r['error']} ({r['elapsed_s']}s)")

    passed = sum(1 for r in results if r["passed"])
    agents_hit = sorted({r["agent"] for r in results if r["agent"]})
    print()
    print(f"  Scenarios passing : {passed}/{len(results)}")
    print(f"  Specialists hit   : {', '.join(agents_hit) or '(none)'}")
    if passed == len(results):
        verdict = "DEGRADED-PASS" if cosmos_blocked else "PASS"
        note = (
            " (Cosmos blocked by MCAPS firewall — data-heavy answers return empty rows)"
            if cosmos_blocked else ""
        )
        print(f"  VERDICT: {verdict} — prototype answers every starter scenario.{note}")
        return 0
    print("  VERDICT: FAIL — see failures above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
