"""Tests for tool_definitions.yaml — the data-driven tool catalogue."""
from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL_DEFS = (
    ROOT
    / "accelerator"
    / "templates"
    / "prototype"
    / "agents"
    / "tools"
    / "tool_definitions.yaml"
)


class TestToolDefinitions(unittest.TestCase):
    def setUp(self):
        with open(TOOL_DEFS, "r", encoding="utf-8") as f:
            self.defs = yaml.safe_load(f) or []

    def test_is_a_list(self):
        self.assertIsInstance(self.defs, list)
        self.assertGreater(len(self.defs), 0)

    def test_every_entry_has_required_keys(self):
        required_keys = {"name", "description", "parameters"}
        for entry in self.defs:
            self.assertIsInstance(entry, dict, entry)
            missing = required_keys - set(entry.keys())
            self.assertFalse(missing, f"{entry.get('name')!r} missing {missing}")

    def test_parameters_is_json_schema_object(self):
        for entry in self.defs:
            params = entry["parameters"]
            self.assertIsInstance(params, dict, entry["name"])
            self.assertEqual(
                params.get("type"),
                "object",
                f"{entry['name']} parameters.type must be 'object'",
            )

    def test_names_unique(self):
        names = [d["name"] for d in self.defs]
        self.assertEqual(len(names), len(set(names)), f"duplicate tool names: {names}")


if __name__ == "__main__":
    unittest.main()
