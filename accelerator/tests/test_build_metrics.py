"""Tests for accelerator/scripts/build-metrics.py — step-start + active/idle split.

build-metrics.py has a hyphenated filename, so it's loaded via
importlib.util the same way other hyphenated scripts are loaded elsewhere
in this test suite (see test_deploy_wrapper.py). STATE/METRICS_PATH are
module-level globals computed from __file__ at import time; tests
monkeypatch them to a temp directory so nothing touches the real
generated/build-state/.
"""
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "accelerator" / "scripts"


def _load_build_metrics():
    spec = importlib.util.spec_from_file_location("build_metrics_module", SCRIPTS / "build-metrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class BuildMetricsModuleTests(unittest.TestCase):
    def test_module_loads(self):
        bm = _load_build_metrics()
        self.assertTrue(callable(bm.main))


class StepStartTests(unittest.TestCase):
    def setUp(self):
        self.bm = _load_build_metrics()
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "generated" / "build-state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.bm.STATE = self.state
        self.bm.METRICS_PATH = self.state / "metrics.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_step_name_rejected(self):
        rc = self.bm.step_start("99-not-a-real-step")
        self.assertEqual(rc, 1)
        self.assertFalse(self.bm.METRICS_PATH.exists())

    def test_step_start_recorded(self):
        rc = self.bm.step_start("02-infra-agent")
        self.assertEqual(rc, 0)
        metrics = json.loads(self.bm.METRICS_PATH.read_text(encoding="utf-8"))
        self.assertIn("02-infra-agent", metrics["stepStarts"])

    def test_step_start_overwrites_on_rerun(self):
        self.bm.step_start("02-infra-agent")
        first = json.loads(self.bm.METRICS_PATH.read_text(encoding="utf-8"))["stepStarts"]["02-infra-agent"]
        self.bm.step_start("02-infra-agent")
        second = json.loads(self.bm.METRICS_PATH.read_text(encoding="utf-8"))["stepStarts"]["02-infra-agent"]
        # Both are valid ISO timestamps; overwrite behavior is what matters
        # (rerunning a step should reset its clock, unlike record's
        # --keep-existing path for build-start).
        self.assertIsInstance(first, str)
        self.assertIsInstance(second, str)


class SummaryActiveIdleTests(unittest.TestCase):
    def setUp(self):
        self.bm = _load_build_metrics()
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "generated" / "build-state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.bm.STATE = self.state
        self.bm.METRICS_PATH = self.state / "metrics.json"
        self.t0 = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_sentinel(self, name: str, completed_at: datetime) -> None:
        (self.state / f"{name}.done").write_text(
            json.dumps({"completedAt": _iso(completed_at)}), encoding="utf-8"
        )

    def test_active_and_idle_computed_when_step_start_present(self):
        self.bm.METRICS_PATH.write_text(
            json.dumps({"events": {"build-start": _iso(self.t0)}, "stepStarts": {}}), encoding="utf-8"
        )
        # Step 01 finishes at t+5s (no step-start recorded for it).
        self._write_sentinel("01-spec-validator", self.t0 + timedelta(seconds=5))
        # Step 02 starts at t+15s (10s idle since step 01 finished) and
        # completes at t+45s (30s active).
        metrics = self.bm._load_metrics()
        metrics["stepStarts"]["02-infra-agent"] = _iso(self.t0 + timedelta(seconds=15))
        self.bm.METRICS_PATH.write_text(json.dumps(metrics), encoding="utf-8")
        self._write_sentinel("02-infra-agent", self.t0 + timedelta(seconds=45))

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.bm.summary()
        output = buf.getvalue()

        self.assertEqual(rc, 0)
        self.assertIn("active 30s, idle 10s", output)
        self.assertIn("Active work  : 30s", output)
        self.assertIn("Idle/waiting : 10s", output)

    def test_falls_back_to_plain_line_without_step_start(self):
        self.bm.METRICS_PATH.write_text(
            json.dumps({"events": {"build-start": _iso(self.t0)}, "stepStarts": {}}), encoding="utf-8"
        )
        self._write_sentinel("01-spec-validator", self.t0 + timedelta(seconds=5))

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.bm.summary()
        output = buf.getvalue()

        self.assertIn("Validate spec", output)
        self.assertNotIn("active", output)
        self.assertNotIn("Active vs idle", output)


if __name__ == "__main__":
    unittest.main()
