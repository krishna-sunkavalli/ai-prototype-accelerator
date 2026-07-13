"""
ai-prototype-accelerator — Tool-side event channel to the chat UI
Static scaffold file — do NOT modify when processing spec.yaml.

Lets tools running inside an agent turn talk to the active websocket:
  - notify(label): "Querying outage_events..." status lines while tools run
  - send_event(data): any structured event (used by confirmations.py to
    deliver write-confirmation cards)
  - current(): the bound (loop, websocket, session_id, agent_name) so other
    modules can key state to the active turn

Flow: orchestrator._run_agent binds the context before invoking the agent
and unbinds afterwards. MAF executes sync tool callables on worker threads,
so dispatch hops back onto the bound loop with run_coroutine_threadsafe.

Binding uses a ContextVar so concurrent sessions stay isolated when the
executor propagates context (asyncio.to_thread and anyio do). A module-level
fallback covers executors that don't propagate context — with a single
active chat (the normal prototype case) the fallback is always correct.

Dispatch never raises: a UI event must never break a tool call.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# (loop, websocket, session_id, agent_name)
_context: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "tool_activity_context", default=None
)
_fallback: Optional[tuple] = None


def bind(
    loop: asyncio.AbstractEventLoop,
    websocket,
    session_id: str = "",
    agent_name: str = "",
) -> contextvars.Token:
    """Bind the websocket + turn identity that tool events should target.

    Returns a Token for unbind(). Called by the orchestrator around each
    agent run.
    """
    global _fallback
    ctx = (loop, websocket, session_id, agent_name)
    _fallback = ctx
    return _context.set(ctx)


def unbind(token: contextvars.Token) -> None:
    global _fallback
    _fallback = None
    _context.reset(token)


def current() -> Optional[tuple]:
    """The active (loop, websocket, session_id, agent_name), or None."""
    return _context.get() or _fallback


def send_event(data: dict) -> bool:
    """Send a structured event to the bound websocket. False when unbound."""
    ctx = current()
    if ctx is None:
        return False
    loop, websocket = ctx[0], ctx[1]
    payload = json.dumps(data)

    async def _send() -> None:
        try:
            await websocket.send_text(payload)
        except Exception as exc:
            logger.debug("tool event send failed: %s", exc)

    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(_send())
        else:
            asyncio.run_coroutine_threadsafe(_send(), loop)
        return True
    except Exception as exc:
        logger.debug("tool event dispatch failed: %s", exc)
        return False


def notify(label: str) -> None:
    """Send a tool_activity status line. No-op when unbound."""
    send_event({"type": "tool_activity", "label": label})
