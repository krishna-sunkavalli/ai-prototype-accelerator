#!/usr/bin/env python3
"""
accelerator/generators/fill-templates.py — Hydrate generated prototype files from manifest.json

Reads manifest.json and substitutes {{PLACEHOLDERS}} in all .tpl template files,
writing the hydrated output alongside the template (without the .tpl extension).

Run this after spec-validator generates manifest.json — no LLM required.

Usage:
    py -3 accelerator/generators/fill-templates.py
    py -3 accelerator/generators/fill-templates.py --target bicepparam
    py -3 accelerator/generators/fill-templates.py --target config
    py -3 accelerator/generators/fill-templates.py --target hooks

Replaces these files entirely from templates:
    generated/prototype/infra/main.bicepparam     <- accelerator/templates/prototype/infra/main.bicepparam.tpl
    generated/prototype/backend/config.py         <- accelerator/templates/prototype/backend/config.py.tpl
    generated/prototype/hooks/postprovision.sh    <- accelerator/templates/prototype/hooks/postprovision.sh.tpl
    generated/prototype/hooks/postprovision.ps1   <- accelerator/templates/prototype/hooks/postprovision.ps1.tpl
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TEMPLATE_ROOT = WORKSPACE_ROOT / "accelerator" / "templates" / "prototype"
OUTPUT_ROOT = WORKSPACE_ROOT / "generated" / "prototype"
MANIFEST_PATH = WORKSPACE_ROOT / "generated" / "build-state" / "manifest.json"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from model_catalog import MODEL_VERSION_DEFAULTS, resolve_version  # noqa: E402

TEMPLATES: list[tuple[pathlib.Path, pathlib.Path]] = [
    (TEMPLATE_ROOT / "infra" / "main.bicepparam.tpl", OUTPUT_ROOT / "infra" / "main.bicepparam"),
    (TEMPLATE_ROOT / "backend" / "config.py.tpl", OUTPUT_ROOT / "backend" / "config.py"),
    (TEMPLATE_ROOT / "hooks" / "postprovision.sh.tpl", OUTPUT_ROOT / "hooks" / "postprovision.sh"),
    (TEMPLATE_ROOT / "hooks" / "postprovision.ps1.tpl", OUTPUT_ROOT / "hooks" / "postprovision.ps1"),
]

TARGET_TEMPLATES: dict[str, list[tuple[pathlib.Path, pathlib.Path]]] = {
    "bicepparam": [TEMPLATES[0]],
    "config": [TEMPLATES[1]],
    "hooks": TEMPLATES[2:],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hydrate template-backed outputs from generated/build-state/manifest.json."
    )
    parser.add_argument(
        "--target",
        choices=["all", "bicepparam", "config", "hooks"],
        default="all",
        help="Hydrate one target group or all outputs (default: all).",
    )
    return parser.parse_args()


def title_to_filename(title: str) -> str:
    """Convert a document display title to its kebab-case .md filename."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)     # strip punctuation
    slug = re.sub(r"[\s-]+", "-", slug.strip())   # collapse spaces/hyphens
    return f"{slug}.md"


# Known issue #1: Unicode punctuation authored in spec.yaml (em dashes, smart
# quotes, ellipses) survives into manifest.json as proper UTF-8 but renders as
# `?` / U+FFFD inside the deployed Container App env vars. The corruption
# happens somewhere in the Bicep -> ARM -> ACA env-var pipeline (cp1252
# transcode on Windows hosts). Centralize sanitation here so every branding
# field that flows into Bicep/Python is ASCII-safe by construction.
_PUNCT_MAP = {
    "\u2013": "-",     # en dash
    "\u2014": " - ",   # em dash
    "\u2015": " - ",   # horizontal bar
    "\u2018": "'",     # left single quote
    "\u2019": "'",     # right single quote / apostrophe
    "\u201A": "'",     # single low-9 quote
    "\u201C": '"',     # left double quote
    "\u201D": '"',     # right double quote
    "\u201E": '"',     # double low-9 quote
    "\u2026": "...",   # ellipsis
    "\u00A0": " ",     # non-breaking space
    "\ufffd": "",      # replacement character (already-corrupted input)
}


def ascii_safe(value: str) -> str:
    """Normalize Unicode punctuation to ASCII equivalents.

    Branding strings (welcomeMessage, agentName, starterQuestions, ...) flow
    through Bicep param literals and end up as Container App env vars. The
    em-dash -> `?` mojibake observed in production builds is eliminated by
    forcing ASCII at the boundary between the manifest and the template
    substitution dict.
    """
    if not isinstance(value, str):
        return value
    out = value
    for src, dst in _PUNCT_MAP.items():
        out = out.replace(src, dst)
    # Collapse the doubled spaces that em-dash -> " - " expansion can create.
    out = re.sub(r"  +", " ", out)
    return out


def escape_python_string(value: str) -> str:
    """Escape a string for insertion inside a double-quoted Python literal."""
    return json.dumps(value)[1:-1]


def escape_bicep_string(value: str) -> str:
    """Escape a string for insertion inside a single-quoted Bicep literal."""
    normalized = " ".join(value.split())
    return normalized.replace("'", "\\'")


ASSETS_DIR = OUTPUT_ROOT / "frontend" / "public" / "assets"

_LOGO_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}
_LOGO_MAX_BYTES = 5 * 1024 * 1024


def _fetch_remote_logo(url: str) -> str | None:
    """Download the spec's remote logo into the app's local assets.

    The deployed prototype must be self-contained: hotlinking the customer's
    site means a broken brand mark whenever the URL moves, blocks hotlinks,
    or the demo network can't reach it. The URL only needs to be reachable
    during the build. Returns the web path to serve, or None on any problem
    (caller falls back to the generated initials badge).
    """
    import urllib.request

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (ai-prototype-accelerator logo fetch)"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get_content_type()
            data = resp.read(_LOGO_MAX_BYTES + 1)
    except Exception as exc:
        print(f"  WARN: logo download failed ({exc}); using generated badge", file=sys.stderr)
        return None

    ext = _LOGO_CONTENT_TYPES.get(content_type)
    if ext is None:
        # Some CDNs serve images as octet-stream; trust the URL suffix then.
        suffix = pathlib.Path(url.split("?", 1)[0]).suffix.lower()
        ext = suffix if suffix in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico") else None
    # Floor guards empty/truncated bodies only — SVG marks can be tiny, and
    # non-image error pages are already rejected by content type above.
    if ext is None or not (100 <= len(data) <= _LOGO_MAX_BYTES):
        print(
            f"  WARN: logo at {url} rejected (type={content_type}, "
            f"bytes={len(data)}); using generated badge",
            file=sys.stderr,
        )
        return None

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    target = ASSETS_DIR / f"brand-logo{ext}"
    target.write_bytes(data)
    print(f"  OK : brand logo downloaded → {target.relative_to(WORKSPACE_ROOT)} ({len(data)} bytes)")
    return f"/assets/brand-logo{ext}"


def _resolve_logo_url(
    logo_url: str, agent_name: str, primary: str, accent: str, website: str = ""
) -> str:
    """Turn the spec's logo_url into what the app should actually serve.

    - empty + website  → discover the logo on the company site, then download
    - empty            → generated initials badge (inline SVG data URI)
    - data: URI        → pass through (already inline)
    - /assets/... path → pass through (user placed the file manually)
    - http(s) URL      → download to local assets at build time; badge on failure
    """
    if not logo_url and website:
        # Zero-touch fallback: even when the BA never populated logo_url,
        # the build discovers the mark itself from customer.website.
        from logo_discovery import fetch_and_discover
        discovered = fetch_and_discover(website)
        if discovered:
            print(f"  OK : logo discovered on {website} → {discovered}")
            logo_url = discovered
    if not logo_url:
        return _build_inline_logo_svg(agent_name, primary, accent)
    if logo_url.startswith(("data:", "/assets/", "assets/")):
        return logo_url
    if logo_url.startswith(("http://", "https://")):
        local = _fetch_remote_logo(logo_url)
        return local or _build_inline_logo_svg(agent_name, primary, accent)
    return logo_url


def _build_inline_logo_svg(agent_name: str, primary: str, accent: str) -> str:
    """Return a `data:image/svg+xml,...` URI for a branded badge logo.

    Renders the agent's initials in white over a solid primary-color rounded
    square with a thin accent bar along the bottom edge. Used when spec.yaml
    does not provide branding.logo_url so every prototype ships with a
    coherent agent mark whose colors match the rest of the theme. The colors
    stay unblended on purpose: gradients between arbitrary customer palettes
    (e.g. blue->lime) collapse into muddy in-between tones.
    """
    # Split camelCase too, so "ConsumersOutagePilot" → "CO", not "C".
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", agent_name)
    initials = "".join(w[0] for w in spaced.split() if w)[:2].upper() or "AI"
    # Build an SVG without quotes that need escaping inside a data URI.
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 44 44'>"
        "<defs><clipPath id='r'><rect width='44' height='44' rx='11'/></clipPath></defs>"
        f"<rect width='44' height='44' rx='11' fill='{primary}'/>"
        f"<rect y='40' width='44' height='4' fill='{accent}' clip-path='url(#r)'/>"
        f"<text x='22' y='27' text-anchor='middle' font-family='Inter, system-ui, sans-serif' "
        f"font-size='16' font-weight='800' fill='#ffffff' letter-spacing='-0.5'>{initials}</text>"
        "</svg>"
    )
    # URL-encode the characters that browsers require encoded inside a data URI.
    encoded = (
        svg.replace("%", "%25")
        .replace("#", "%23")
        .replace("<", "%3C")
        .replace(">", "%3E")
        .replace('"', "%22")
    )
    return f"data:image/svg+xml,{encoded}"


def build_substitutions(manifest: dict) -> dict[str, str]:  # noqa: C901
    c = manifest["customer"]
    d = manifest["deployment"]
    r = manifest["resources"]
    b = manifest["branding"]
    agents: list[dict] = manifest.get("agents", [])
    tables: list[dict] = manifest.get("tables", [])
    documents: list[str] = manifest.get("documents", [])

    # -- Starter questions ---------------------------------------------------
    starter_qs: list[str] = [ascii_safe(q) for q in b.get("starterQuestions", [])]
    # ensure_ascii=False so non-ASCII chars (em dashes, etc.) stay as UTF-8
    # rather than being emitted as `\u2014` style escapes, which Bicep rejects
    # (BCP133: "The unicode escape sequence is not valid"). Then escape any
    # single quotes for the surrounding Bicep string literal so apostrophes
    # inside questions ("What's", "I'm") don't terminate the param string
    # (BCP103).
    sq_bicep = " " + json.dumps(starter_qs, ensure_ascii=False).replace("'", "\\'")
    sq_py_items = ",\n    ".join(json.dumps(q) for q in starter_qs)
    sq_python = f"[\n    {sq_py_items},\n]"

    # -- Agent names (triage first, then alphabetical) -----------------------
    agent_names = ["triage-prototype"] + [
        f'{a["name"]}-prototype'
        for a in sorted(agents, key=lambda a: a["name"])
    ]
    agent_names_py = "[\n    " + ",\n    ".join(f'"{n}"' for n in agent_names) + ",\n]"

    # -- Table names string (for echo messages) ------------------------------
    table_names_str = ", ".join(t["name"] for t in tables) if tables else "containers"

    # -- Mock API endpoints --------------------------------------------------
    mock_endpoints: list[dict] = manifest.get("mockApiEndpoints", [])
    mock_api_json = json.dumps(mock_endpoints)

    # -- Scenarios (one overview scenario per agent from manifest) -----------
    if agents and starter_qs:
        scenario_list: list[dict] = []
        chunk = max(1, len(starter_qs) // max(len(agents), 1))
        for i, agent in enumerate(agents):
            start = i * chunk
            end = start + chunk if i < len(agents) - 1 else len(starter_qs)
            steps = starter_qs[start:end] if start < len(starter_qs) else starter_qs[:2]
            scenario_list.append({
                "name": agent["name"].replace("Agent", " Agent"),
                "description": agent.get("role", f"Demo scenario for {agent['name']}."),
                "steps": steps,
            })

        scenario_parts: list[str] = []
        for scenario in scenario_list:
            steps_str = ",\n            ".join(json.dumps(step) for step in scenario["steps"])
            scenario_parts.append(
                f'    {{\n'
                f'        "name": {json.dumps(scenario["name"])},\n'
                f'        "description": {json.dumps(scenario["description"].strip())},\n'
                f'        "steps": [\n'
                f'            {steps_str},\n'
                f'        ],\n'
                f'    }}'
            )
        scenarios_python = "[\n" + ",\n".join(scenario_parts) + "\n]"
    else:
        scenarios_python = "[]  # Add demo scenarios here"

    # -- Document filenames for bash + PS1 arrays ----------------------------
    doc_filenames = [title_to_filename(doc) for doc in documents]
    if doc_filenames:
        bash_lines = "\n".join(f'  "{filename}"' for filename in doc_filenames)
        bash_array = f"(\n{bash_lines}\n)"
        ps_items = ",\n  ".join(f'"{filename}"' for filename in doc_filenames)
        ps_array = f"@(\n  {ps_items}\n)"
    else:
        bash_array = "()"
        ps_array = "@()"

    ai_region = manifest.get("aiLocation", "eastus")

    # -- Model deployments (Bicep array literal for foundry-iq) --------------
    model_deployments: list[dict] = manifest.get("modelDeployments", [])
    if model_deployments:
        md_lines: list[str] = []
        for md in model_deployments:
            name = md["model"]
            version = resolve_version(name, md.get("version"))
            if version == "latest" and name not in MODEL_VERSION_DEFAULTS:
                print(
                    f"  WARN: no version for model '{name}' and not in model_catalog "
                    "defaults; defaulting to 'latest'",
                    file=sys.stderr,
                )
            entry = (
                "  {\n"
                f"    deploymentName: '{md['deploymentName']}'\n"
                f"    model: '{name}'\n"
                f"    version: '{version}'\n"
                f"    capacity: {md.get('capacity', 10)}\n"
            )
            sku = md.get("sku")
            if sku:
                entry += f"    sku: '{sku}'\n"
            entry += "  }"
            md_lines.append(entry)
        model_deployments_bicep = "[\n" + "\n".join(md_lines) + "\n]"
    else:
        model_deployments_bicep = "[]"

    customer_name = ascii_safe(c["name"])
    agent_name = ascii_safe(b["agentName"])
    primary_color = b["primaryColor"]
    accent_color = b["accentColor"]
    font_family = ascii_safe(b["fontFamily"])
    # Remote logo URLs are downloaded into the app's assets at build time;
    # an empty logo_url triggers discovery on customer.website, then falls
    # back to a generated inline SVG badge whose colors match the palette.
    logo_url = _resolve_logo_url(
        b["logoUrl"], agent_name, primary_color, accent_color,
        website=c.get("website", ""),
    )
    welcome_message = ascii_safe(b["welcomeMessage"])
    use_case_title = ascii_safe(b["useCaseTitle"])
    persona_name = ascii_safe(b["personaName"])
    persona_role = ascii_safe(b["personaRole"])
    search_index_name = r["searchIndexName"]
    cosmos_database_name = r["cosmosDatabaseName"]

    return {
        "{{CUSTOMER_SLUG}}": c["slug"],
        "{{CUSTOMER_SHORT}}": d.get("customerShort", c["slug"]),
        "{{CUSTOMER_SHORT_BICEP}}": escape_bicep_string(d.get("customerShort", c["slug"])),
        "{{DEMO_THEME}}": d.get("demoTheme", c["slug"]),
        "{{DEMO_THEME_BICEP}}": escape_bicep_string(d.get("demoTheme", c["slug"])),
        "{{CUSTOMER_NAME}}": customer_name,
        "{{ENVIRONMENT_NAME}}": d["environmentName"],
        "{{AZURE_REGION}}": d["location"],
        "{{AI_REGION}}": ai_region,
        "{{COSMOS_DATABASE_NAME}}": cosmos_database_name,
        "{{SEARCH_INDEX_NAME}}": search_index_name,
        "{{MI_NAME}}": r["miName"],
        "{{STARTER_QUESTIONS_BICEP}}": sq_bicep,
        "{{STARTER_QUESTIONS_PYTHON}}": sq_python,
        "{{MODEL_DEPLOYMENTS_BICEP}}": model_deployments_bicep,
        "{{AGENT_NAMES_PYTHON}}": agent_names_py,
        "{{TABLE_NAMES_STR}}": table_names_str,
        "{{MOCK_API_ENDPOINTS_JSON}}": mock_api_json,
        "{{SCENARIOS_PYTHON}}": scenarios_python,
        "{{DOCS_BASH_ARRAY}}": bash_array,
        "{{DOCS_PS_ARRAY}}": ps_array,
        "{{USE_CASE_TITLE}}": use_case_title,
        "{{CUSTOMER_NAME_PY}}": escape_python_string(customer_name),
        "{{CUSTOMER_NAME_BICEP}}": escape_bicep_string(customer_name),
        "{{COSMOS_DATABASE_NAME_PY}}": escape_python_string(cosmos_database_name),
        "{{COSMOS_DATABASE_NAME_BICEP}}": escape_bicep_string(cosmos_database_name),
        "{{SEARCH_INDEX_NAME_PY}}": escape_python_string(search_index_name),
        "{{SEARCH_INDEX_NAME_BICEP}}": escape_bicep_string(search_index_name),
        "{{BRANDING_AGENT_NAME_PY}}": escape_python_string(agent_name),
        "{{BRANDING_AGENT_NAME_BICEP}}": escape_bicep_string(agent_name),
        "{{BRANDING_PRIMARY_COLOR_PY}}": escape_python_string(primary_color),
        "{{BRANDING_PRIMARY_COLOR_BICEP}}": escape_bicep_string(primary_color),
        "{{BRANDING_ACCENT_COLOR_PY}}": escape_python_string(accent_color),
        "{{BRANDING_ACCENT_COLOR_BICEP}}": escape_bicep_string(accent_color),
        "{{BRANDING_FONT_FAMILY_PY}}": escape_python_string(font_family),
        "{{BRANDING_FONT_FAMILY_BICEP}}": escape_bicep_string(font_family),
        "{{BRANDING_LOGO_URL_PY}}": escape_python_string(logo_url),
        "{{BRANDING_LOGO_URL_BICEP}}": escape_bicep_string(logo_url),
        "{{BRANDING_WELCOME_MESSAGE_PY}}": escape_python_string(welcome_message),
        "{{BRANDING_WELCOME_MESSAGE_BICEP}}": escape_bicep_string(welcome_message),
        "{{BRANDING_USE_CASE_TITLE_PY}}": escape_python_string(use_case_title),
        "{{BRANDING_USE_CASE_TITLE_BICEP}}": escape_bicep_string(use_case_title),
        "{{BRANDING_PERSONA_NAME_PY}}": escape_python_string(persona_name),
        "{{BRANDING_PERSONA_NAME_BICEP}}": escape_bicep_string(persona_name),
        "{{BRANDING_PERSONA_ROLE_PY}}": escape_python_string(persona_role),
        "{{BRANDING_PERSONA_ROLE_BICEP}}": escape_bicep_string(persona_role),
    }


def fill_template(
    template_path: pathlib.Path,
    output_path: pathlib.Path,
    substitutions: dict[str, str],
) -> bool:
    content = template_path.read_text(encoding="utf-8")
    for placeholder, value in substitutions.items():
        content = content.replace(placeholder, value)
    remaining = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", content)))
    if remaining:
        # Enterprise rule: a hydrated artifact must contain ZERO unresolved
        # placeholders. A leftover {{FOO}} silently shipping into deployed
        # infra or runtime config is a class of bug we refuse to allow.
        print(
            f"  FAIL: unfilled placeholders in {output_path.name}: "
            f"{remaining}",
            file=sys.stderr,
        )
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"  OK : {output_path.relative_to(WORKSPACE_ROOT)}")
    return True


def main() -> None:
    args = parse_args()

    if not MANIFEST_PATH.exists():
        print("ERROR: generated/build-state/manifest.json not found.")
        print("       Run spec-validator first (@devlead build).")
        sys.exit(1)

    from manifest_schema import load_and_validate  # noqa: E402
    try:
        manifest = load_and_validate(MANIFEST_PATH)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    substitutions = build_substitutions(manifest)

    print("fill-templates.py — Hydrating generated prototype files from manifest.json")
    print(f"  Customer : {manifest['customer']['name']}")
    print(f"  Use case : {manifest['branding']['useCaseTitle']}")
    print(f"  Region   : {manifest['deployment']['location']}")
    print(f"  Target   : {args.target}")
    print()

    templates = TEMPLATES if args.target == "all" else TARGET_TEMPLATES[args.target]

    ok = True
    for template_path, output_path in templates:
        if not template_path.exists():
            print(f"  SKIP : {template_path.name} (template not found)")
            continue
        ok = fill_template(template_path, output_path, substitutions) and ok

    print()
    if ok:
        if args.target == "all":
            print("Done. All templates hydrated — no LLM required.")
        else:
            print(f"Done. Target {args.target} hydrated — no LLM required.")
    else:
        print("FAILED. Unresolved placeholders detected — see FAIL messages above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
