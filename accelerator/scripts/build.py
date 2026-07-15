#!/usr/bin/env python3
"""
accelerator/scripts/build.py — Headless build driver.

Runs the full spec-to-deployed pipeline without Copilot agent mode, with the
two schedule optimizations the interactive devlead can't do:

  1. PARALLEL GENERATION — LLM steps 3 (seed data), 4 (agent definitions),
     and 5 (knowledge docs) run as concurrent chat-completions calls instead
     of serially (~sum → slowest single step).
  2. PROVISION OVERLAP — `azd provision` starts as soon as the manifest and
     bicepparam exist and runs concurrently with generation. The
     postprovision hook defers itself (BUILD_DEFER_HOOKS) because seeding /
     registration need the generated artifacts; the driver re-runs it after
     both tracks finish, then runs `azd deploy`.

The driver composes the existing machinery rather than replacing it:
specialist briefs (.github/specialists/*.md) are the prompts, sentinels gate
resume exactly as with the devlead, preflight_env (phase 1) and preflight
(phase 2) are the same hard gates, deploy.py's env-alignment and AI Search
capacity-swap helpers are reused, and build-metrics records the same events.

LLM configuration (steps 3-5 need a model endpoint that exists BEFORE this
build — the prototype's own deployments are being provisioned concurrently):

    BUILD_LLM_API        "azure" (default) or "openai"
    BUILD_LLM_ENDPOINT   azure: https://<res>.openai.azure.com
                         openai: base URL (default https://api.openai.com/v1)
    BUILD_LLM_API_KEY    key for the endpoint
    BUILD_LLM_DEPLOYMENT azure deployment name (e.g. gpt-4o)
    BUILD_LLM_MODEL      openai model name (e.g. gpt-4o)

Usage:
    py -3 accelerator/scripts/build.py                # build + deploy + verify
    py -3 accelerator/scripts/build.py --no-deploy    # generate + preflight only
    py -3 accelerator/scripts/build.py --no-verify
    py -3 accelerator/scripts/build.py --plan         # show what would run

Exit codes: 0 success; 1 build/deploy/verify failure; 2 configuration problem.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTO = ROOT / "generated" / "prototype"
STATE = ROOT / "generated" / "build-state"
MANIFEST_PATH = STATE / "manifest.json"
SPECIALISTS = ROOT / ".github" / "specialists"

sys.path.insert(0, str(ROOT / "accelerator" / "generators"))
import sentinels  # noqa: E402


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_deploy = _load_module("deploy", ROOT / "accelerator" / "scripts" / "deploy.py")

_LLM_STEPS = {
    "03": ("data-agent", "Seed sample data"),
    "04": ("agents-builder", "Configure AI agents"),
    "05": ("docs-agent", "Generate knowledge docs"),
}

_MAX_LLM_RETRIES = 1  # one corrective retry per step on protocol/contract failure


def _log(msg: str) -> None:
    print(f"build: {msg}", flush=True)


def _run(cmd: list[str], cwd: pathlib.Path | None = None, env: dict | None = None,
         check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        env=env, capture_output=capture, text=True, shell=False,
    )
    if check and result.returncode != 0:
        if capture:
            sys.stderr.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
        raise SystemExit(f"build: command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def _py(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(ROOT / script), *args], check=check)


# ── LLM transport (stdlib only) ──────────────────────────────────────────────

def _chat(messages: list[dict], *, json_mode: bool = True) -> str:
    """One chat-completions call against the configured endpoint."""
    api = os.environ.get("BUILD_LLM_API", "azure").lower()
    key = os.environ.get("BUILD_LLM_API_KEY", "")
    if not key:
        raise SystemExit(
            "build: BUILD_LLM_API_KEY is not set. The generation steps need a "
            "model endpoint that already exists (see the header of this script)."
        )

    if api == "azure":
        endpoint = os.environ.get("BUILD_LLM_ENDPOINT", "").rstrip("/")
        deployment = os.environ.get("BUILD_LLM_DEPLOYMENT", "gpt-4o")
        if not endpoint:
            raise SystemExit("build: BUILD_LLM_ENDPOINT is not set (azure mode).")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21"
        headers = {"api-key": key, "Content-Type": "application/json"}
        body: dict = {"messages": messages, "temperature": 0.3, "max_tokens": 16000}
    else:
        endpoint = os.environ.get("BUILD_LLM_ENDPOINT", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("BUILD_LLM_MODEL", "gpt-4o")
        url = f"{endpoint}/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 16000}

    if json_mode:
        body["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        if json_mode and exc.code == 400 and "response_format" in detail:
            return _chat(messages, json_mode=False)  # endpoint predates json_object
        raise SystemExit(f"build: LLM call failed HTTP {exc.code}: {detail}")
    return payload["choices"][0]["message"]["content"] or ""


# ── Files protocol ───────────────────────────────────────────────────────────

def _extract_files_payload(text: str) -> list[dict]:
    """Parse the model's response into [{path, content}, ...].

    Accepts a bare JSON object, or one wrapped in code fences / stray prose.
    Raises ValueError with a description usable as retry feedback.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        if start < 0:
            raise ValueError("response contained no JSON object")
        candidate = candidate[start:]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response was not valid JSON: {exc}")
    files = parsed.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError('JSON must be {"files": [{"path": ..., "content": ...}, ...]}')
    for f in files:
        if not isinstance(f, dict) or not f.get("path") or not isinstance(f.get("content"), str):
            raise ValueError("every files[] entry needs a string 'path' and string 'content'")
    return files


def _validate_file_paths(files: list[dict], step: str) -> None:
    """Emitted paths must stay inside the step's owned output root."""
    roots = {
        "03": ("generated/prototype/db/",),
        "04": ("generated/prototype/agents/specialists/",),
        "05": ("generated/prototype/agents/knowledge/",),
    }[step]
    for f in files:
        path = f["path"].replace("\\", "/").lstrip("./")
        if ".." in path.split("/"):
            raise ValueError(f"path escapes the workspace: {f['path']}")
        if not path.startswith(roots):
            raise ValueError(
                f"step {step} may only write under {roots}, got: {f['path']}"
            )


def _kebab(title: str) -> str:
    # Matches title_to_filename() in fill-templates.py / preflight.py.
    slug = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    return re.sub(r"[\s-]+", "-", slug.strip()) + ".md"


def _required_outputs(manifest: dict, step: str) -> list[str]:
    """The exact files a step must emit — stated in the prompt and checked after."""
    if step == "03":
        return ["generated/prototype/db/cosmos_seed.py"]
    if step == "04":
        outputs = [
            "generated/prototype/agents/specialists/triage/agent.yaml",
            "generated/prototype/agents/specialists/triage/skills/routing/SKILL.md",
        ]
        for agent in manifest.get("agents", []):
            base = f"generated/prototype/agents/specialists/{agent['name']}"
            outputs.append(f"{base}/agent.yaml")
            outputs.append(f"{base}/schemas.py")
            for skill in agent.get("skills", []) or []:
                outputs.append(f"{base}/skills/{skill}/SKILL.md")
        return outputs
    if step == "05":
        return [f"generated/prototype/agents/knowledge/{_kebab(t)}" for t in manifest.get("documents", [])]
    raise ValueError(f"unknown LLM step {step}")


def _step_prompt(manifest: dict, step: str, specialist_md: str, feedback: str = "") -> list[dict]:
    required = _required_outputs(manifest, step)
    system = (
        "You are a code-generation specialist inside a deterministic build "
        "pipeline. Follow the specialist brief exactly for CONTENT, but ignore "
        "its instructions about running terminal commands, writing sentinel "
        "files, or printing summaries — the driver handles those.\n\n"
        "Respond with ONLY a JSON object of this exact shape, no prose, no "
        'markdown fences:\n{"files": [{"path": "<repo-relative path>", '
        '"content": "<full file content>"}]}\n\n'
        "You MUST emit every one of these files:\n- " + "\n- ".join(required)
    )
    user = (
        f"## Specialist brief\n\n{specialist_md}\n\n"
        f"## generated/build-state/manifest.json\n\n{json.dumps(manifest, indent=2)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if feedback:
        messages.append({
            "role": "user",
            "content": f"Your previous response failed validation:\n{feedback}\n"
                       f"Emit the corrected JSON files object now.",
        })
    return messages


def _post_write_check(manifest: dict, step: str) -> str:
    """Deterministic per-step contract check. Returns '' or an error message."""
    missing = [p for p in _required_outputs(manifest, step) if not (ROOT / p).exists()]
    if missing:
        return "missing required files: " + ", ".join(missing)
    if step == "03":
        result = subprocess.run(
            [sys.executable, "cosmos_seed.py"],
            cwd=str(PROTO / "db"),
            env={**os.environ, "SEED_DRY_RUN": "1"},
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            tail = (result.stdout + result.stderr).strip().splitlines()[-10:]
            return "seed dry-run failed:\n" + "\n".join(tail)
    return ""


def _run_llm_step(manifest: dict, step: str) -> None:
    name, label = _LLM_STEPS[step]
    specialist_md = (SPECIALISTS / f"{name}.md").read_text(encoding="utf-8")
    feedback = ""
    for attempt in range(_MAX_LLM_RETRIES + 1):
        _log(f"step {int(step)} ({label}) — LLM attempt {attempt + 1}")
        response = _chat(_step_prompt(manifest, step, specialist_md, feedback))
        try:
            files = _extract_files_payload(response)
            _validate_file_paths(files, step)
        except ValueError as exc:
            feedback = str(exc)
            _log(f"step {int(step)} attempt {attempt + 1} rejected: {feedback}")
            continue
        for f in files:
            target = ROOT / f["path"].replace("\\", "/").lstrip("./")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["content"], encoding="utf-8")
        error = _post_write_check(manifest, step)
        if not error:
            outputs = {
                "03": [PROTO / "db" / "cosmos_seed.py"],
                "04": [PROTO / "agents" / "specialists"],
                "05": [PROTO / "agents" / "knowledge"],
            }[step]
            sentinels.write(
                STATE / f"{step}-{name}.done",
                manifest["specChecksum"], outputs, manifest=manifest,
            )
            _log(f"step {int(step)} ({label}) complete — {len(files)} file(s)")
            return
        feedback = error
        _log(f"step {int(step)} attempt {attempt + 1} failed contract: {error.splitlines()[0]}")
    raise SystemExit(f"build: step {int(step)} ({label}) failed after retries: {feedback}")


# ── Deterministic steps + plan ───────────────────────────────────────────────

def _plan() -> dict[str, bool]:
    """step number → stale? (needs run)"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "accelerator/scripts/plan-rebuild.py"), "--json"],
        capture_output=True, text=True,
    )
    plan = json.loads(result.stdout)["plan"]
    return {f"{entry['step']:02d}": entry["action"] == "rerun" for entry in plan}


def _write_deterministic_sentinels(manifest: dict, stale: dict[str, bool]) -> None:
    targets = {
        "02": [PROTO / "infra" / "main.bicepparam", PROTO / "azure.yaml"],
        "06": [PROTO / "backend" / "config.py"],
        "07": [PROTO / "hooks" / "postprovision.sh", PROTO / "hooks" / "postprovision.ps1"],
    }
    for step, outputs in targets.items():
        if stale.get(step, True):
            name = {"02": "infra-agent", "06": "backend-agent", "07": "hook-agent"}[step]
            sentinels.write(STATE / f"{step}-{name}.done", manifest["specChecksum"], outputs, manifest=manifest)


# ── Provision track (runs concurrently with generation) ─────────────────────

class _ProvisionTrack:
    """Runs env-alignment + `azd provision` on a thread, with deploy.py's
    AI Search capacity-swap recovery."""

    def __init__(self, azd: str, manifest: dict):
        self.azd = azd
        self.manifest = manifest
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        self._thread.join()
        if self.error:
            raise SystemExit(f"build: provisioning failed — {self.error}")

    def _azd_env_set(self, key: str, value: str) -> None:
        subprocess.run([self.azd, "env", "set", key, value], cwd=str(PROTO),
                       capture_output=True, text=True)

    def _run(self) -> None:
        try:
            _deploy._align_azd_env(self.azd, self.manifest, dry_run=False)
            self._azd_env_set("BUILD_DEFER_HOOKS", "true")
            tried: set[str] = set()
            for attempt in range(3):
                result = subprocess.run(
                    [self.azd, "provision", "--no-prompt"],
                    cwd=str(PROTO), capture_output=True, text=True,
                )
                sys.stdout.write(result.stdout or "")
                sys.stderr.write(result.stderr or "")
                if result.returncode == 0:
                    return
                stderr_lower = (result.stderr or "").lower()
                if not _deploy._looks_like_search_capacity_error(stderr_lower):
                    self.error = f"azd provision exited {result.returncode}"
                    return
                current = self.manifest.get("deployment", {}).get("location", "")
                tried.add(current.lower())
                fallback = _deploy._pick_search_fallback(current, tried)
                if not fallback:
                    self.error = "AI Search capacity exhausted in all fallback regions"
                    return
                _log(f"AI Search capacity error — retrying with AZURE_SEARCH_LOCATION={fallback}")
                self._azd_env_set("AZURE_SEARCH_LOCATION", fallback)
                tried.add(fallback.lower())
            self.error = "azd provision failed after retries"
        except BaseException as exc:  # never lose the thread silently
            self.error = str(exc)
        finally:
            self._azd_env_set("BUILD_DEFER_HOOKS", "false")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Headless spec-to-deployed build")
    parser.add_argument("--no-deploy", action="store_true", help="Generate + preflight only")
    parser.add_argument("--no-verify", action="store_true", help="Skip post-deploy verification")
    parser.add_argument("--plan", action="store_true", help="Print the rebuild plan and exit")
    args = parser.parse_args()

    deploy_enabled = not args.no_deploy

    # Step 0 + clock
    _py("accelerator/generators/materialize-prototype.py")
    _py("accelerator/scripts/build-metrics.py", "record", "build-start", "--keep-existing")

    # Step 1 (always cheap) + phase-1 gate
    _py("accelerator/generators/spec-validator.py")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    stale = _plan()
    if args.plan:
        _py("accelerator/scripts/plan-rebuild.py")
        return 0

    if deploy_enabled:
        _py("accelerator/generators/preflight_env.py")

    # Deterministic hydration (steps 2, 6, 7)
    _py("accelerator/generators/fill-templates.py")
    _write_deterministic_sentinels(manifest, stale)

    # Kick provisioning early so it overlaps generation
    provision: _ProvisionTrack | None = None
    if deploy_enabled:
        azd = _deploy._which("azd", ".exe")
        if not azd:
            raise SystemExit("build: azd not on PATH")
        _py("accelerator/scripts/build-metrics.py", "record", "deploy-start")
        provision = _ProvisionTrack(azd, manifest)
        provision.start()
        _log("azd provision started (overlapped with generation)")

    # Parallel LLM generation for stale steps
    llm_stale = [s for s in _LLM_STEPS if stale.get(s, True)]
    if llm_stale:
        _log(f"generating steps {', '.join(str(int(s)) for s in llm_stale)} in parallel")
        with ThreadPoolExecutor(max_workers=len(llm_stale)) as pool:
            futures = {pool.submit(_run_llm_step, manifest, s): s for s in llm_stale}
            failures = []
            for future, s in futures.items():
                try:
                    future.result()
                except SystemExit as exc:
                    failures.append(str(exc))
        if failures:
            raise SystemExit("; ".join(failures))
    else:
        _log("generation steps all fresh — skipped")

    # Phase-2 gate
    _py("accelerator/generators/preflight.py")

    if not deploy_enabled:
        _log("--no-deploy: stopping after preflight (artifacts + sentinels written)")
        return 0

    # Join provisioning, then run the deferred hooks + deploy
    provision.join()
    _log("provisioning complete — running deferred postprovision hooks")
    _run([azd, "hooks", "run", "postprovision"], cwd=PROTO)
    _run([azd, "deploy", "--no-prompt"], cwd=PROTO)
    _py("accelerator/scripts/build-metrics.py", "record", "deploy-done")

    # Resolve the app URL
    result = _run([azd, "env", "get-value", "AZURE_CONTAINER_APP_URL"], cwd=PROTO, capture=True)
    app_url = (result.stdout or "").strip()

    if not args.no_verify and app_url:
        _py("accelerator/scripts/verify-prototype.py", app_url)
        _py("accelerator/scripts/build-metrics.py", "record", "verify-done")

    _py("accelerator/scripts/build-metrics.py", "summary")
    if app_url:
        _log(f"App URL: {app_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
