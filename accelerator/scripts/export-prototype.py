#!/usr/bin/env python3
"""
accelerator/scripts/export-prototype.py — Lift the built prototype into a
standalone repository.

The generated prototype is designed to be self-contained; this script makes
that real: it copies generated/prototype/ to a new directory, writes a
product README derived from the manifest, preserves the originating
spec.yaml as docs/spec.yaml (the spec is the product's PRD), and optionally
initialises a git repository. The exported tree has no dependency on the
accelerator — it is the seed of the real product a team inherits when a
validated prototype graduates.

Usage:
    py -3 accelerator/scripts/export-prototype.py <target-dir> [--git]

Rules:
    - target-dir must not exist (or must be an empty directory)
    - build state (.azure/, __pycache__, *.pyc) is never exported
    - refuses to run when generated/prototype or the manifest is missing
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTO = ROOT / "generated" / "prototype"
MANIFEST_PATH = ROOT / "generated" / "build-state" / "manifest.json"
SPEC_PATH = ROOT / "spec.yaml"

_EXCLUDE_DIRS = {".azure", "__pycache__", ".git"}


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {n for n in names if n in _EXCLUDE_DIRS or n.endswith(".pyc")}
    return ignored


def _readme(manifest: dict) -> str:
    customer = manifest["customer"]["name"]
    branding = manifest["branding"]
    agents = manifest.get("agents", [])
    tables = manifest.get("tables", [])
    documents = manifest.get("documents", [])
    deployment = manifest["deployment"]

    agent_rows = "\n".join(
        f"| {a['name']} | {a.get('role', '')} | {a.get('model', '')} |" for a in agents
    )
    table_names = ", ".join(t["name"] for t in tables)
    doc_list = "\n".join(f"- {d}" for d in documents)
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""# {branding.get('agentName', customer)} — {branding.get('useCaseTitle', 'AI Prototype')}

A multi-agent AI assistant for **{customer}**, built on Microsoft Foundry and
the Microsoft Agent Framework: a triage agent routes each question to a
domain specialist grounded in Azure Cosmos DB data and Azure AI Search
documents. FastAPI backend on Azure Container Apps, single managed identity,
provisioned with Bicep, deployed with `azd up`.

## Specialists

| Agent | Role | Model |
|---|---|---|
{agent_rows}

## Data

- **Cosmos DB containers:** {table_names}
- **Knowledge documents:**
{doc_list}

## Deploy

Prerequisites: [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli),
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd),
an Azure subscription with quota for the configured OpenAI model deployments.

```bash
az login
azd auth login
azd env new {deployment.get('environmentName', 'prototype')}
azd up
```

`azd up` provisions the infrastructure, registers the Foundry agents, seeds
the database, indexes the documents, and prints the app URL.

## Product definition

This application was generated from a single product spec. The originating
spec lives at [docs/spec.yaml](docs/spec.yaml) — treat it as the PRD: it
declares the use case, the specialists, the data model, and the branding
this codebase implements.

---
*Exported from ai-prototype-accelerator on {exported_at}
(spec checksum `{manifest.get('specChecksum', 'unknown')}`).*
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the built prototype to a standalone repo")
    parser.add_argument("target", type=pathlib.Path, help="Target directory (new or empty)")
    parser.add_argument("--git", action="store_true", help="git init + initial commit in the target")
    args = parser.parse_args()

    if not PROTO.exists() or not any(PROTO.iterdir()):
        print("ERROR: generated/prototype is missing or empty — run @devlead build first.", file=sys.stderr)
        return 1
    if not MANIFEST_PATH.exists():
        print("ERROR: generated/build-state/manifest.json not found — run @devlead build first.", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    target: pathlib.Path = args.target.resolve()
    if target.exists():
        if not target.is_dir():
            print(f"ERROR: {target} exists and is not a directory.", file=sys.stderr)
            return 1
        if any(target.iterdir()):
            print(f"ERROR: {target} is not empty — refusing to export over existing content.", file=sys.stderr)
            return 1
        target.rmdir()  # copytree wants to create it

    print(f"Exporting prototype for {manifest['customer']['name']}")
    shutil.copytree(PROTO, target, ignore=_ignore)

    (target / "README.md").write_text(_readme(manifest), encoding="utf-8")
    docs_dir = target / "docs"
    docs_dir.mkdir(exist_ok=True)
    if SPEC_PATH.exists():
        shutil.copy2(SPEC_PATH, docs_dir / "spec.yaml")

    file_count = sum(1 for p in target.rglob("*") if p.is_file())
    print(f"  Copied  : {file_count} files → {target}")
    print("  Wrote   : README.md (from manifest), docs/spec.yaml (the PRD)")

    if args.git:
        try:
            subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True,
                           capture_output=True, text=True)
            subprocess.run(["git", "add", "-A"], cwd=target, check=True,
                           capture_output=True, text=True)
            subprocess.run(
                ["git", "commit", "-m",
                 f"Initial commit - exported from ai-prototype-accelerator "
                 f"(spec {manifest.get('specChecksum', 'unknown')})"],
                cwd=target, check=True, capture_output=True, text=True,
            )
            print("  Git     : initialised on 'main' with initial commit")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            print(f"  Git     : FAILED ({detail.strip()}) — files exported, init manually.", file=sys.stderr)
            return 1

    print("Export complete. The target is self-contained — deploy from it with `azd up`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
