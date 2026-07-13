"""Tests for the write-confirmation plumbing (activity + confirmations).

Write tools park operations in the confirmations registry and push a card
over the bound websocket; orchestrator.handle_confirmation pops and executes.
These tests pin the registry semantics and the event dispatch without any
Azure dependency.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "accelerator" / "templates" / "prototype"))

from agents.tools import activity, confirmations  # noqa: E402


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class TestConfirmationFlow(unittest.TestCase):
    def setUp(self):
        confirmations.clear()

    def test_request_outside_turn_is_refused(self):
        ok = confirmations.request(
            message="Delete?", operation="delete",
            pending={"container": "t", "item_id": "A1", "partition_key": "k"},
        )
        self.assertFalse(ok)
        self.assertIsNone(confirmations.pop("session-1"))

    def test_request_parks_operation_and_sends_card(self):
        ws = _FakeWebSocket()

        async def run():
            token = activity.bind(
                asyncio.get_running_loop(), ws,
                session_id="session-1", agent_name="OutageStatusAgent",
            )
            try:
                ok = confirmations.request(
                    message="Upsert SP000001?",
                    operation="upsert",
                    pending={"container": "service_points", "item": {"id": "SP000001"}},
                )
                await asyncio.sleep(0)  # flush the dispatched send task
                return ok
            finally:
                activity.unbind(token)

        ok = asyncio.run(run())
        self.assertTrue(ok)

        card = next(m for m in ws.sent if m["type"] == "confirmation")
        self.assertEqual(card["operation"], "upsert")
        self.assertIn("SP000001", card["message"])
        self.assertEqual(card["confirm_label"], "Yes, proceed")

        pending = confirmations.pop("session-1")
        self.assertEqual(pending["operation"], "upsert")
        self.assertEqual(pending["agent"], "OutageStatusAgent")
        self.assertEqual(pending["container"], "service_points")
        self.assertEqual(pending["item"], {"id": "SP000001"})
        # execute_write kwargs shape: everything except operation/agent
        kwargs = {k: v for k, v in pending.items() if k not in ("operation", "agent")}
        self.assertEqual(set(kwargs), {"container", "item"})

    def test_pop_claims_exactly_once(self):
        ws = _FakeWebSocket()

        async def run():
            token = activity.bind(asyncio.get_running_loop(), ws, "s2", "A")
            try:
                confirmations.request("m", "delete", {"container": "t", "item_id": "X", "partition_key": "p"})
                await asyncio.sleep(0)
            finally:
                activity.unbind(token)

        asyncio.run(run())
        self.assertIsNotNone(confirmations.pop("s2"))
        self.assertIsNone(confirmations.pop("s2"))

    def test_notify_sends_tool_activity(self):
        ws = _FakeWebSocket()

        async def run():
            token = activity.bind(asyncio.get_running_loop(), ws, "s3", "A")
            try:
                activity.notify("Querying outage events data...")
                await asyncio.sleep(0)
            finally:
                activity.unbind(token)

        asyncio.run(run())
        event = next(m for m in ws.sent if m["type"] == "tool_activity")
        self.assertIn("outage events", event["label"])


if __name__ == "__main__":
    unittest.main()
