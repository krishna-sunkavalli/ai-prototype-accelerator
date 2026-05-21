"""
ai-prototype-accelerator — Mock API Router
Static scaffold file — do NOT modify when processing spec.yaml.

This router exposes one GET endpoint per name listed in the env var
``MOCK_API_ENDPOINTS`` (populated from ``spec.yaml -> mock_api.endpoints``
by ``fill-templates.py``). Each endpoint returns a small, documented
placeholder payload so the ``call_mock_api`` tool always gets a 200 back
and agents can demonstrate the call flow without any per-customer code
landing in the static templates.

If a customer prototype needs real, domain-shaped fixture data, generate
a separate router file under ``backend/api/`` from a specialist and
include it from ``backend/main.py`` — never edit this file with
customer-specific logic.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

mock_router = APIRouter(prefix="/mock-api")


def _allowed_endpoints() -> list[str]:
    """Return the endpoint names declared in spec.yaml -> mock_api.endpoints."""
    raw = os.environ.get("MOCK_API_ENDPOINTS", "[]")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("MOCK_API_ENDPOINTS is not valid JSON — treating as empty")
        return []
    return [str(e).strip("/") for e in parsed if isinstance(e, (str, int))]


@mock_router.get("/_endpoints")
async def list_endpoints() -> dict:
    """Return the list of mock endpoints this prototype was deployed with."""
    return {
        "endpoints": _allowed_endpoints(),
        "note": (
            "Each endpoint is exposed at /mock-api/<name> and returns a "
            "placeholder payload. Replace with real fixtures by generating a "
            "second router per spec."
        ),
    }


@mock_router.get("/{endpoint_name:path}")
async def mock_endpoint(endpoint_name: str, request: Request):
    """Generic handler for any name in MOCK_API_ENDPOINTS."""
    name = endpoint_name.strip("/")
    allowed = _allowed_endpoints()

    if allowed and name not in allowed:
        logger.info("mock_api | endpoint=%s | status=404 (not in allowlist)", name)
        return JSONResponse(
            status_code=404,
            content={
                "error": f"Endpoint '{name}' is not declared in spec.yaml.mock_api.endpoints.",
                "allowed_endpoints": allowed,
            },
        )

    params = dict(request.query_params)
    logger.info("mock_api | endpoint=%s | params=%s | status=200", name, params)
    return {
        "endpoint": name,
        "source": "mock_api_router (placeholder)",
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": params,
        "data": [],
        "note": (
            "This is a placeholder response. Generate a per-spec router under "
            "backend/api/ and include it from backend/main.py to return real "
            "domain-shaped fixture data."
        ),
    }
