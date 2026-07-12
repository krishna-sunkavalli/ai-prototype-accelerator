"""Tests for incremental rebuild: per-step input fingerprints in sentinels.

A spec edit must invalidate only the steps whose manifest inputs actually
changed. These tests pin the STEP_INPUTS contract: branding-only edits leave
data/agents/docs fresh; table edits invalidate data + agents but not docs.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "sentinels", ROOT / "accelerator" / "generators" / "sentinels.py"
)
sentinels = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sentinels)


def _manifest() -> dict:
    return {
        "specChecksum": "aaaa1111",
        "buildTimestamp": "2026-07-11T00:00:00.000000Z",
        "customer": {"slug": "acme", "name": "Acme Co", "industry": "Test"},
        "deployment": {"environmentName": "acme-prototype", "suffix": "ab12"},
        "resources": {"cosmosAccountName": "acme-cosmos"},
        "branding": {
            "agentName": "AcmeBot",
            "primaryColor": "#000000",
            "accentColor": "#ffffff",
            "useCaseTitle": "Demo",
            "starterQuestions": ["q1"],
        },
        "tables": [{"name": "t1", "partitionKey": "/k", "seedCount": 10}],
        "agents": [{"name": "TestAgent", "model": "gpt-4o"}],
        "modelDeployments": [{"deploymentName": "gpt-4o", "model": "gpt-4o", "capacity": 10}],
        "documents": ["Guide"],
        "documentSpecs": [{"title": "Guide", "description": "", "key_topics": []}],
        "foundryIq": {"indexName": "acme-idx"},
        "mockApiEnabled": False,
        "mockApiEndpoints": [],
        "aiLocation": "eastus2",
    }


class TestStepInputsHash(unittest.TestCase):
    def test_hash_is_stable_for_identical_manifests(self):
        m1, m2 = _manifest(), _manifest()
        for step in sentinels.STEP_INPUTS:
            self.assertEqual(
                sentinels.step_inputs_hash(m1, step),
                sentinels.step_inputs_hash(m2, step),
            )

    def test_volatile_fields_do_not_affect_fingerprints(self):
        m1, m2 = _manifest(), _manifest()
        m2["specChecksum"] = "bbbb2222"
        m2["buildTimestamp"] = "2026-07-12T00:00:00.000000Z"
        for step in sentinels.STEP_INPUTS:
            self.assertEqual(
                sentinels.step_inputs_hash(m1, step),
                sentinels.step_inputs_hash(m2, step),
                f"step {step} fingerprint must ignore volatile fields",
            )

    def test_branding_color_edit_touches_only_hydration_steps(self):
        m1, m2 = _manifest(), copy.deepcopy(_manifest())
        m2["branding"]["primaryColor"] = "#005DAA"
        changed = {
            step
            for step in sentinels.STEP_INPUTS
            if sentinels.step_inputs_hash(m1, step) != sentinels.step_inputs_hash(m2, step)
        }
        # 01 fingerprints the whole manifest; 02 and 06 consume branding.
        # Data (03), agents (04: only branding.agentName), docs (05: only
        # useCaseTitle), and hooks (07) must stay fresh.
        self.assertEqual(changed, {"01", "02", "06"})

    def test_table_edit_invalidates_data_and_agents_not_docs(self):
        m1, m2 = _manifest(), copy.deepcopy(_manifest())
        m2["tables"][0]["seedCount"] = 50
        changed = {
            step
            for step in sentinels.STEP_INPUTS
            if sentinels.step_inputs_hash(m1, step) != sentinels.step_inputs_hash(m2, step)
        }
        self.assertIn("03", changed)
        self.assertIn("04", changed)
        self.assertNotIn("05", changed)
        self.assertNotIn("07", changed)


class TestIsStaleWithFingerprints(unittest.TestCase):
    def test_unrelated_spec_change_keeps_step_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "cosmos_seed.py"
            out.write_text("# seed", encoding="utf-8")
            sentinel = pathlib.Path(tmp) / "03-data-agent.done"

            old = _manifest()
            sentinels.write(sentinel, old["specChecksum"], [out], manifest=old)

            new = copy.deepcopy(_manifest())
            new["specChecksum"] = "bbbb2222"  # spec text changed...
            new["branding"]["primaryColor"] = "#005DAA"  # ...but only branding

            stale, reason = sentinels.is_stale(
                sentinel, new["specChecksum"], [out], manifest=new
            )
            self.assertFalse(stale, f"expected fresh, got stale ({reason})")

    def test_relevant_spec_change_marks_step_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "cosmos_seed.py"
            out.write_text("# seed", encoding="utf-8")
            sentinel = pathlib.Path(tmp) / "03-data-agent.done"

            old = _manifest()
            sentinels.write(sentinel, old["specChecksum"], [out], manifest=old)

            new = copy.deepcopy(_manifest())
            new["specChecksum"] = "bbbb2222"
            new["tables"][0]["seedCount"] = 50

            stale, reason = sentinels.is_stale(
                sentinel, new["specChecksum"], [out], manifest=new
            )
            self.assertTrue(stale)
            self.assertEqual(reason, "inputs-changed")

    def test_sentinel_without_fingerprint_falls_back_to_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "cosmos_seed.py"
            out.write_text("# seed", encoding="utf-8")
            sentinel = pathlib.Path(tmp) / "03-data-agent.done"

            old = _manifest()
            sentinels.write(sentinel, old["specChecksum"], [out])  # no manifest

            new = copy.deepcopy(_manifest())
            new["specChecksum"] = "bbbb2222"
            stale, reason = sentinels.is_stale(
                sentinel, new["specChecksum"], [out], manifest=new
            )
            self.assertTrue(stale)
            self.assertEqual(reason, "spec-changed")

    def test_sentinel_payload_records_inputs_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "config.py"
            out.write_text("# cfg", encoding="utf-8")
            sentinel = pathlib.Path(tmp) / "06-backend-agent.done"
            m = _manifest()
            sentinels.write(sentinel, m["specChecksum"], [out], manifest=m)
            payload = json.loads(sentinel.read_text(encoding="utf-8"))
            self.assertEqual(
                payload.get("inputsHash"), sentinels.step_inputs_hash(m, "06")
            )


if __name__ == "__main__":
    unittest.main()
