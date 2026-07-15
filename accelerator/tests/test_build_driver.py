"""Tests for the headless build driver's files protocol and step contracts.

The driver's LLM steps exchange a strict JSON files payload; these tests pin
extraction robustness, path confinement per step, and the required-output
derivation that both the prompt and the post-write check rely on.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "build_driver", ROOT / "accelerator" / "scripts" / "build.py"
)
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)


def _manifest() -> dict:
    return {
        "specChecksum": "aaaa1111",
        "agents": [
            {"name": "OutageStatusAgent", "skills": ["lookup_outage_by_address", "nearby_outage_summary"]},
            {"name": "SafetyAgent", "skills": []},
        ],
        "documents": ["Outage Safety Guide for Customers", "After the Power Comes Back"],
    }


class TestExtractFilesPayload(unittest.TestCase):
    def test_bare_json(self):
        files = bd._extract_files_payload('{"files": [{"path": "a/b.py", "content": "x"}]}')
        self.assertEqual(files[0]["path"], "a/b.py")

    def test_fenced_json(self):
        text = '```json\n{"files": [{"path": "a.md", "content": "# hi"}]}\n```'
        self.assertEqual(len(bd._extract_files_payload(text)), 1)

    def test_prose_prefix_tolerated(self):
        text = 'Here are the files:\n{"files": [{"path": "a.md", "content": "x"}]}'
        self.assertEqual(len(bd._extract_files_payload(text)), 1)

    def test_missing_files_key_raises(self):
        with self.assertRaises(ValueError):
            bd._extract_files_payload('{"nope": true}')

    def test_entry_without_content_raises(self):
        with self.assertRaises(ValueError):
            bd._extract_files_payload('{"files": [{"path": "a.md"}]}')


class TestValidateFilePaths(unittest.TestCase):
    def test_step03_confined_to_db(self):
        bd._validate_file_paths(
            [{"path": "generated/prototype/db/cosmos_seed.py", "content": "x"}], "03"
        )
        with self.assertRaises(ValueError):
            bd._validate_file_paths(
                [{"path": "generated/prototype/backend/config.py", "content": "x"}], "03"
            )

    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):
            bd._validate_file_paths(
                [{"path": "generated/prototype/db/../../../etc/passwd", "content": "x"}], "03"
            )

    def test_step04_specialists_only(self):
        bd._validate_file_paths(
            [{"path": "generated/prototype/agents/specialists/triage/agent.yaml", "content": "x"}], "04"
        )
        with self.assertRaises(ValueError):
            bd._validate_file_paths(
                [{"path": "generated/prototype/agents/register_agents.py", "content": "x"}], "04"
            )


class TestRequiredOutputs(unittest.TestCase):
    def test_step03(self):
        self.assertEqual(
            bd._required_outputs(_manifest(), "03"),
            ["generated/prototype/db/cosmos_seed.py"],
        )

    def test_step04_covers_triage_agents_and_skills(self):
        outputs = bd._required_outputs(_manifest(), "04")
        self.assertIn("generated/prototype/agents/specialists/triage/agent.yaml", outputs)
        self.assertIn("generated/prototype/agents/specialists/OutageStatusAgent/schemas.py", outputs)
        self.assertIn(
            "generated/prototype/agents/specialists/OutageStatusAgent/skills/nearby_outage_summary/SKILL.md",
            outputs,
        )
        self.assertIn("generated/prototype/agents/specialists/SafetyAgent/agent.yaml", outputs)

    def test_step05_kebab_filenames(self):
        outputs = bd._required_outputs(_manifest(), "05")
        self.assertEqual(outputs, [
            "generated/prototype/agents/knowledge/outage-safety-guide-for-customers.md",
            "generated/prototype/agents/knowledge/after-the-power-comes-back.md",
        ])

    def test_prompt_lists_every_required_file(self):
        manifest = _manifest()
        messages = bd._step_prompt(manifest, "05", "brief text")
        system = messages[0]["content"]
        for path in bd._required_outputs(manifest, "05"):
            self.assertIn(path, system)


if __name__ == "__main__":
    unittest.main()
