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
import os
import pathlib
import py_compile
import re
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


# Tools available in the static register_agents.py _TOOL_CATALOGUE. An
# agent.yaml referencing anything else registers a PromptAgent whose tool
# calls can never be dispatched (RESOLVED.md #4b).
_KNOWN_TOOLS = {"run_sql_query", "search_knowledge_base", "call_mock_api"}


def _load_manifest() -> dict | None:
    if not MANIFEST.exists():
        return None
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _title_to_filename(title: str) -> str:
    """Document title → kebab-case .md filename (docs-agent convention).

    Kept in sync with title_to_filename() in fill-templates.py — both must
    match the filenames the postprovision hook uploads.
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug.strip())
    return f"{slug}.md"


def check_agent_outputs(errors: list[str]) -> None:
    """Contract-check the LLM-emitted agent definitions (build step 4).

    The agents-builder specialist writes agent.yaml / schemas.py / SKILL.md
    from prompt instructions alone; nothing else verifies it followed them.
    This is the deterministic gate: every manifest agent must have a parseable
    agent.yaml whose name matches, whose tools exist in the static
    _TOOL_CATALOGUE, and whose skills all have SKILL.md files.
    """
    manifest = _load_manifest()
    if manifest is None:
        return
    specialists = PROTO / "agents" / "specialists"
    if not specialists.exists():
        _err(errors, "agents/specialists/ missing — step 4 (agents-builder) has not run")
        return

    triage = specialists / "triage" / "agent.yaml"
    if not triage.exists():
        _err(errors, "agents/specialists/triage/agent.yaml missing")

    for agent in manifest.get("agents", []) or []:
        name = agent.get("name", "")
        agent_dir = specialists / name
        agent_yaml = agent_dir / "agent.yaml"
        if not agent_yaml.exists():
            _err(errors, f"agents/specialists/{name}/agent.yaml missing")
            continue
        try:
            config = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            _err(errors, f"agents/specialists/{name}/agent.yaml is not valid YAML: {exc}")
            continue
        if not isinstance(config, dict):
            _err(errors, f"agents/specialists/{name}/agent.yaml is not a mapping")
            continue
        if config.get("name") != name:
            _err(
                errors,
                f"agents/specialists/{name}/agent.yaml name mismatch: "
                f"{config.get('name')!r} != {name!r}",
            )
        if not str(config.get("system_prompt") or "").strip():
            _err(errors, f"agents/specialists/{name}/agent.yaml has empty system_prompt")
        unknown = [t for t in (config.get("tools") or []) if t not in _KNOWN_TOOLS]
        if unknown:
            _err(
                errors,
                f"agents/specialists/{name}/agent.yaml declares tools not in the "
                f"static _TOOL_CATALOGUE: {unknown} (allowed: {sorted(_KNOWN_TOOLS)})",
            )
        if not (agent_dir / "schemas.py").exists():
            _err(errors, f"agents/specialists/{name}/schemas.py missing")
        for skill in agent.get("skills", []) or []:
            skill_md = agent_dir / "skills" / skill / "SKILL.md"
            if not skill_md.exists():
                _err(errors, f"agents/specialists/{name}/skills/{skill}/SKILL.md missing")


def check_knowledge_docs(errors: list[str]) -> None:
    """Every manifest document must exist under agents/knowledge/ (step 5)."""
    manifest = _load_manifest()
    if manifest is None:
        return
    knowledge = PROTO / "agents" / "knowledge"
    for title in manifest.get("documents", []) or []:
        filename = _title_to_filename(title)
        if not (knowledge / filename).exists():
            _err(
                errors,
                f"knowledge doc missing for '{title}': expected "
                f"agents/knowledge/{filename}",
            )


def check_seed_dry_run(errors: list[str]) -> None:
    """Execute cosmos_seed.py in SEED_DRY_RUN mode (build step 3 contract).

    Runs the LLM-generated row generators without Cosmos and enforces the
    seed contract (unique 'id', partition-key field on every row) via
    _seed_lib.SeedRunner. A script that passes here cannot fail the real
    seed for contract reasons.
    """
    seed = PROTO / "db" / "cosmos_seed.py"
    if not seed.exists():
        return  # check_required_paths already reports it
    env = {**os.environ, "SEED_DRY_RUN": "1"}
    try:
        result = subprocess.run(
            [sys.executable, seed.name],
            cwd=str(seed.parent),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        _err(errors, "cosmos_seed.py dry run timed out after 180s")
        return
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-8:]
        _err(
            errors,
            "cosmos_seed.py dry run failed (SEED_DRY_RUN=1):\n      "
            + "\n      ".join(tail),
        )


def _contrast_vs_white(hex_color: str) -> float | None:
    """WCAG contrast ratio of a #RRGGBB color against white, or None if malformed."""
    if not isinstance(hex_color, str) or not hex_color.startswith("#") or len(hex_color) != 7:
        return None

    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    try:
        r = int(hex_color[1:3], 16) / 255.0
        g = int(hex_color[3:5], 16) / 255.0
        b = int(hex_color[5:7], 16) / 255.0
    except ValueError:
        return None
    lum = 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)
    return 1.05 / (lum + 0.05)  # contrast against white (L=1.0)


def check_brand_contrast(errors: list[str]) -> None:
    """Hard-fail when branding.primaryColor has < 4.5:1 contrast against white.

    The frontend draws white text on the primary color (user message bubbles,
    primary buttons, focus rings). Anything below WCAG AA (4.5:1) renders as
    unreadable. We catch this here instead of in spec-validator because the
    BA agent sometimes picks a brand-accurate-but-too-light color (e.g. a
    customer's official light blue) and we don't want validation noise during
    spec authoring — only at deploy time.

    branding.accentColor gets a soft warning instead: the frontend uses it
    only decoratively (logo underline, agent-bubble border — never as a text
    background), so low contrast degrades polish, not readability.
    """
    if not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    branding = manifest.get("branding", {})

    # Malformed colors return None; the schema check already flags those.
    primary = branding.get("primaryColor", "")
    ratio = _contrast_vs_white(primary)
    if ratio is not None and ratio < 4.5:
        _err(
            errors,
            (
                f"branding.primaryColor {primary} has {ratio:.2f}:1 contrast "
                f"against white — WCAG AA requires 4.5:1 for readable button "
                f"labels and bubble text. Darken primary_color in spec.yaml."
            ),
        )

    accent = branding.get("accentColor", "")
    accent_ratio = _contrast_vs_white(accent)
    if accent_ratio is not None and accent_ratio < 3.0:
        print(
            f"  WARN: branding.accentColor {accent} has {accent_ratio:.2f}:1 "
            f"contrast against white (< 3:1). Accent is decorative-only, so "
            f"this does not block deploy, but underlines and borders may look "
            f"faint on light backgrounds."
        )


def main() -> None:
    print("preflight.py — Pre-deploy validation")
    errors: list[str] = []

    check_required_paths(errors)
    check_manifest_schema(errors)
    check_brand_contrast(errors)
    check_agent_outputs(errors)
    check_knowledge_docs(errors)
    check_seed_dry_run(errors)
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
