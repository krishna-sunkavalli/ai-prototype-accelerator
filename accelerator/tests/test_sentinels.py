"""Tests for accelerator/generators/sentinels.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "accelerator" / "generators"))

import sentinels  # noqa: E402


class TestSentinels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.sentinel = self.dir / "test.done"
        self.output = self.dir / "out.txt"
        self.output.write_text("hello", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_sentinel_is_stale(self):
        stale, reason = sentinels.is_stale(self.sentinel, "abc", [self.output])
        self.assertTrue(stale)
        self.assertEqual(reason, "missing")

    def test_fresh_after_write(self):
        sentinels.write(self.sentinel, "abc", [self.output])
        stale, reason = sentinels.is_stale(self.sentinel, "abc", [self.output])
        self.assertFalse(stale, reason)
        self.assertEqual(reason, "fresh")

    def test_spec_change_invalidates(self):
        sentinels.write(self.sentinel, "abc", [self.output])
        stale, reason = sentinels.is_stale(self.sentinel, "xyz", [self.output])
        self.assertTrue(stale)
        self.assertEqual(reason, "spec-changed")

    def test_output_mutation_invalidates(self):
        sentinels.write(self.sentinel, "abc", [self.output])
        self.output.write_text("modified", encoding="utf-8")
        stale, reason = sentinels.is_stale(self.sentinel, "abc", [self.output])
        self.assertTrue(stale)
        self.assertEqual(reason, "outputs-modified")

    def test_legacy_timestamp_is_stale(self):
        self.sentinel.write_text("2026-05-18T00:00:00Z", encoding="utf-8")
        stale, reason = sentinels.is_stale(self.sentinel, "abc", [self.output])
        self.assertTrue(stale)
        self.assertEqual(reason, "legacy-format")


if __name__ == "__main__":
    unittest.main()
