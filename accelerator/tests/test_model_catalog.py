"""Tests for accelerator/generators/model_catalog.py"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "accelerator" / "generators"))

import model_catalog  # noqa: E402


class TestModelCatalog(unittest.TestCase):
    def test_known_model_uses_default_version(self):
        self.assertEqual(
            model_catalog.resolve_version("gpt-4o"),
            model_catalog.MODEL_VERSION_DEFAULTS["gpt-4o"],
        )

    def test_explicit_version_overrides_default(self):
        self.assertEqual(
            model_catalog.resolve_version("gpt-4o", "2099-01-01"),
            "2099-01-01",
        )

    def test_unknown_model_falls_back_to_latest(self):
        self.assertEqual(model_catalog.resolve_version("not-a-model"), "latest")

    def test_default_sku(self):
        self.assertEqual(model_catalog.resolve_sku(), "GlobalStandard")
        self.assertEqual(model_catalog.resolve_sku("Standard"), "Standard")


if __name__ == "__main__":
    unittest.main()
