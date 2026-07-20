#!/usr/bin/env python3
"""
wire_real_data_sources.py — Foundry IQ wiring for customer-owned real data sources.

Static template (no per-customer hydration needed; all customer-specific
values arrive as CLI args from postprovision.sh/.ps1). Do not edit the
copy under generated/prototype/hooks/ — patch this source under
accelerator/templates/prototype/hooks/ instead so the fix propagates to
every future build.

Only invoked when generated/prototype/hooks/data-grounding.json declares
mode: "real" (spec.yaml data_grounding.mode). For each declared data source
(kind: azure_blob | azure_sql — the only kinds Foundry IQ knowledge sources
support), this script:

  1. Grants the Azure AI Search service's own managed identity read access
     on the real resource (Storage Blob Data Reader for blob, Reader for
     SQL — ARM-level only; SQL's data-plane grant is a separate manual
     step, printed below, since it requires a live T-SQL connection).
  2. Discovers everything under the resource:
       - azure_blob: every container in the storage account
         (`az storage container list` — an ARM-visible, always-available
         operation).
       - azure_sql: every table/view in the database, via a best-effort
         T-SQL query over `sqlcmd` (tables are NOT an ARM concept — there
         is no CLI/ARM way to list them; if `sqlcmd` is unavailable or the
         query fails, this is a non-fatal warning with manual instructions,
         not a build failure).
  3. Creates one knowledge source per discovered container/table.
  4. Rebuilds the single knowledge base to reference every discovered
     knowledge source (never fails the whole postprovision — Foundry IQ
     wiring for a customer-owned external resource is best-effort by
     nature, since network/firewall/permission state is outside this
     accelerator's control).

Exit code is always 0. Failures are reported as WARNING lines so the rest
of postprovision (Cosmos seed, agent registration, etc.) is never blocked
by an external resource issue.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

EMBEDDING_MODEL_NAME = "text-embedding-3-large"
KB_REASONING_MODEL = "gpt-4o-mini"  # matches the hardcoded KB model in postprovision.{sh,ps1}.tpl


def _slug(value: str, max_len: int = 60) -> str:
    """Lowercase, alphanumeric + hyphen, no leading/trailing/doubled hyphens."""
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    s = re.sub(r"-+", "-", s)
    return s[:max_len].strip("-") or "src"


def _put_json(url: str, body: dict, admin_key: str) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={"api-key": admin_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # network errors, timeouts, etc.
        return 0, str(e)


def _assign_role(principal_id: str, role: str, scope: str, label: str) -> None:
    if not principal_id or not scope:
        print(f"   WARNING: skipping role assignment for {label} (missing principal or scope).")
        return
    result = subprocess.run(
        [
            "az", "role", "assignment", "create",
            "--assignee-object-id", principal_id,
            "--assignee-principal-type", "ServicePrincipal",
            "--role", role,
            "--scope", scope,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        detail_str = detail[-1][:300] if detail else "unknown error"
        print(f"   WARNING: could not assign '{role}' to {label} on {scope} -- {detail_str}")
        print("            You may need Owner/User Access Administrator permission on that "
              "resource, or grant this role manually in the Azure portal.")
    else:
        print(f"   OK: '{role}' -> {label}")


def _discover_blob_containers(account_name: str) -> list[str]:
    result = subprocess.run(
        [
            "az", "storage", "container", "list",
            "--account-name", account_name,
            "--auth-mode", "login",
            "-o", "json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        print(f"   WARNING: could not list containers in storage account '{account_name}' -- "
              f"{detail[-1][:300] if detail else 'unknown error'}")
        return []
    try:
        containers = json.loads(result.stdout)
        return [c["name"] for c in containers if c.get("name")]
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"   WARNING: could not parse container list for '{account_name}'.")
        return []


_SQL_DISCOVERY_QUERY = (
    "SET NOCOUNT ON; SELECT TABLE_SCHEMA + N'.' + TABLE_NAME FROM "
    "INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE IN (N'BASE TABLE', N'VIEW') ORDER BY 1;"
)


def _discover_sql_tables(server_fqdn: str, database: str) -> list[str]:
    sqlcmd = shutil.which("sqlcmd")
    if not sqlcmd:
        print("   WARNING: 'sqlcmd' not found on this machine -- cannot auto-discover "
              "tables/views. Install the SQL Server command line tools, or list tables "
              "manually and add a search-index knowledge source in the Foundry portal instead.")
        return []
    result = subprocess.run(
        [
            sqlcmd,
            "-S", server_fqdn,
            "-d", database,
            "-G",
            "--authentication-method=ActiveDirectoryDefault",
            "-h", "-1",
            "-W",
            "-Q", _SQL_DISCOVERY_QUERY,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        print(f"   WARNING: table/view discovery failed against {server_fqdn}/{database} -- "
              f"{detail[-1][:300] if detail else 'unknown error'}")
        print("            This is expected if the caller's identity doesn't yet have read "
              "access. Grant it, then re-run `azd deploy` (or `azd up`) to retry:")
        print(f"              CREATE USER [<search-service-name>] FROM EXTERNAL PROVIDER;")
        print(f"              EXEC sp_addrolemember 'db_datareader', [<search-service-name>];")
        return []
    tables = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return tables


def _blob_ks_body(ks_name: str, resource_id: str, container: str, ai_endpoint: str, description: str) -> dict:
    return {
        "name": ks_name,
        "kind": "azureBlob",
        "description": description,
        "azureBlobParameters": {
            "connectionString": f"ResourceId={resource_id};",
            "containerName": container,
            "folderPath": None,
            "isADLSGen2": False,
            "ingestionParameters": {
                "contentExtractionMode": "minimal",
                # See RESOLVED.md: chatCompletionModel must not be set when
                # disableImageVerbalization is true -- 400 otherwise.
                "disableImageVerbalization": True,
                "embeddingModel": {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": ai_endpoint,
                        "deploymentId": EMBEDDING_MODEL_NAME,
                        "modelName": EMBEDDING_MODEL_NAME,
                    },
                },
            },
        },
    }


def _sql_ks_body(ks_name: str, resource_id: str, database: str, table_or_view: str, description: str) -> dict:
    return {
        "name": ks_name,
        "kind": "indexedSql",
        "description": description,
        "indexedSqlParameters": {
            "connectionString": f"Database={database};ResourceId={resource_id};Connection Timeout=30;",
            "tableOrView": table_or_view,
            "ingestionParameters": {
                # indexedSql only supports "minimal" -- image extraction/
                # verbalization and chatCompletionModel have no effect for
                # row-based SQL ingestion.
                "contentExtractionMode": "minimal",
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-grounding", required=True)
    parser.add_argument("--search-endpoint", required=True)
    parser.add_argument("--admin-key", required=True)
    parser.add_argument("--api-version", required=True)
    parser.add_argument("--ai-endpoint", required=True)
    parser.add_argument("--search-principal-id", default="")
    parser.add_argument("--index-name", required=True)
    parser.add_argument("--customer-name", default="")
    parser.add_argument("--retrieval-instructions", default="")
    args = parser.parse_args()

    try:
        with open(args.data_grounding, "r", encoding="utf-8") as f:
            grounding = json.load(f)
    except (OSError, json.JSONDecodeError):
        print("   INFO: no data-grounding.json found or unreadable; nothing to wire.")
        return 0

    if grounding.get("mode") != "real":
        print("   INFO: dataGrounding.mode is not 'real'; nothing to wire.")
        return 0

    data_sources = grounding.get("dataSources") or []
    if not data_sources:
        print("   WARNING: dataGrounding.mode is 'real' but no data sources were resolved.")
        return 0

    search_endpoint = args.search_endpoint.rstrip("/")
    ks_names: list[str] = []
    rbac_done: set[str] = set()

    for ds in data_sources:
        name = ds.get("name", "")
        kind = ds.get("kind", "")
        resource_id = ds.get("resourceId", "")
        description = f"{name} (customer-provided {kind} data source)"
        print(f"   Wiring data source '{name}' (kind={kind})...")

        if kind == "azure_blob":
            if resource_id not in rbac_done:
                _assign_role(args.search_principal_id, "Storage Blob Data Reader", resource_id,
                             f"Search -> {name} (Storage)")
                rbac_done.add(resource_id)
            account_name = name.split("/")[0]
            containers = _discover_blob_containers(account_name)
            if not containers:
                print(f"   WARNING: no containers found/accessible in '{account_name}'; skipping.")
                continue
            for container in containers:
                ks_name = f"{_slug(account_name, 40)}-{_slug(container, 15)}-ks"
                body = _blob_ks_body(ks_name, resource_id, container, args.ai_endpoint, description)
                status, resp_text = _put_json(
                    f"{search_endpoint}/knowledgesources/{ks_name}?api-version={args.api_version}",
                    body, args.admin_key,
                )
                if status in (200, 201, 204):
                    print(f"   OK: knowledge source '{ks_name}' created/updated (container '{container}').")
                    ks_names.append(ks_name)
                else:
                    print(f"   WARNING: knowledge source '{ks_name}' failed (HTTP {status}). {resp_text[:300]}")

        elif kind == "azure_sql":
            if resource_id not in rbac_done:
                _assign_role(args.search_principal_id, "Reader", resource_id, f"Search -> {name} (SQL)")
                rbac_done.add(resource_id)
            parts = name.split("/")
            if len(parts) != 2:
                print(f"   WARNING: expected 'server/database' for azure_sql, got '{name}'; skipping.")
                continue
            server, database = parts
            server_fqdn = server if "." in server else f"{server}.database.windows.net"
            tables = _discover_sql_tables(server_fqdn, database)
            if not tables:
                print(f"   WARNING: no tables/views discovered in '{name}'; skipping.")
                continue
            for table in tables:
                ks_name = f"{_slug(database, 30)}-{_slug(table, 25)}-ks"
                body = _sql_ks_body(ks_name, resource_id, database, table, description)
                status, resp_text = _put_json(
                    f"{search_endpoint}/knowledgesources/{ks_name}?api-version={args.api_version}",
                    body, args.admin_key,
                )
                if status in (200, 201, 204):
                    print(f"   OK: knowledge source '{ks_name}' created/updated (table '{table}').")
                    ks_names.append(ks_name)
                else:
                    print(f"   WARNING: knowledge source '{ks_name}' failed (HTTP {status}). {resp_text[:300]}")

        else:
            print(f"   WARNING: unsupported kind '{kind}' for data source '{name}'; skipping.")

    if not ks_names:
        print("   WARNING: no knowledge sources were successfully wired; knowledge base not updated.")
        return 0

    kb_name = f"{args.index_name}-kb"
    kb_body = {
        "name": kb_name,
        "description": f"Foundry IQ knowledge base for {args.customer_name} "
                        f"(grounded in {len(ks_names)} real data source knowledge source(s)).",
        "knowledgeSources": [{"name": n} for n in ks_names],
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": args.ai_endpoint,
                "deploymentId": KB_REASONING_MODEL,
                "modelName": KB_REASONING_MODEL,
            },
        }],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "low"},
        "retrievalInstructions": args.retrieval_instructions,
    }
    status, resp_text = _put_json(
        f"{search_endpoint}/knowledgebases/{kb_name}?api-version={args.api_version}",
        kb_body, args.admin_key,
    )
    if status in (200, 201, 204):
        print(f"   OK: knowledge base '{kb_name}' updated with {len(ks_names)} knowledge source(s).")
    else:
        print(f"   WARNING: knowledge base update failed (HTTP {status}). {resp_text[:300]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
