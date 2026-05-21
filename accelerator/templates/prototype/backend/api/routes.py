"""
ai-prototype-accelerator — API Routes
Static scaffold file — do NOT modify when processing spec.yaml.
"""
import os
import json
import logging
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse

from agents import orchestrator
from agents.tools.sql_tool import (
    check_cosmos_health,
    get_cosmos_client,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_TURNS = 20


# ── GET /health ────────────────────────────────────────────────────────────────

@router.get("/health")
async def health(request: Request):
    issues = []

    # Check project client / agent availability
    try:
        agents = orchestrator.get_registered_agent_names()
        if not agents:
            issues.append("no agents registered")
    except Exception as e:
        issues.append(f"orchestrator: {str(e)}")
        agents = []

    # Check Cosmos DB — report as warning only; org policy may enforce publicNetworkAccess=Disabled
    cosmos_warning = None
    try:
        check_cosmos_health()
    except Exception as e:
        cosmos_warning = f"cosmos: {str(e)}"

    # Check AI Search — use admin key (Bearer token is unreliable even with correct RBAC)
    try:
        import httpx
        endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
        search_api_key = os.environ.get("AZURE_SEARCH_ADMIN_KEY", "")
        if endpoint and search_api_key:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    f"{endpoint}/indexes?api-version=2023-11-01",
                    headers={"api-key": search_api_key},
                    timeout=5.0
                )
                if resp.status_code not in (200, 204):
                    issues.append(f"ai_search: HTTP {resp.status_code}")
        # If no key available, skip — don't report as issue
    except Exception as e:
        issues.append(f"ai_search: {str(e)}")

    if issues:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "version": "1.0.0",
                "agents": agents,
                "issues": issues,
                **({"warnings": [cosmos_warning]} if cosmos_warning else {})
            }
        )

    warnings = [cosmos_warning] if cosmos_warning else []
    return JSONResponse(
        status_code=200,
        content={
            "status": "degraded" if warnings else "healthy",
            "version": "1.0.0",
            "agents": agents,
            **({"warnings": warnings} if warnings else {})
        }
    )


# ── WS /chat ───────────────────────────────────────────────────────────────────

@router.websocket("/chat")
async def chat(websocket: WebSocket):
    await websocket.accept()
    thread_id = None
    turn_count = 0

    try:
        # Assign a session UUID per connection.
        # The orchestrator maps this UUID to a Foundry thread internally.
        thread_id = str(uuid.uuid4())
        logger.info("WebSocket connected, session_id=%s", thread_id)

        while True:
            raw = await websocket.receive_text()

            # Parse incoming message — may be text or JSON control message
            try:
                incoming = json.loads(raw)
                msg_type = incoming.get("type", "text")
            except (json.JSONDecodeError, AttributeError):
                incoming = {"type": "text", "content": raw}
                msg_type = "text"

            if msg_type == "confirm":
                # User responded to a write confirmation
                await orchestrator.handle_confirmation(
                    incoming, thread_id, websocket
                )
                continue

            # Regular user message
            user_message = incoming.get("content", raw) if isinstance(incoming, dict) else raw
            turn_count += 1

            # Reset thread after MAX_TURNS to avoid token overflow
            if turn_count > MAX_TURNS:
                # Create a new session UUID — orchestrator will open a fresh Foundry thread
                thread_id = str(uuid.uuid4())
                turn_count = 1
                await websocket.send_text(json.dumps({
                    "type": "text",
                    "content": "\u26a0\ufe0f Session refreshed after 20 messages to maintain performance. Starting a new conversation.",
                    "done": True
                }))

            # Route through orchestrator
            await orchestrator.route(
                user_message=user_message,
                thread_id=thread_id,
                websocket=websocket
            )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected, session_id=%s", thread_id)
        # Foundry thread cleanup is handled by orchestrator.close_all_connections()
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "content": "An unexpected error occurred. Please try again."
            }))
        except Exception:
            pass


# ── GET /config ────────────────────────────────────────────────────────────────

def _parse_starter_questions(raw: str) -> list:
    """Parse STARTER_QUESTIONS env var — handles both valid JSON arrays and
    bare comma-separated strings (which az containerapp update can produce)."""
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        # Strip surrounding brackets if present, then split on commas
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        return [q.strip().strip('"\'') for q in raw.split(",") if q.strip()]


@router.get("/config")
async def config():
    return {
        "CUSTOMER_NAME": os.environ.get("CUSTOMER_NAME", ""),
        "AGENT_NAME": os.environ.get("AGENT_NAME", "AI Assistant"),
        "PRIMARY_COLOR": os.environ.get("PRIMARY_COLOR", "#0078D4"),
        "ACCENT_COLOR": os.environ.get("ACCENT_COLOR", "#50E6FF"),
        "FONT_FAMILY": os.environ.get("FONT_FAMILY", "Arial, sans-serif"),
        "LOGO_URL": os.environ.get("LOGO_URL", ""),
        "WELCOME_MESSAGE": os.environ.get(
            "WELCOME_MESSAGE",
            "Hello! How can I help you today?"
        ),
        "USE_CASE_TITLE": os.environ.get("USE_CASE_TITLE", ""),
        "PROTOTYPE_RESET": os.environ.get("PROTOTYPE_RESET", "false").lower(),
        "MOCK_API_ENABLED": os.environ.get("MOCK_API_ENABLED", "false").lower(),
        "STARTER_QUESTIONS": _parse_starter_questions(os.environ.get("STARTER_QUESTIONS", "[]")),
        "PERSONA_NAME": os.environ.get("PERSONA_NAME", "User"),
        "PERSONA_ROLE": os.environ.get("PERSONA_ROLE", "Viewer"),
    }


# ── POST /admin/reset ──────────────────────────────────────────────────────────
# Wipes every container in the prototype's Cosmos DB and re-runs the seed
# script so demo state is restored to the post-provision baseline.
#
# The endpoint is gated by PROTOTYPE_RESET=true and is intended for demo /
# prototype use only — never enable it in a production deployment.

@router.post("/admin/reset")
async def admin_reset(request: Request):
    if os.environ.get("PROTOTYPE_RESET", "false").lower() != "true":
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    try:
        client = get_cosmos_client()
        db = client.get_database_client(os.environ["AZURE_COSMOS_DATABASE"])

        # 1. Drop every container — cosmos_seed re-creates them via
        #    create_container_if_not_exists with the correct partition key.
        containers = [c["id"] for c in db.list_containers()]
        for name in containers:
            db.delete_container(name)
        containers_dropped = len(containers)

        # 2. Re-run the seed script. Lives at /app/db/cosmos_seed.py inside the
        #    container image (see Dockerfile). Run as a subprocess so its
        #    side-effects (container creation, item upserts) stay isolated.
        import subprocess
        import sys

        seed_path = os.path.join(os.path.dirname(__file__), "..", "..", "db", "cosmos_seed.py")
        seed_path = os.path.abspath(seed_path)
        seed_cwd = os.path.dirname(seed_path)

        result = subprocess.run(
            [sys.executable, seed_path],
            cwd=seed_cwd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            logger.error(
                "admin_reset seed failed | rc=%s | stderr=%s",
                result.returncode, result.stderr[:1000],
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": "Seed script failed; see server logs.",
                },
            )

        logger.info(
            "admin_reset completed at %s | containers_dropped=%d",
            datetime.now(timezone.utc).isoformat(), containers_dropped,
        )

        return {
            "status": "reset",
            "containers_dropped": containers_dropped,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.exception("admin_reset failed: %s", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e)},
        )
