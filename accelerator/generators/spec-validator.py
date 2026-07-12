"""
spec-validator implementation: reads spec.yaml, validates, writes manifest.json.
"""
from __future__ import annotations
import hashlib
import json
import pathlib
import random
import re
import string
import sys
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Run: py -3 -m pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "spec.yaml"
STATE_DIR = ROOT / "generated" / "build-state"
MANIFEST_PATH = STATE_DIR / "manifest.json"
DONE_PATH = STATE_DIR / "01-spec-validator.done"


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not SPEC_PATH.exists():
        fail("spec.yaml not found at repo root")

    raw = SPEC_PATH.read_text(encoding="utf-8")
    spec = yaml.safe_load(raw)

    errors: list[str] = []

    required_top = [
        "customer", "branding", "use_case", "deployment", "agents", "tables",
        "documents", "starter_questions", "demo_persona", "foundry_iq",
        "model_deployments",
    ]
    for key in required_top:
        if key not in spec:
            errors.append(f"missing top-level key: {key}")

    slug = spec.get("customer", {}).get("slug", "")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        errors.append(f"customer.slug invalid (lowercase, hyphens only): {slug!r}")

    region = spec.get("deployment", {}).get("azure_region", "")
    if not re.fullmatch(r"[a-z]+[a-z0-9]*", region):
        errors.append(f"deployment.azure_region invalid: {region!r}")

    website = spec.get("customer", {}).get("website", "")
    if website and not website.startswith(("http://", "https://")):
        errors.append(f"customer.website must be an http(s) URL: {website!r}")

    # Routing keyword overlap check
    seen: dict[str, str] = {}
    overlaps: list[str] = []
    for agent in spec.get("agents", []):
        for kw in agent.get("routing_keywords", []) or []:
            key = kw.strip().lower()
            if key in seen and seen[key] != agent["name"]:
                overlaps.append(f"{kw!r} in both {seen[key]} and {agent['name']}")
            else:
                seen[key] = agent["name"]
    if overlaps:
        errors.append("routing_keywords overlap: " + "; ".join(overlaps))

    deployment_names = {d["deployment_name"] for d in spec.get("model_deployments", [])}
    for a in spec.get("agents", []):
        if a.get("model") not in deployment_names:
            errors.append(f"agent {a.get('name')} model {a.get('model')!r} not in model_deployments")

    if not spec.get("agents"):
        errors.append("no agents defined")
    if not spec.get("tables"):
        errors.append("no tables defined")
    if not spec.get("documents"):
        errors.append("no documents defined")

    for t in spec.get("tables", []):
        pk = t.get("partition_key", "")
        if not pk.startswith("/"):
            errors.append(f"table {t.get('name')} partition_key must start with '/': {pk!r}")

    if errors:
        print("Spec validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    # Derive resource names
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    rng = random.Random(checksum)
    suffix = "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(4))

    # Sticky suffix: when the previous manifest targeted the same product
    # (same slug + environment), keep its suffix so derived resource names
    # (Cosmos account, storage, ACR) stay stable across spec iterations.
    # Without this, every spec edit re-derives the suffix from the checksum
    # and the next deploy abandons the previously provisioned resources —
    # which defeats incremental rebuild. A new slug or environment is a new
    # product and gets a fresh suffix.
    slug_now = spec["customer"]["slug"]
    env_now = spec["deployment"]["environment_name"]
    if MANIFEST_PATH.exists():
        try:
            prev = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            prev_suffix = prev.get("deployment", {}).get("suffix", "")
            if (
                prev_suffix
                and prev.get("customer", {}).get("slug") == slug_now
                and prev.get("deployment", {}).get("environmentName") == env_now
            ):
                suffix = prev_suffix
        except (json.JSONDecodeError, OSError):
            pass

    storage_base = slug.replace("-", "")[:20]
    customer = spec["customer"]
    deployment = spec["deployment"]
    branding = spec["branding"]
    use_case = spec["use_case"]
    foundry = spec["foundry_iq"]
    chunking = foundry.get("chunking", {})
    demo = spec["demo_persona"]
    mock = spec.get("mock_api", {}) or {}

    env_name = deployment["environment_name"]

    # Short customer name for Bicep `customerName` param (max 20 chars, kebab-case).
    # When the spec slug is short enough, use it as-is; otherwise fall back to the
    # first hyphen-segment of the slug (e.g. "acco-engineered-systems" → "acco").
    if len(slug) <= 20:
        customer_short = slug
    else:
        first_segment = slug.split("-", 1)[0]
        customer_short = first_segment[:20] or slug[:20]

    # Demo theme slug for Bicep `demoTheme` param (max 30 chars).
    # Defaults to the spec slug when it fits; otherwise truncates the trailing
    # segment of the environment_name (e.g. "...-prototype" → "prototype").
    if len(slug) <= 30:
        demo_theme = slug
    else:
        env_tail = env_name.rsplit("-", 1)[-1] if "-" in env_name else env_name
        demo_theme = env_tail[:30] or slug[:30]

    # Length-budget warnings — flag now what would otherwise blow up at deploy time.
    resource_prefix = f"{customer_short}-{demo_theme}"
    warnings: list[str] = []

    # Starter-question ergonomics: the UI renders them as chips, which read
    # best as 4-5 short questions. Soft warnings, not failures.
    starter_qs = spec.get("starter_questions") or []
    if len(starter_qs) > 5:
        warnings.append(
            f"{len(starter_qs)} starter questions; the chip row reads best "
            f"with 4-5. Consider trimming spec.yaml starter_questions."
        )
    long_qs = [q for q in starter_qs if len(str(q)) > 80]
    if long_qs:
        warnings.append(
            f"{len(long_qs)} starter question(s) exceed 80 characters and "
            f"will wrap awkwardly as chips. Aim for <= 70 characters, e.g. "
            f"shorten: {str(long_qs[0])[:60]!r}..."
        )
    # Container Apps Environment name = `${resourcePrefix}-cae` (max 32 chars).
    if len(resource_prefix) + 4 > 32:
        warnings.append(
            f"resourcePrefix '{resource_prefix}' ({len(resource_prefix)} chars) plus '-cae' "
            f"exceeds the 32-char Container Apps Environment name limit. "
            f"Shorten customer.slug or deployment.environment_name in spec.yaml."
        )
    # Cosmos DB account name = `${resourcePrefix}-cosmos-{suffix}` (max 44 chars).
    if len(resource_prefix) + 12 > 44:
        warnings.append(
            f"resourcePrefix '{resource_prefix}' plus '-cosmos-XXXX' exceeds the 44-char "
            f"Cosmos DB account name limit."
        )

    manifest = {
        "specChecksum": checksum,
        "buildTimestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "customer": {
            "slug": slug,
            "name": customer["name"],
            "industry": customer.get("industry", ""),
            "website": customer.get("website", ""),
        },
        "deployment": {
            "environmentName": env_name,
            "resourceGroup": deployment["resource_group"],
            "location": region,
            "suffix": suffix,
            "customerShort": customer_short,
            "demoTheme": demo_theme,
        },
        "resources": {
            "cosmosAccountName": f"{resource_prefix}-cosmos",
            "cosmosDatabaseName": deployment["database_name"],
            "searchIndexName": foundry["index_name"],
            "storageAccount": f"{storage_base}st{suffix}",
            "miName": f"{customer_short}-{env_name}-id",
            "acrName": f"{storage_base}acr{suffix}",
        },
        "branding": {
            "agentName": branding["agent_name"],
            "primaryColor": branding["primary_color"],
            "accentColor": branding["accent_color"],
            "fontFamily": branding["font_family"],
            "logoUrl": branding.get("logo_url", ""),
            "welcomeMessage": branding.get("welcome_message", ""),
            "useCaseTitle": use_case["title"],
            "starterQuestions": (spec.get("starter_questions") or [])[:6],
            "personaName": demo["name"],
            "personaRole": demo["role"],
        },
        "tables": [
            {
                "name": t["name"],
                "partitionKey": t["partition_key"],
                "seedCount": t.get("seed_count", 100),
                "seedScenario": t.get("seed_scenario", ""),
                "description": t.get("description", ""),
                "columns": t.get("columns", []),
            }
            for t in spec["tables"]
        ],
        "agents": [
            {
                "name": a["name"],
                "role": a.get("role", ""),
                "model": a["model"],
                "routingKeywords": a.get("routing_keywords", []),
                "tools": a.get("tools", []),
                "skills": [s["name"] for s in (a.get("skills") or [])],
                "skillDescriptions": {
                    s["name"]: s.get("description", "")
                    for s in (a.get("skills") or [])
                },
                "systemPromptFocus": a.get("system_prompt_focus", ""),
                "responseFormat": a.get("response_format", []),
            }
            for a in spec["agents"]
        ],
        "modelDeployments": [
            {
                "deploymentName": m["deployment_name"],
                "model": m["model"],
                "version": m.get("version"),
                "sku": m.get("sku"),
                "capacity": m.get("capacity", 10),
            }
            for m in spec["model_deployments"]
        ],
        "documents": [d["title"] for d in spec["documents"]],
        "documentSpecs": [
            {
                "title": d["title"],
                "description": d.get("description", ""),
                "key_topics": d.get("key_topics", []),
            }
            for d in spec["documents"]
        ],
        "foundryIq": {
            "indexName": foundry["index_name"],
            "chunkSize": chunking.get("chunk_size", 1024),
            "overlap": chunking.get("overlap", 128),
        },
        "mockApiEnabled": bool(mock.get("enabled", False)),
        "mockApiEndpoints": mock.get("endpoints", []) or [],
        "aiLocation": region,
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Schema validation — catches contract drift between spec-validator and
    # downstream consumers (fill-templates, register_agents, hooks). Failing
    # here is preferred to failing mid-deploy.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from manifest_schema import validate as _validate_manifest  # noqa: E402
    schema_errors = _validate_manifest(manifest)
    if schema_errors:
        print("manifest.json failed schema validation:", file=sys.stderr)
        for e in schema_errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    from sentinels import write as _write_sentinel  # noqa: E402
    _write_sentinel(DONE_PATH, manifest["specChecksum"], [MANIFEST_PATH], manifest=manifest)

    print("[Step 1/7] Spec validation passed.")
    print(f"  Customer:  {customer['name']}")
    agent_names = ", ".join(a["name"] for a in spec["agents"])
    print(f"  Agents:    {len(spec['agents'])} ({agent_names})")
    table_names = ", ".join(t["name"] for t in spec["tables"])
    print(f"  Tables:    {len(spec['tables'])} ({table_names})")
    model_names = ", ".join(m["deployment_name"] for m in spec["model_deployments"])
    print(f"  Models:    {len(spec['model_deployments'])} ({model_names})")
    print(f"  Docs:      {len(spec['documents'])}")
    print(f"  Suffix:    {suffix}")
    if customer_short != slug:
        print(f"  CustomerShort: {customer_short} (derived; full slug exceeds 20 chars)")
    print(f"  Manifest written to {MANIFEST_PATH.relative_to(ROOT)}")
    for w in warnings:
        print(f"  WARN: {w}")


if __name__ == "__main__":
    main()
