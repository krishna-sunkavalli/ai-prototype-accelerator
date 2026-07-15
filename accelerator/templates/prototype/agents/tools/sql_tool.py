"""
ai-prototype-accelerator — Cosmos DB Tool
Static scaffold file — do NOT modify when processing spec.yaml.

Auth: Azure RBAC via DefaultAzureCredential (never keys or passwords).
Write operations require explicit user confirmation before execution.
"""
import os
import re
import json
import logging
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey, exceptions as cosmos_exc
from azure.identity import DefaultAzureCredential

from agents.tools import activity, confirmations

logger = logging.getLogger(__name__)

# Operation verbs that require user confirmation before execution
_WRITE_OPS = {"upsert", "create", "replace", "patch", "delete"}


# ── Client ─────────────────────────────────────────────────────────────────────

# CosmosClient is designed to be a long-lived singleton: it owns connection
# pools and the credential caches tokens. Constructing one per tool call adds
# seconds across a multi-tool answer and hammers the token endpoint.
_COSMOS_CLIENT: CosmosClient | None = None


def get_cosmos_client() -> CosmosClient:
    """Return the process-wide CosmosClient (Entra ID RBAC), creating it once."""
    global _COSMOS_CLIENT
    if _COSMOS_CLIENT is None:
        client_id = os.environ["AZURE_CLIENT_ID"]
        credential = DefaultAzureCredential(managed_identity_client_id=client_id)
        _COSMOS_CLIENT = CosmosClient(
            url=os.environ["AZURE_COSMOS_ENDPOINT"],
            credential=credential
        )
    return _COSMOS_CLIENT


def get_container(container_name: str):
    """Return a Cosmos DB container proxy."""
    client = get_cosmos_client()
    db = client.get_database_client(os.environ["AZURE_COSMOS_DATABASE"])
    return db.get_container_client(container_name)


# ── Health check ───────────────────────────────────────────────────────────────

def check_cosmos_health() -> dict:
    """Verify Cosmos DB connectivity. Returns {"status": "ok"} or raises."""
    client = get_cosmos_client()
    db_name = os.environ["AZURE_COSMOS_DATABASE"]
    client.get_database_client(db_name).read()
    return {"status": "ok"}


# ── Agent tool: run_sql_query ──────────────────────────────────────────────────
# Called by the TOOL_DISPATCH in orchestrator.py.
# Accepts Cosmos SQL queries using either the container name in FROM
# (e.g. SELECT * FROM <container_name> WHERE ...) or the canonical alias
# (SELECT * FROM c WHERE ...).
# Extracts the container name from the FROM clause and normalises to Cosmos SQL.
#
# The container list is discovered at runtime from the Cosmos database itself,
# so this file stays spec-agnostic across builds.

_KNOWN_CONTAINERS_CACHE: list[str] | None = None


def _known_containers() -> list[str]:
    """Return the list of container names in the prototype's Cosmos database.

    Discovered lazily on first call and cached for the process lifetime.
    Falls back to an empty list if discovery fails — callers must handle that.
    """
    global _KNOWN_CONTAINERS_CACHE
    if _KNOWN_CONTAINERS_CACHE is None:
        try:
            client = get_cosmos_client()
            db = client.get_database_client(os.environ["AZURE_COSMOS_DATABASE"])
            _KNOWN_CONTAINERS_CACHE = [c["id"] for c in db.list_containers()]
        except Exception as exc:
            logger.warning("Failed to enumerate Cosmos containers: %s", exc)
            _KNOWN_CONTAINERS_CACHE = []
    return _KNOWN_CONTAINERS_CACHE


def run_sql_query(query: str, params: list | None = None, container: str | None = None) -> list[dict]:
    """
    Execute a Cosmos DB query on behalf of an agent tool call.

    The query may reference container names directly in the FROM clause
    (e.g. ``SELECT * FROM loan_assets WHERE c.assetId = @id``).
    The function strips the container name and routes to the right container.

    Parameters
    ----------
    query : str
        Cosmos SQL query, optionally using a container name as the FROM source.
    params : list, optional
        Named parameters as ``[{"name": "@paramName", "value": value}, ...]``.
        Also accepts dict format ``{"name": value, ...}`` for backward compat.
    container : str, optional
        Override the container to query.  Inferred from the FROM clause if omitted.
    """
    known = _known_containers()

    # ── Determine container from FROM clause if not supplied ──────────────────
    if container is None:
        for name in known:
            if re.search(r'\bFROM\s+' + re.escape(name) + r'\b', query, re.IGNORECASE):
                container = name
                break
        if container is None:
            if len(known) == 1:
                container = known[0]
            else:
                raise ValueError(
                    "run_sql_query: could not infer Cosmos container from query. "
                    f"Known containers: {known}. Pass `container=` explicitly or "
                    "include the container name in the FROM clause."
                )

    # ── Normalise FROM <table> → FROM c for Cosmos SQL ────────────────────────
    cosmos_query = re.sub(
        r'\bFROM\s+' + re.escape(container) + r'\b',
        'FROM c',
        query,
        flags=re.IGNORECASE,
    )
    # Also replace any <table>. prefix (e.g. loan_assets.assetId → c.assetId)
    cosmos_query = re.sub(
        r'\b' + re.escape(container) + r'\.',
        'c.',
        cosmos_query,
        flags=re.IGNORECASE,
    )

    # ── Convert params → Cosmos parameter list ────────────────────────────────
    # Accepts either:
    #   dict: {"status": "reo"}  → [{"name": "@status", "value": "reo"}]
    #   list: [{"name": "@status", "value": "reo"}]  (already correct format)
    cosmos_params: list[dict] | None = None
    if params:
        if isinstance(params, list):
            # Already in Cosmos format — ensure "@" prefix on names
            cosmos_params = [
                {"name": p["name"] if p["name"].startswith("@") else f"@{p['name']}", "value": p["value"]}
                for p in params
                if isinstance(p, dict) and "name" in p and "value" in p
            ] or None
        elif isinstance(params, dict):
            cosmos_params = [
                {"name": k if k.startswith("@") else f"@{k}", "value": v}
                for k, v in params.items()
            ]

    activity.notify(f"Querying {container.replace('_', ' ')} data...")
    logger.info("run_sql_query | container=%s | query=%.120s | params=%s", container, cosmos_query, cosmos_params)
    try:
        return query_items(container, cosmos_query, cosmos_params)
    except Exception as exc:
        logger.error("run_sql_query FAILED | container=%s | query=%.200s | params=%s | error=%s",
                     container, cosmos_query, cosmos_params, exc)
        # Never let the raw SDK/Cosmos exception (which can include hostnames,
        # IPs, or firewall/policy detail) propagate to MAF's tool-result text —
        # that string is what the model sees and may paraphrase back to the user.
        raise RuntimeError("The requested data is temporarily unavailable.") from exc


# ── Tool functions ─────────────────────────────────────────────────────────────

def query_items(
    container_name: str,
    query: str,
    parameters: list[dict] | None = None,
    partition_key: Any = None
) -> list[dict]:
    """
    Execute a Cosmos DB SQL query against a container.
    Always a read — returns list of dicts.
    Use parameters list format: [{"name": "@param", "value": val}, ...]
    """
    container = get_container(container_name)
    kwargs: dict = {"query": query, "enable_cross_partition_query": partition_key is None}
    if parameters:
        kwargs["parameters"] = parameters
    if partition_key is not None:
        kwargs["partition_key"] = partition_key

    items = list(container.query_items(**kwargs))
    _log_op("QUERY", container_name, f"{query[:100]} | rows={len(items)}")
    return items


def upsert_item(container_name: str, item: dict) -> Any:
    """
    Upsert a document — requires explicit user confirmation first.

    Parks the operation with the confirmation registry, pushes a
    confirmation card to the chat UI, and tells the model to wait. The
    write only executes when the user confirms (execute_write, called by
    orchestrator.handle_confirmation).
    """
    parked = confirmations.request(
        message=_build_confirmation_message("upsert", container_name, item),
        operation="upsert",
        pending={"container": container_name, "item": item},
    )
    if not parked:
        return {"error": "Write operations require an active chat session to confirm."}
    return {
        "status": "awaiting_user_confirmation",
        "operation": "upsert",
        "container": container_name,
        "item_id": item.get("id", "(new)"),
        "note": "Tell the user you need their confirmation before writing; a confirmation prompt is on their screen.",
    }


def delete_item(container_name: str, item_id: str, partition_key: Any) -> Any:
    """
    Delete a document by id — requires explicit user confirmation first.
    Same parked-confirmation flow as upsert_item.
    """
    parked = confirmations.request(
        message=f"This will permanently delete item '{item_id}' from '{container_name}'. Proceed?",
        operation="delete",
        pending={"container": container_name, "item_id": item_id, "partition_key": partition_key},
    )
    if not parked:
        return {"error": "Write operations require an active chat session to confirm."}
    return {
        "status": "awaiting_user_confirmation",
        "operation": "delete",
        "container": container_name,
        "item_id": item_id,
        "note": "Tell the user you need their confirmation before deleting; a confirmation prompt is on their screen.",
    }


def execute_write(operation: str, **kwargs) -> dict:
    """
    Execute a confirmed write operation.
    Called only after user explicitly confirms via WebSocket.

    operation: "upsert" or "delete"
    For upsert: pass container=str, item=dict
    For delete: pass container=str, item_id=str, partition_key=Any
    """
    if operation not in _WRITE_OPS:
        raise ValueError(f"execute_write called with unknown operation: {operation}")

    container_name = kwargs["container"]
    container = get_container(container_name)

    try:
        if operation == "upsert":
            item = kwargs["item"]
            result = container.upsert_item(item)
            _log_op("UPSERT", container_name, str(item.get("id", "?")))
            return {"status": "success", "id": result.get("id")}

        elif operation == "delete":
            item_id = kwargs["item_id"]
            partition_key = kwargs["partition_key"]
            container.delete_item(item=item_id, partition_key=partition_key)
            _log_op("DELETE", container_name, item_id)
            return {"status": "success", "id": item_id}

    except cosmos_exc.CosmosHttpResponseError as e:
        logger.error("Cosmos write failed: %s | op=%s | container=%s", e, operation, container_name)
        raise RuntimeError(f"Database write failed: {e.message}")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _build_confirmation_message(operation: str, container_name: str, item: dict) -> str:
    item_id = item.get("id", "(new)")
    op_label = operation.upper()
    return f"This will {op_label} item '{item_id}' in '{container_name}'. Proceed?"


def _log_op(operation: str, container: str, detail: str) -> None:
    """Log operation metadata (non-blocking)."""
    try:
        logger.info(
            "cosmos_tool | op=%s | container=%s | detail=%s",
            operation, container, detail[:100]
        )
    except Exception:
        pass

