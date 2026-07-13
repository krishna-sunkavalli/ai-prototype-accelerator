"""
ai-prototype-accelerator — Write-operation confirmation registry
Static scaffold file — do NOT modify when processing spec.yaml.

Write tools (upsert/delete in sql_tool.py) never execute directly: they
park the operation here, push a confirmation card to the chat UI over the
activity channel, and return an "awaiting confirmation" result to the model.
When the user answers, routes.py hands the decision to
orchestrator.handle_confirmation, which pops the parked operation and — only
on an explicit yes — executes it via sql_tool.execute_write.

State is per chat session (one pending operation at a time; a newer request
replaces an older unanswered one, which matches how the UI presents cards).
"""
from __future__ import annotations

import logging

from agents.tools import activity

logger = logging.getLogger(__name__)

_PENDING: dict[str, dict] = {}  # session_id → parked operation kwargs


def request(
    message: str,
    operation: str,
    pending: dict,
    confirm_label: str = "Yes, proceed",
    cancel_label: str = "No, cancel",
) -> bool:
    """Park a write operation and push its confirmation card to the UI.

    `pending` holds exactly the kwargs sql_tool.execute_write needs
    (container, item / item_id + partition_key). Returns False when no chat
    turn is bound — the caller should tell the model the write cannot
    proceed rather than silently dropping it.
    """
    ctx = activity.current()
    if ctx is None:
        logger.warning("confirmation requested outside an active turn: %s", operation)
        return False
    session_id, agent_name = ctx[2], ctx[3]

    _PENDING[session_id] = {
        "operation": operation,
        "agent": agent_name,
        **pending,
    }
    sent = activity.send_event({
        "type": "confirmation",
        "message": message,
        "operation": operation,
        "confirm_label": confirm_label,
        "cancel_label": cancel_label,
    })
    if not sent:
        _PENDING.pop(session_id, None)
        return False
    logger.info("confirmation parked | session=%s | op=%s", session_id, operation)
    return True


def pop(session_id: str) -> dict | None:
    """Claim the parked operation for a session (removes it)."""
    return _PENDING.pop(session_id, None)


def clear() -> None:
    _PENDING.clear()
