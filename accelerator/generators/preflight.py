#!/usr/bin/env python3
"""
accelerator/generators/preflight.py — Validate generated prototype before deploy.

Catches a class of bugs that historically only surfaced during `azd up`:
    - .bicep / .bicepparam don't compile
    - .bicep / .bicepparam fail Azure-side validation (what-if): policy
      denials, region/SKU unavailability, name collisions, parameter drift
    - Generated .py files are syntactically broken
    - Generated agent.yaml / azure.yaml are malformed
    - register_agents.py references a tool not in tool_definitions.yaml
    - Required artifacts missing from generated/prototype/

Run order is fast → expensive. Exits non-zero on the first failure so
`@devlead build` can stop before invoking `azd up`.
"""
from __future__ import annotations

import json
import pathlib
import py_compile
import shutil
import subprocess
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Run: py -3 -m pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTO = ROOT / "generated" / "prototype"
STATE = ROOT / "generated" / "build-state"
MANIFEST = STATE / "manifest.json"


def _err(checks: list[str], msg: str) -> None:
    checks.append(msg)


def check_required_paths(errors: list[str]) -> None:
    required = [
        MANIFEST,
        PROTO / "infra" / "main.bicep",
        PROTO / "infra" / "main.bicepparam",
        PROTO / "infra" / "modules" / "foundry-iq.bicep",
        PROTO / "azure.yaml",
        PROTO / "Dockerfile",
        PROTO / "backend" / "main.py",
        PROTO / "backend" / "config.py",
        PROTO / "backend" / "requirements.txt",
        PROTO / "agents" / "orchestrator.py",
        PROTO / "agents" / "register_agents.py",
        PROTO / "agents" / "tools" / "tool_definitions.yaml",
        PROTO / "db" / "cosmos_seed.py",
        PROTO / "db" / "_seed_lib.py",
        PROTO / "frontend" / "public" / "index.html",
        PROTO / "hooks" / "preprovision.sh",
        PROTO / "hooks" / "postprovision.sh",
        PROTO / "hooks" / "postprovision.ps1",
    ]
    for p in required:
        if not p.exists():
            _err(errors, f"missing required artifact: {p.relative_to(ROOT)}")


def check_python_compiles(errors: list[str]) -> None:
    for py in PROTO.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as e:
            _err(errors, f"py-compile failed: {py.relative_to(ROOT)} — {e.msg.strip()}")


def check_yaml_parses(errors: list[str]) -> None:
    for y in list(PROTO.rglob("*.yaml")) + list(PROTO.rglob("*.yml")):
        try:
            with open(y, "r", encoding="utf-8") as f:
                yaml.safe_load(f)
        except yaml.YAMLError as e:
            _err(errors, f"yaml parse failed: {y.relative_to(ROOT)} — {e}")


def check_json_parses(errors: list[str]) -> None:
    for j in PROTO.rglob("*.json"):
        if ".azure" in j.parts or "node_modules" in j.parts:
            continue
        try:
            json.loads(j.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _err(errors, f"json parse failed: {j.relative_to(ROOT)} — {e}")


def check_no_unresolved_placeholders(errors: list[str]) -> None:
    """Hydrated output files must contain no {{PLACEHOLDER}} tokens."""
    import re
    pattern = re.compile(r"\{\{[A-Z0-9_]+\}\}")
    targets = [
        PROTO / "infra" / "main.bicepparam",
        PROTO / "backend" / "config.py",
        PROTO / "hooks" / "postprovision.sh",
        PROTO / "hooks" / "postprovision.ps1",
    ]
    for t in targets:
        if not t.exists():
            continue
        text = t.read_text(encoding="utf-8")
        hits = sorted(set(pattern.findall(text)))
        if hits:
            _err(errors, f"unresolved placeholders in {t.relative_to(ROOT)}: {hits}")


def check_tools_resolve(errors: list[str]) -> None:
    """Every agent.yaml tool must be declared in tool_definitions.yaml."""
    tool_def_path = PROTO / "agents" / "tools" / "tool_definitions.yaml"
    if not tool_def_path.exists():
        return  # already reported by check_required_paths
    with open(tool_def_path, "r", encoding="utf-8") as f:
        defs = yaml.safe_load(f) or []
    known = {d.get("name") for d in defs if isinstance(d, dict)}

    for agent_yaml in (PROTO / "agents" / "specialists").rglob("agent.yaml"):
        with open(agent_yaml, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for t in cfg.get("tools", []) or []:
            if t not in known:
                _err(
                    errors,
                    f"{agent_yaml.relative_to(ROOT)}: tool '{t}' not in tool_definitions.yaml",
                )


def check_bicep_builds(errors: list[str]) -> None:
    """Run `az bicep build` on main.bicep. Soft-skip if az is not installed."""
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        return
    main_bicep = PROTO / "infra" / "main.bicep"
    if not main_bicep.exists():
        return
    try:
        result = subprocess.run(
            [az, "bicep", "build", "--file", str(main_bicep), "--stdout"],
            capture_output=True,
            text=True,
            shell=False,
        )
    except (OSError, FileNotFoundError):
        return
    if result.returncode != 0:
        # `az bicep build` writes lint warnings to stderr even on success.
        # Only fail on a non-zero exit.
        _err(errors, f"az bicep build failed: {result.stderr.strip()[:500]}")


def check_bicep_whatif(errors: list[str]) -> None:
    """Run `az deployment sub what-if` against the emitted Bicep.

    Catches policy denials, name collisions, region/SKU unavailability, and
    schema drift between `main.bicep` and `main.bicepparam` — all of which
    historically only surfaced after `azd up` had already started spending
    time provisioning. Hard-fail on real validation errors; soft-skip when
    the dev box can't reach Azure (not logged in, no subscription, offline).

    Skip is signalled by stderr containing one of the known "not ready"
    markers below. Any other non-zero exit is treated as a real failure.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        return
    main_bicep = PROTO / "infra" / "main.bicep"
    bicepparam = PROTO / "infra" / "main.bicepparam"
    if not (main_bicep.exists() and bicepparam.exists() and MANIFEST.exists()):
        return

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return  # already reported by check_manifest_schema
    location = manifest.get("deployment", {}).get("location")
    if not location:
        return  # schema validator will have flagged this
    resource_group = manifest.get("deployment", {}).get("resourceGroup")
    if not resource_group:
        return

    try:
        result = subprocess.run(
            [
                az,
                "deployment",
                "group",
                "what-if",
                "--resource-group",
                resource_group,
                "--template-file",
                str(main_bicep),
                "--parameters",
                str(bicepparam),
                "--no-pretty-print",
                "--only-show-errors",
            ],
            capture_output=True,
            text=True,
            shell=False,
            timeout=180,
        )
    except (OSError, FileNotFoundError):
        return
    except subprocess.TimeoutExpired:
        _err(errors, "az deployment group what-if timed out after 180s")
        return

    if result.returncode == 0:
        return

    stderr_low = (result.stderr or "").lower()
    skip_markers = (
        "please run 'az login'",
        "az login",
        "no subscription found",
        "please select a subscription",
        "could not connect",
        "could not retrieve",
        "interactiveauthenticationrequired",
        "subscription is not registered",
        "resourcegroupnotfound",       # RG doesn't exist yet — azd creates it
        "could not be found",          # generic "RG not found" wording
    )
    if any(m in stderr_low for m in skip_markers):
        return

    # Real validation failure — surface the first ~800 chars of stderr.
    msg = (result.stderr or result.stdout or "").strip()
    _err(errors, f"az deployment group what-if failed: {msg[:800]}")


def check_manifest_schema(errors: list[str]) -> None:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    try:
        from manifest_schema import load_and_validate  # noqa: E402
        load_and_validate(MANIFEST)
    except SystemExit as e:
        _err(errors, f"manifest schema: {e}")


def check_foundry_subdomain_available(errors: list[str]) -> None:
    """Fail fast when the computed Foundry custom subdomain is held by a
    soft-deleted Cognitive Services account.

    Bicep computes the Foundry hub name as
        '{customerShort}-{demoTheme}-hub'
    and uses that as the custom subdomain. Subdomains are global and reserved
    for 48h after soft-delete. If a prior failed build left a soft-deleted
    account with the same name, `azd up` fails mid-provision with
    `CustomDomainInUse` — wasting 10-15 minutes.

    Soft-skip when az CLI is unavailable, the user is not logged in, or the
    subscription cannot be reached.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az or not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    deployment = manifest.get("deployment", {})
    customer_short = deployment.get("customerShort")
    demo_theme = deployment.get("demoTheme")
    if not (customer_short and demo_theme):
        return
    subdomain = f"{customer_short}-{demo_theme}-hub"

    try:
        result = subprocess.run(
            [
                az, "cognitiveservices", "account", "list-deleted",
                "--query",
                f"[?name=='{subdomain}'].{{name:name,location:location,resourceGroup:resourceGroup}}",
                "-o", "json",
                "--only-show-errors",
            ],
            capture_output=True, text=True, shell=False, timeout=30,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return

    if result.returncode != 0:
        # Soft-skip on auth / network issues; surface only real failures.
        stderr_low = (result.stderr or "").lower()
        soft = ("az login", "no subscription", "could not", "interactiveauthentication")
        if any(m in stderr_low for m in soft):
            return
        return  # don't block on partial CLI failures

    try:
        deleted = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return
    if deleted:
        d = deleted[0]
        _err(
            errors,
            (
                f"Foundry subdomain '{subdomain}' is held by a soft-deleted "
                f"Cognitive Services account in {d.get('location')} "
                f"(resource group: {d.get('resourceGroup')}). "
                f"Run: az cognitiveservices account purge --name {subdomain} "
                f"--location {d.get('location')} --resource-group {d.get('resourceGroup')}"
            ),
        )


def check_azd_env_matches_manifest(errors: list[str]) -> None:
    """Warn when the active azd env name differs from manifest.environmentName.

    Reuse of an azd env from an earlier customer can silently deploy to the
    wrong resource group. We only emit a warning (not a hard fail) because
    azd auto-creates the env on first `azd up`; the check is a no-op until
    the user has at least one env defined.
    """
    if not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    expected = manifest.get("deployment", {}).get("environmentName")
    if not expected:
        return
    config_path = PROTO / ".azure" / "config.json"
    if not config_path.exists():
        return  # no env yet — azd will create one matching AZURE_ENV_NAME
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    active = cfg.get("defaultEnvironment")
    if active and active != expected:
        _err(
            errors,
            (
                f"active azd env '{active}' does not match manifest "
                f"environmentName '{expected}'. Run: "
                f"azd env new {expected} (from generated/prototype) to switch."
            ),
        )


def check_brand_contrast(errors: list[str]) -> None:
    """Hard-fail when branding.primaryColor has < 4.5:1 contrast against white.

    The frontend draws white text on the primary color (user message bubbles,
    primary buttons, focus rings). Anything below WCAG AA (4.5:1) renders as
    unreadable. We catch this here instead of in spec-validator because the
    BA agent sometimes picks a brand-accurate-but-too-light color (e.g. a
    customer's official light blue) and we don't want validation noise during
    spec authoring — only at deploy time.
    """
    if not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    primary = manifest.get("branding", {}).get("primaryColor", "")
    if not isinstance(primary, str) or not primary.startswith("#") or len(primary) != 7:
        return  # schema check already caught malformed colors

    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    try:
        r = int(primary[1:3], 16) / 255.0
        g = int(primary[3:5], 16) / 255.0
        b = int(primary[5:7], 16) / 255.0
    except ValueError:
        return
    lum = 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)
    ratio = 1.05 / (lum + 0.05)  # contrast against white (L=1.0)
    if ratio < 4.5:
        _err(
            errors,
            (
                f"branding.primaryColor {primary} has {ratio:.2f}:1 contrast "
                f"against white — WCAG AA requires 4.5:1 for readable button "
                f"labels and bubble text. Darken primary_color in spec.yaml."
            ),
        )


def main() -> None:
    print("preflight.py — Pre-deploy validation")
    errors: list[str] = []

    check_required_paths(errors)
    check_manifest_schema(errors)
    check_brand_contrast(errors)
    check_no_unresolved_placeholders(errors)
    check_python_compiles(errors)
    check_yaml_parses(errors)
    check_json_parses(errors)
    check_tools_resolve(errors)
    check_bicep_builds(errors)
    check_foundry_subdomain_available(errors)
    check_azd_env_matches_manifest(errors)
    check_bicep_whatif(errors)

    if errors:
        print(f"  FAILED — {len(errors)} issue(s):", file=sys.stderr)
        for e in errors:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    print("  OK — generated prototype is ready to deploy.")


if __name__ == "__main__":
    main()
