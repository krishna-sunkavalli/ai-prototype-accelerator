"""
ai-prototype-accelerator — Tool-activity notifications
Static scaffold file — do NOT modify when processing spec.yaml.

Lets tools tell the chat UI what they are doing ("Querying outage_events...")
while an agent run is in flight, instead of leaving the user staring at a
typing indicator during multi-second tool round-trips.

Flow:
  - orchestrator._run_agent binds the active (event loop, websocket) pair
    before invoking the agent and unbinds afterwards.
  - MAF executes sync tool callables on worker threads; each tool calls
    notify(label) at entry, which hops the event back onto the bound loop
    with run_coroutine_threadsafe.

Binding uses a ContextVar so concurrent sessions stay isolated when the
executor propagates context (asyncio.to_thread and anyio do). A module-level
fallback covers executors that don't propagate context — with a single
active chat (the normal prototype case) the fallback is always correct.

notify() never raises: a UI status line must never break a tool call.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_context: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "tool_activity_context", default=None
)
_fallback: Optional[tuple] = None


def bind(loop: asyncio.AbstractEventLoop, websocket) -> contextvars.Token:
    """Bind the websocket that should receive activity events.

    Returns a Token for unbind(). Called by the orchestrator around each
    agent run.
    """
    global _fallback
    _fallback = (loop, websocket)
    return _context.set((loop, websocket))


def unbind(token: contextvars.Token) -> None:
    global _fallback
    _fallback = None
    _context.reset(token)


def notify(label: str) -> None:
    """Send a tool_activity event to the bound websocket. No-op when unbound."""
    ctx = _context.get() or _fallback
    if ctx is None:
        return
    loop, websocket = ctx
    payload = json.dumps({"type": "tool_activity", "label": label})

    async def _send() -> None:
        try:
            await websocket.send_text(payload)
        except Exception as exc:
            logger.debug("tool_activity send failed: %s", exc)

    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(_send())
        else:
            asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception as exc:
        logger.debug("tool_activity dispatch failed: %s", exc)
