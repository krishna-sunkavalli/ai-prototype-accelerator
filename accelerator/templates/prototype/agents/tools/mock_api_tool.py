"""
ai-prototype-accelerator — Mock API Tool
Static scaffold file — do NOT modify when processing spec.yaml.

Calls internal mock API endpoints on localhost.
Validates endpoint against allowed list from MOCK_API_ENDPOINTS env var.
"""
import os
import json
import logging

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "http://localhost:8000/mock-api"


def _get_allowed_endpoints() -> list[str]:
    """Load allowed endpoints from MOCK_API_ENDPOINTS env var (JSON array)."""
    raw = os.environ.get("MOCK_API_ENDPOINTS", "[]")
    try:
        endpoints = json.loads(raw)
        return [str(e) for e in endpoints]
    except (json.JSONDecodeError, TypeError):
        logger.warning("MOCK_API_ENDPOINTS is not valid JSON — no endpoints allowed")
        return []


def call_mock_api(endpoint: str, params: dict = None) -> dict:
    """
    Call an internal mock API endpoint.

    - Validates endpoint is in allowed list (MOCK_API_ENDPOINTS env var).
    - Makes GET request to localhost mock router.
    - Returns response dict.
    - Handles 404, 500 gracefully.
    """
    if params is None:
        params = {}

    allowed = _get_allowed_endpoints()
    if allowed and endpoint not in allowed:
        logger.warning("call_mock_api blocked: endpoint=%s not in allowed list", endpoint)
        return {
            "error": f"Endpoint '{endpoint}' is not in the allowed list.",
            "allowed_endpoints": allowed
        }

    url = f"{_BASE_URL}/{endpoint.lstrip('/')}"

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params)

        _log_call(endpoint, response.status_code)

        if response.status_code == 404:
            return {"error": f"Endpoint '{endpoint}' not found (404)."}
        elif response.status_code >= 500:
            return {"error": f"Mock API server error ({response.status_code})."}

        return response.json()

    except httpx.TimeoutException:
        logger.error("call_mock_api timeout: endpoint=%s", endpoint)
        return {"error": "Mock API request timed out."}
    except httpx.RequestError as e:
        logger.error("call_mock_api request error: %s", e)
        return {"error": f"Mock API unavailable: {str(e)}"}
    except Exception as e:
        logger.error("call_mock_api unexpected error: %s", e)
        return {"error": str(e)}


def _log_call(endpoint: str, status_code: int) -> None:
    try:
        logger.info("mock_api_tool | endpoint=%s | status=%d", endpoint, status_code)
    except Exception:
        pass
