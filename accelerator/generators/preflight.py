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
PREFLIGHT_CACHE = STATE / "preflight-cache.json"


def _err(checks: list[str], msg: str) -> None:
    checks.append(msg)


# ── Preflight result cache ────────────────────────────────────────────────
# Some checks (Bicep build, what-if, seed dry-run) are expensive but wholly
# determined by the inputs they read. We fingerprint those inputs and store
# the hash of the last successful invocation in preflight-cache.json; when
# the fingerprint matches, skip re-running. Any input change or a fresh
# workspace invalidates the cache automatically.

def _load_cache() -> dict:
    if not PREFLIGHT_CACHE.exists():
        return {}
    try:
        return json.loads(PREFLIGHT_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        PREFLIGHT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PREFLIGHT_CACHE.write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        pass  # cache is best-effort


def _fingerprint(paths: list[pathlib.Path]) -> str:
    """SHA-256 fingerprint over the concatenated bytes of every existing path.

    Missing files contribute the literal token 'MISSING:<relpath>' so removals
    invalidate the cache just like edits do.
    """
    import hashlib
    h = hashlib.sha256()
    for p in sorted(paths):
        if p.exists():
            h.update(f"{p.name}:".encode("utf-8"))
            try:
                h.update(p.read_bytes())
            except OSError:
                h.update(b"UNREADABLE")
        else:
            try:
                rel = p.relative_to(ROOT)
            except ValueError:
                rel = p
            h.update(f"MISSING:{rel}".encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _cache_hit(key: str, fingerprint: str) -> bool:
    return _load_cache().get(key) == fingerprint


def _cache_store(key: str, fingerprint: str) -> None:
    cache = _load_cache()
    cache[key] = fingerprint
    _save_cache(cache)


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
        PROTO / "agents" / "system_prompt_preamble.md",
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


def _bicep_infra_files() -> list[pathlib.Path]:
    """Every file that could change the outcome of bicep build / what-if."""
    infra = PROTO / "infra"
    files: list[pathlib.Path] = []
    if (infra / "main.bicep").exists():
        files.append(infra / "main.bicep")
    if (infra / "main.bicepparam").exists():
        files.append(infra / "main.bicepparam")
    modules = infra / "modules"
    if modules.exists():
        files.extend(sorted(modules.glob("*.bicep")))
    return files


def check_bicep_builds(errors: list[str]) -> None:
    """Run `az bicep build` on main.bicep. Soft-skip if az is not installed.

    Sentinel-cached: skipped when the last successful invocation covered
    the exact same infra fingerprint.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        return
    main_bicep = PROTO / "infra" / "main.bicep"
    if not main_bicep.exists():
        return

    infra_files = _bicep_infra_files()
    fingerprint = _fingerprint(infra_files)
    if _cache_hit("check_bicep_builds", fingerprint):
        print("  cached: check_bicep_builds (infra unchanged)")
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
        return
    _cache_store("check_bicep_builds", fingerprint)


def check_bicep_whatif(errors: list[str]) -> None:
    """Run `az deployment sub what-if` against the emitted Bicep.

    Catches policy denials, name collisions, region/SKU unavailability, and
    schema drift between `main.bicep` and `main.bicepparam` — all of which
    historically only surfaced after `azd up` had already started spending
    time provisioning. Hard-fail on real validation errors; soft-skip when
    the dev box can't reach Azure (not logged in, no subscription, offline).

    Skip is signalled by stderr containing one of the known "not ready"
    markers below. Any other non-zero exit is treated as a real failure.

    Sentinel-cached: skipped when the last successful invocation covered
    the exact same infra fingerprint AND target RG/location.
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

    # Fingerprint includes RG/location so what-if reruns when either changes,
    # even if the bicep bytes are identical.
    infra_files = _bicep_infra_files()
    fingerprint = _fingerprint(infra_files) + f"|rg={resource_group}|loc={location}"
    if _cache_hit("check_bicep_whatif", fingerprint):
        print("  cached: check_bicep_whatif (infra + target unchanged)")
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
        _cache_store("check_bicep_whatif", fingerprint)
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


def check_global_name_collisions(errors: list[str]) -> None:
    """Fail fast when a global-namespace resource name is already taken.

    Storage accounts, Container Registries, Cosmos DB accounts, and Foundry
    (Cognitive Services) custom subdomains share a global namespace. If a
    prior aborted build in a different resource group is still holding the
    computed names, `azd up` fails 90 s in with
    ``StorageAccountAlreadyTaken`` / ``AlreadyInUse`` / ``BadRequest: Dns
    record ... is already taken`` / ``CustomDomainInUse`` errors — the
    Hudson Advisors 2026-07-13 reproduction. This check surfaces those
    conflicts before `azd up` runs so the user can purge / delete the
    stranded resource group manually or via `cleanup-partial-deploy.py`.

    The Resource Graph query and the Cognitive Services domain-availability
    call run concurrently — sequentially they take 20-30 s each cold, in
    parallel the total is bounded by the slower of the two.
    """
    from concurrent.futures import ThreadPoolExecutor

    az = shutil.which("az") or shutil.which("az.cmd")
    if not az or not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return

    resources = manifest.get("resources") or {}
    deployment = manifest.get("deployment") or {}
    target_rg = (deployment.get("resourceGroup") or "").lower()

    storage_name = (resources.get("storageAccount") or "").lower()
    acr_name = (resources.get("acrName") or "").lower()
    cosmos_name = (resources.get("cosmosAccountName") or "").lower()
    customer_short = deployment.get("customerShort") or ""
    demo_theme = deployment.get("demoTheme") or ""
    foundry_subdomain = (
        f"{customer_short}-{demo_theme}-hub" if customer_short and demo_theme else ""
    )

    resource_types = {
        "microsoft.storage/storageaccounts": storage_name,
        "microsoft.containerregistry/registries": acr_name,
        "microsoft.documentdb/databaseaccounts": cosmos_name,
    }
    lookup: dict[str, str] = {n: t for t, n in resource_types.items() if n}

    # Cache-hit short-circuit — a prior "no collision" verdict for the same
    # manifest is still valid; nothing about the global-name namespace can
    # change without the manifest checksum moving.
    checksum = manifest.get("specChecksum") or ""
    fingerprint = f"{checksum}|{storage_name}|{acr_name}|{cosmos_name}|{foundry_subdomain}"
    if _cache_hit("check_global_name_collisions", fingerprint):
        print("  cached: check_global_name_collisions (manifest unchanged)")
        return

    def _run_graph_query() -> list[str]:
        """Return list of error strings from the Resource Graph probe."""
        local: list[str] = []
        if not lookup:
            return local
        names_list = ", ".join(f"'{n}'" for n in lookup)
        query = (
            "Resources | where tolower(name) in ("
            + names_list
            + ") | project name, type, resourceGroup, subscriptionId, location"
        )
        try:
            result = subprocess.run(
                [
                    az, "graph", "query", "-q", query,
                    "--first", "50", "--query", "data",
                    "-o", "json", "--only-show-errors",
                ],
                capture_output=True, text=True, shell=False, timeout=30,
            )
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
            return local
        if result.returncode != 0:
            return local
        try:
            rows = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return local
        for row in rows:
            found_name = (row.get("name") or "").lower()
            found_type = (row.get("type") or "").lower()
            found_rg = (row.get("resourceGroup") or "").lower()
            if found_rg == target_rg:
                continue
            if found_name in lookup and found_type == lookup[found_name]:
                local.append(
                    f"global name '{found_name}' ({found_type}) is "
                    f"already taken by resource in resource group "
                    f"'{row.get('resourceGroup')}' "
                    f"(subscription {row.get('subscriptionId')}). "
                    f"Delete the stranded resource before retrying, "
                    f"e.g. az group delete -g {row.get('resourceGroup')} "
                    f"--subscription {row.get('subscriptionId')} --yes."
                )
        return local

    def _run_domain_check() -> list[str]:
        local: list[str] = []
        if not foundry_subdomain:
            return local
        try:
            probe = subprocess.run(
                [
                    az, "cognitiveservices", "account", "check-domain-availability",
                    "--subdomain-name", foundry_subdomain,
                    "--type", "AIServices",
                    "-o", "json", "--only-show-errors",
                ],
                capture_output=True, text=True, shell=False, timeout=30,
            )
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
            return local
        if probe.returncode != 0:
            return local
        try:
            payload = json.loads(probe.stdout or "{}")
        except json.JSONDecodeError:
            return local
        if payload.get("nameAvailable") is False:
            reason = payload.get("reason") or "in use"
            message = payload.get("message") or ""
            local.append(
                f"Foundry subdomain '{foundry_subdomain}' is not "
                f"available ({reason}). {message} If you own the "
                f"holding account, delete or purge it; otherwise "
                f"shorten customer.slug or deployment.environment_name "
                f"in spec.yaml."
            )
        return local

    with ThreadPoolExecutor(max_workers=2) as pool:
        graph_future = pool.submit(_run_graph_query)
        domain_future = pool.submit(_run_domain_check)
        collected: list[str] = []
        for fut in (graph_future, domain_future):
            collected.extend(fut.result())

    for msg in collected:
        _err(errors, msg)

    # Cache the "no collision" result keyed on the manifest checksum. If the
    # manifest hasn't changed, subsequent preflight runs skip the ~40 s
    # Resource Graph + domain-availability round-trip. Any real collision is
    # returned before we reach this point so a positive result is never cached.
    if not collected:
        checksum = manifest.get("specChecksum") or ""
        fingerprint = f"{checksum}|{storage_name}|{acr_name}|{cosmos_name}|{foundry_subdomain}"
        _cache_store("check_global_name_collisions", fingerprint)


# ── Soft-skip markers shared by every az-CLI capacity check ────────────────
# When the dev box isn't logged in / has no subscription / can't reach ARM,
# capacity checks should soft-skip rather than block spec authoring. Real
# validation failures (returncode != 0 with none of these markers in stderr)
# are surfaced as hard errors.
_AZ_SOFT_SKIP_MARKERS = (
    "please run 'az login'",
    "az login",
    "no subscription found",
    "please select a subscription",
    "could not connect",
    "could not retrieve",
    "interactiveauthenticationrequired",
    "subscription is not registered",
    "aadsts70043",  # refresh token expired
)


def _az_soft_skip(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(m in low for m in _AZ_SOFT_SKIP_MARKERS)


# ── Authentication + tooling gates ─────────────────────────────────────────
# These run in the early preflight phase (preflight_env.py) so that a fresh
# machine or an expired token surfaces immediately, before any LLM work.


def check_azd_installed(errors: list[str]) -> None:
    """Hard-fail when `azd` is not on PATH.

    `azd up` is the deployment vector; without azd there is no way to
    provision or deploy, so failing early is strictly better than failing
    after 8 minutes of LLM generation.
    """
    if shutil.which("azd") or shutil.which("azd.exe"):
        return
    _err(
        errors,
        (
            "azd is not on PATH. Install the Azure Developer CLI from "
            "https://aka.ms/azd-install (Windows: winget install "
            "Microsoft.Azd) and reopen the terminal."
        ),
    )


def check_az_logged_in(errors: list[str]) -> None:
    """Hard-fail when `az account show` fails.

    Every capacity/quota probe below depends on an authenticated az session.
    Rather than let each probe soft-skip and mask an expired token, we
    check once, up-front, with a copy-pasteable remediation.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        _err(
            errors,
            (
                "Azure CLI (az) is not on PATH. Install from "
                "https://aka.ms/InstallAzureCliWindows (or "
                "https://aka.ms/InstallAzureCli) and reopen the terminal."
            ),
        )
        return
    try:
        result = subprocess.run(
            [az, "account", "show", "--only-show-errors"],
            capture_output=True, text=True, shell=False, timeout=15,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        _err(errors, "az account show did not respond within 15 s")
        return
    if result.returncode == 0:
        return
    stderr_low = (result.stderr or "").lower()
    if "aadsts70043" in stderr_low or "refresh token" in stderr_low:
        _err(
            errors,
            (
                "Azure CLI refresh token has expired. Run: "
                "az login --scope https://management.core.windows.net//.default"
            ),
        )
        return
    _err(
        errors,
        (
            "Not signed into Azure CLI. Run: az login "
            "(add --tenant <tenantId> if you have multiple tenants)."
        ),
    )


def check_az_subscription_matches_manifest(errors: list[str]) -> None:
    """Warn (hard-fail) when the active az subscription doesn't match the
    manifest's target subscription — if the manifest declares one.

    Today the manifest schema does not require `subscriptionId`; when it is
    absent we soft-skip. When it is present, running preflight with the
    wrong active subscription selected causes every capacity probe to
    report data for the wrong sub — and `azd up` deploys to whichever sub
    az is currently pointed at, which is the whole reason we care.
    """
    if not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    expected_sub = (
        manifest.get("subscriptionId")
        or manifest.get("deployment", {}).get("subscriptionId")
    )
    if not expected_sub:
        return  # manifest doesn't pin a subscription — nothing to compare
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        return  # check_az_logged_in already reported this
    try:
        result = subprocess.run(
            [az, "account", "show", "--query", "id", "-o", "tsv",
             "--only-show-errors"],
            capture_output=True, text=True, shell=False, timeout=15,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        return  # check_az_logged_in already reported this
    active = (result.stdout or "").strip()
    if active and active.lower() != expected_sub.lower():
        _err(
            errors,
            (
                f"active az subscription '{active}' does not match manifest "
                f"subscriptionId '{expected_sub}'. Run: "
                f"az account set --subscription {expected_sub}"
            ),
        )


def check_provider_registered(errors: list[str]) -> None:
    """Fail fast if `Microsoft.App` (Container Apps) is not registered.

    Container Apps is the runtime target for every prototype. If the provider
    isn't registered on the subscription, `azd up` blocks in preprovision
    while it registers and waits — several minutes wasted. Preflight can
    catch it in <1s and hand the user the exact `az provider register` line.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        return
    try:
        result = subprocess.run(
            [az, "provider", "show", "--namespace", "Microsoft.App",
             "--query", "registrationState", "-o", "tsv", "--only-show-errors"],
            capture_output=True, text=True, shell=False, timeout=30,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        if _az_soft_skip(result.stderr):
            return
        return  # tolerate transient CLI issues, don't hard-fail on show
    state = (result.stdout or "").strip()
    if state and state != "Registered":
        _err(
            errors,
            (
                f"Azure provider Microsoft.App is '{state}' — required for "
                f"Container Apps. Run: az provider register --namespace "
                f"Microsoft.App --wait"
            ),
        )


def check_model_quota(errors: list[str]) -> None:
    """Verify each manifest modelDeployments[] fits in-region.

    Ported from preprovision.{sh,ps1} so failures surface before any azd
    work, not partway into provisioning. Uses `az cognitiveservices usage
    list` and enforces `capacity <= (limit - currentValue)` per model. When
    the API returns no data for a model (fresh region / first deployment),
    we do NOT block — capacity data appears only after the first deployment.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az or not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    deployment = manifest.get("deployment", {})
    region = deployment.get("aiLocation") or deployment.get("location")
    if not region:
        return
    models = manifest.get("modelDeployments") or []
    if not models:
        return

    try:
        result = subprocess.run(
            [az, "cognitiveservices", "usage", "list", "--location", region,
             "-o", "json", "--only-show-errors"],
            capture_output=True, text=True, shell=False, timeout=45,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        if _az_soft_skip(result.stderr):
            return
        return  # partial CLI failures shouldn't block

    try:
        usages = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return

    # Build a lookup by lowercase name.value substring
    def _find(model_name: str) -> tuple[float, float] | None:
        for u in usages:
            name = ((u.get("name") or {}).get("value") or "").lower()
            if model_name.lower() in name:
                limit = float(u.get("limit") or 0)
                current = float(u.get("currentValue") or 0)
                return limit, current
        return None

    for md in models:
        model = md.get("model")
        required = float(md.get("capacity") or 0)
        if not model or required <= 0:
            continue
        found = _find(model)
        if found is None:
            continue  # no data — soft skip per docstring
        limit, current = found
        remaining = limit - current
        if remaining < required:
            _err(
                errors,
                (
                    f"model '{model}' quota in {region}: {remaining:g}k "
                    f"remaining (limit {limit:g}k, used {current:g}k), needs "
                    f"{required:g}k. Request more via aka.ms/oai-quota, lower "
                    f"modelDeployments[].capacity in spec.yaml, or change "
                    f"deployment.azure_region."
                ),
            )


def check_search_sku_capacity(errors: list[str]) -> None:
    """Probe AI Search SKU availability in the manifest region.

    AI Search 'basic' regularly hits `InsufficientResourcesAvailable` for
    the whole region (RESOLVED.md #3, BACKLOG.md #2, Hudson Advisors
    2026-07-13). The Microsoft.Search/locations quota API doesn't reflect
    the "no capacity right now on Basic SKU" state — Azure only surfaces
    that at provision time. Instead we use the checkNameAvailability API
    with the target SKU: if Basic isn't offered in this region right now,
    the check fails.

    A weaker signal is the region-wide `quotas` endpoint (services / total
    used). We keep that as a secondary check for a hard "region full"
    signal, but rely on the SKU probe for the softer "temporarily out"
    case that has repeatedly bitten prototype builds.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az or not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    deployment = manifest.get("deployment", {})
    resources = manifest.get("resources", {})
    # AI Search may be pinned to a different region via AZURE_SEARCH_LOCATION;
    # fall back to the manifest deployment location when it's not.
    region = deployment.get("searchLocation") or deployment.get("location")
    if not region:
        return
    search_name = (resources.get("searchName") or "hudson-search-preflight-probe").lower()
    subscription_id = manifest.get("subscriptionId")

    # ── Primary: SKU-availability via checkNameAvailability ────────────
    body = json.dumps({"name": search_name, "type": "Microsoft.Search/searchServices"})
    url = (
        f"https://management.azure.com/subscriptions/{{subscriptionId}}"
        f"/providers/Microsoft.Search/checkNameAvailability?api-version=2023-11-01"
    )
    if subscription_id:
        url = url.replace("{subscriptionId}", subscription_id)

    try:
        probe = subprocess.run(
            [
                az, "rest", "--method", "post", "--only-show-errors",
                "--url", url,
                "--body", body,
                "--headers", "Content-Type=application/json",
            ],
            capture_output=True, text=True, shell=False, timeout=30,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        probe = None

    if probe is not None and probe.returncode == 0:
        try:
            payload = json.loads(probe.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        # `nameAvailable` false with reason "AlreadyExists" is a name clash,
        # not a capacity signal — silently skip. Anything else with a
        # reason of "Invalid" indicates the SKU / region combo is rejected.
        reason = (payload.get("reason") or "").lower()
        message = (payload.get("message") or "").lower()
        if reason == "invalid" and (
            "not available" in message or "insufficient" in message
        ):
            _err(
                errors,
                (
                    f"AI Search Basic SKU is not offered in {region} right "
                    f"now ({payload.get('message')}). Set AZURE_SEARCH_LOCATION "
                    f"in the azd env to a nearby region (eastus / westus3 / "
                    f"northcentralus), or change deployment.azure_region."
                ),
            )
            return

    # ── Secondary: region-wide quota headroom ─────────────────────────
    quota_url = (
        f"https://management.azure.com/subscriptions/{{subscriptionId}}"
        f"/providers/Microsoft.Search/locations/{region}/quotas"
        f"?api-version=2023-11-01"
    )
    if subscription_id:
        quota_url = quota_url.replace("{subscriptionId}", subscription_id)

    try:
        result = subprocess.run(
            [az, "rest", "--method", "get", "--only-show-errors", "--url", quota_url],
            capture_output=True, text=True, shell=False, timeout=30,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        if _az_soft_skip(result.stderr):
            return
        return

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return
    quotas = payload.get("value") or []
    if not quotas:
        return

    for q in quotas:
        name = ((q.get("name") or {}).get("value") or "").lower()
        if "searchservices" not in name:
            continue
        limit = float(q.get("limit") or 0)
        current = float(q.get("currentValue") or 0)
        if limit and current >= limit:
            _err(
                errors,
                (
                    f"AI Search in {region} is at capacity "
                    f"({int(current)}/{int(limit)} services). Set "
                    f"AZURE_SEARCH_LOCATION to a nearby region."
                ),
            )


def check_cosmos_regional_capacity(errors: list[str]) -> None:
    """Heuristic probe for Cosmos DB regional health.

    Cosmos does not expose a first-class 'will a ZR account fit' quota API.
    We do two things:

    1. Confirm the manifest region is a supported Cosmos DB location.
    2. When the account will be zone-redundant, confirm the region reports
       `supportsAvailabilityZones=true`. Regions that don't have AZ support
       for Cosmos will fail `azd provision` with `ServiceUnavailable` (this
       is exactly the Hudson Advisors 2026-07-13 failure).

    Runtime capacity (bursty demand pressure) is not knowable from a
    read-only probe, so we cannot preempt every capacity failure — but the
    static region/SKU check catches the biggest recurring one.
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az or not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    region = manifest.get("deployment", {}).get("location")
    if not region:
        return

    try:
        result = subprocess.run(
            [az, "cosmosdb", "locations", "list", "-o", "json",
             "--only-show-errors"],
            capture_output=True, text=True, shell=False, timeout=30,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        if _az_soft_skip(result.stderr):
            return
        return
    try:
        locations = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return

    match = None
    for loc in locations:
        props = loc.get("properties") or {}
        name = (loc.get("name") or props.get("name") or "").lower().replace(" ", "")
        if name == region.lower().replace(" ", ""):
            match = loc
            break
    if match is None:
        _err(
            errors,
            (
                f"Cosmos DB is not listed as available in region '{region}'. "
                f"Change deployment.azure_region in spec.yaml to a Cosmos-"
                f"supported region."
            ),
        )
        return

    # Availability-zone support is reported in properties.supportsAvailabilityZones.
    props = match.get("properties") or {}
    supports_az = props.get("supportsAvailabilityZones")
    # Only assert AZ support when the account is going to be provisioned as
    # zone-redundant. The current cosmos.bicep default is non-ZR Serverless,
    # so we skip unless a manifest flag (deployment.zoneRedundant) turns it on.
    zone_redundant = manifest.get("deployment", {}).get("zoneRedundant", False)
    if zone_redundant and supports_az is False:
        _err(
            errors,
            (
                f"Cosmos DB in {region} does not support zone-redundant "
                f"accounts, but deployment.zoneRedundant=true. Either set "
                f"zoneRedundant=false in spec.yaml or pick an AZ-capable "
                f"region."
            ),
        )


# Image references we know are placeholders — a Container App still running
# one of these after Step 9 means `azd deploy` never pushed the real image
# (or was silently skipped due to a missing env value, see
# recover-bicep-outputs.py). We match on prefix so tags don't matter.
_PLACEHOLDER_IMAGE_PREFIXES = (
    "mcr.microsoft.com/azuredocs/containerapps-helloworld",
    "mcr.microsoft.com/k8se/quickstart",
    "mcr.microsoft.com/azure-app-service/samples/",
)


def check_container_image_not_placeholder(errors: list[str]) -> None:
    """Hard-fail when the deployed Container App is still running the ACA
    placeholder image.

    The Bicep default (`mcr.microsoft.com/azuredocs/containerapps-helloworld`)
    exists so that the initial provision can create the Container App
    resource before `azd deploy` pushes our image. But if `azd deploy` never
    ran — or ran and silently failed (e.g. missing
    `AZURE_CONTAINER_REGISTRY_ENDPOINT` after a partial provision, Hudson
    Advisors 2026-07-13) — the Container App stays on the placeholder and
    `/health` responds with the ACA welcome page. Users think the app is up.

    This check reads the actual container image reference from the deployed
    Container App and fails the build if it matches a known placeholder.
    Soft-skips when the Container App or resource group doesn't exist yet
    (fresh preflight before first deploy).
    """
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az or not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    deployment = manifest.get("deployment", {})
    rg = deployment.get("resourceGroup")
    if not rg:
        return
    subscription = (
        manifest.get("subscriptionId")
        or deployment.get("subscriptionId")
    )

    cmd = [
        az, "containerapp", "list",
        "--resource-group", rg,
        "--query",
        "[].{name:name, image:properties.template.containers[0].image}",
        "-o", "json", "--only-show-errors",
    ]
    if subscription:
        cmd.extend(["--subscription", subscription])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, shell=False, timeout=30,
        )
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        if _az_soft_skip(result.stderr):
            return
        # RG doesn't exist yet on a fresh build — that's fine.
        if "resourcegroupnotfound" in (result.stderr or "").lower():
            return
        return  # tolerate other transient CLI errors

    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return

    for row in rows:
        image = (row.get("image") or "").lower()
        if not image:
            continue
        for prefix in _PLACEHOLDER_IMAGE_PREFIXES:
            if image.startswith(prefix.lower()):
                _err(
                    errors,
                    (
                        f"Container App '{row.get('name')}' is still running "
                        f"placeholder image '{row.get('image')}'. `azd deploy` "
                        f"never pushed the real image, or it failed silently. "
                        f"Run: py -3 accelerator/scripts/recover-bicep-outputs.py "
                        f"(recovers missing outputs), then `azd deploy` from "
                        f"generated/prototype/."
                    ),
                )
                break


def check_azd_env_matches_manifest(errors: list[str]) -> None:
    """Hard-fail when the active azd env drifts from manifest.deployment.

    An azd env reused from an earlier customer can silently deploy the whole
    stack to the wrong region and resource group. We compare three fields:
    the active env name (from .azure/config.json) and, if the env's .env
    file exists, AZURE_LOCATION and AZURE_RESOURCE_GROUP. Only a partial
    mismatch is a hard failure — if no env exists yet, azd will create one
    from the manifest via the planned deploy wrapper (KNOWN_ISSUES #1/#3).
    """
    if not MANIFEST.exists():
        return
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    deployment = manifest.get("deployment", {})
    expected_env = deployment.get("environmentName")
    expected_location = deployment.get("location")
    expected_rg = deployment.get("resourceGroup")
    if not expected_env:
        return

    config_path = PROTO / ".azure" / "config.json"
    if not config_path.exists():
        return  # no env yet — nothing to drift from
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    active = cfg.get("defaultEnvironment")
    if active and active != expected_env:
        _err(
            errors,
            (
                f"active azd env '{active}' does not match manifest "
                f"environmentName '{expected_env}'. Run (from generated/prototype/): "
                f"azd env new {expected_env} --location {expected_location} "
                f"then azd env select {expected_env}."
            ),
        )
        # If the name is wrong, per-value checks below are misleading — skip.
        return

    if not active:
        return

    env_file = PROTO / ".azure" / active / ".env"
    if not env_file.exists():
        return
    env_vars: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_vars[key.strip()] = value.strip().strip('"').strip("'")

    actual_location = env_vars.get("AZURE_LOCATION")
    actual_rg = env_vars.get("AZURE_RESOURCE_GROUP")
    if expected_location and actual_location and actual_location != expected_location:
        _err(
            errors,
            (
                f"azd env '{active}' AZURE_LOCATION='{actual_location}' does not "
                f"match manifest.deployment.location '{expected_location}'. "
                f"Run (from generated/prototype/): azd env set AZURE_LOCATION "
                f"{expected_location}."
            ),
        )
    if expected_rg and actual_rg and actual_rg != expected_rg:
        _err(
            errors,
            (
                f"azd env '{active}' AZURE_RESOURCE_GROUP='{actual_rg}' does not "
                f"match manifest.deployment.resourceGroup '{expected_rg}'. "
                f"Run (from generated/prototype/): azd env set "
                f"AZURE_RESOURCE_GROUP {expected_rg}."
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

    Sentinel-cached: skipped when both cosmos_seed.py and _seed_lib.py
    fingerprint matches the last successful invocation.
    """
    seed = PROTO / "db" / "cosmos_seed.py"
    seed_lib = PROTO / "db" / "_seed_lib.py"
    if not seed.exists():
        return  # check_required_paths already reports it

    fingerprint = _fingerprint([seed, seed_lib])
    if _cache_hit("check_seed_dry_run", fingerprint):
        print("  cached: check_seed_dry_run (cosmos_seed.py unchanged)")
        return

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
        return
    _cache_store("check_seed_dry_run", fingerprint)


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
    # Phase 2 preflight — artifact + Bicep validation.
    #
    # Environment, authentication, subscription, azd env alignment, model
    # TPM quota, AI Search regional capacity, Cosmos DB regional/AZ support,
    # provider registration, and Foundry subdomain reservation are all run
    # in Phase 1 (preflight_env.py) after Step 1. Keeping them out of this
    # file's main() avoids duplicating expensive Azure calls; devlead runs
    # both scripts in the correct order.
    print("preflight.py — Phase 2 (artifacts + Bicep validation)")
    errors: list[str] = []

    check_required_paths(errors)
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
    check_bicep_whatif(errors)

    if errors:
        print(f"  FAILED — {len(errors)} issue(s):", file=sys.stderr)
        for e in errors:
            print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    print("  OK — generated prototype is ready to deploy.")


if __name__ == "__main__":
    main()
