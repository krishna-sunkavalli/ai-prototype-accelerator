"""Contract tests for accelerator/generators/preflight_env.py and the auth
check functions in accelerator/generators/preflight.py.

The tests focus on the parts that are safe to run offline: the module loads,
main() is callable, and the auth-related helpers do not crash when the
manifest is missing / minimal. Live-Azure behaviour (real `az account show`
responses) is covered by the smoke run we do at build time; unit tests here
stub `shutil.which` to control whether az / azd appear installed.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
GENERATORS = ROOT / "accelerator" / "generators"


def _load(module_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, GENERATORS / file_name
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreflightEnvModuleTests(unittest.TestCase):
    """Sanity: preflight_env.py imports and exposes main()."""

    def test_module_loads(self):
        env = _load("preflight_env_test", "preflight_env.py")
        self.assertTrue(callable(env.main))

    def test_loads_preflight_lib(self):
        env = _load("preflight_env_test", "preflight_env.py")
        lib = env._load_preflight_module()
        # A representative early check and a representative late check both
        # live on the shared library.
        self.assertTrue(callable(lib.check_azd_installed))
        self.assertTrue(callable(lib.check_az_logged_in))
        self.assertTrue(callable(lib.check_az_subscription_matches_manifest))
        self.assertTrue(callable(lib.check_provider_registered))
        self.assertTrue(callable(lib.check_model_quota))
        self.assertTrue(callable(lib.check_search_sku_capacity))
        self.assertTrue(callable(lib.check_cosmos_regional_capacity))
        self.assertTrue(callable(lib.check_global_name_collisions))
        self.assertTrue(callable(lib.check_bicep_builds))
        self.assertTrue(callable(lib.check_python_compiles))


class AuthCheckTests(unittest.TestCase):
    """The three new authentication helpers in preflight.py."""

    def setUp(self):
        self.preflight = _load("preflight_test", "preflight.py")

    def test_azd_missing_hard_fails(self):
        with mock.patch("shutil.which", return_value=None):
            errors: list[str] = []
            self.preflight.check_azd_installed(errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("azd", errors[0].lower())

    def test_azd_present_no_error(self):
        # shutil.which is called for both 'azd' and 'azd.exe'; return truthy
        # so the check treats azd as installed.
        with mock.patch("shutil.which", return_value="/usr/bin/azd"):
            errors: list[str] = []
            self.preflight.check_azd_installed(errors)
        self.assertEqual(errors, [])

    def test_az_missing_hard_fails(self):
        with mock.patch("shutil.which", return_value=None):
            errors: list[str] = []
            self.preflight.check_az_logged_in(errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("azure cli", errors[0].lower())

    def test_subscription_check_soft_skips_without_manifest(self):
        # When MANIFEST does not exist the check returns silently.
        with tempfile.TemporaryDirectory() as tmp:
            fake_manifest = pathlib.Path(tmp) / "manifest.json"  # not created
            with mock.patch.object(self.preflight, "MANIFEST", fake_manifest):
                errors: list[str] = []
                self.preflight.check_az_subscription_matches_manifest(errors)
        self.assertEqual(errors, [])

    def test_subscription_check_soft_skips_without_subscription_id(self):
        # Manifest present but no subscriptionId field → nothing to compare.
        with tempfile.TemporaryDirectory() as tmp:
            fake = pathlib.Path(tmp) / "manifest.json"
            fake.write_text(json.dumps({"deployment": {"location": "eastus2"}}))
            with mock.patch.object(self.preflight, "MANIFEST", fake):
                errors: list[str] = []
                self.preflight.check_az_subscription_matches_manifest(errors)
        self.assertEqual(errors, [])


class GlobalNameCollisionTests(unittest.TestCase):
    """check_global_name_collisions flags cross-RG global name conflicts."""

    def setUp(self):
        self.preflight = _load("preflight_glob_test", "preflight.py")
        # Redirect the preflight cache to a per-test tempfile so cache-hit
        # short-circuits from earlier tests (or earlier real preflight runs)
        # don't mask the check under test.
        self._cache_tmp = tempfile.TemporaryDirectory()
        self._orig_cache = self.preflight.PREFLIGHT_CACHE
        self.preflight.PREFLIGHT_CACHE = (
            pathlib.Path(self._cache_tmp.name) / "preflight-cache.json"
        )

    def tearDown(self):
        self.preflight.PREFLIGHT_CACHE = self._orig_cache
        self._cache_tmp.cleanup()

    def _write_manifest(self, tmp: str) -> pathlib.Path:
        manifest_path = pathlib.Path(tmp) / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "deployment": {
                        "resourceGroup": "rg-target",
                        "customerShort": "hudson",
                        "demoTheme": "hudson",
                        "location": "eastus2",
                    },
                    "resources": {
                        "storageAccount": "hudsonhudsonst",
                        "acrName": "hudsonhudsonacr",
                        "cosmosAccountName": "hudson-hudson-cosmos",
                    },
                }
            )
        )
        return manifest_path

    def test_soft_skip_when_az_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._write_manifest(tmp)
            with mock.patch.object(self.preflight, "MANIFEST", manifest), \
                 mock.patch("shutil.which", return_value=None):
                errors: list[str] = []
                self.preflight.check_global_name_collisions(errors)
        self.assertEqual(errors, [])

    def test_reports_conflict_in_other_rg(self):
        # Simulate a Resource Graph hit that returns the storage account in
        # a different RG than the manifest's target.
        graph_stdout = json.dumps(
            [
                {
                    "name": "hudsonhudsonst",
                    "type": "microsoft.storage/storageaccounts",
                    "resourceGroup": "rg-stranded",
                    "subscriptionId": "00000000-0000-0000-0000-000000000000",
                    "location": "southcentralus",
                }
            ]
        )

        # The check also calls the Foundry checkDomainAvailability endpoint;
        # return a "name available" response so it doesn't add a second error.
        domain_stdout = json.dumps({"nameAvailable": True})

        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            # First call is graph query, second is cognitiveservices check.
            cmd = args[0]
            if "graph" in cmd:
                return subprocess.CompletedProcess(cmd, 0, graph_stdout, "")
            if "cognitiveservices" in cmd:
                return subprocess.CompletedProcess(cmd, 0, domain_stdout, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._write_manifest(tmp)
            with mock.patch.object(self.preflight, "MANIFEST", manifest), \
                 mock.patch("shutil.which", return_value="/usr/bin/az"), \
                 mock.patch.object(self.preflight.subprocess, "run", side_effect=fake_run):
                errors: list[str] = []
                self.preflight.check_global_name_collisions(errors)

        self.assertEqual(len(errors), 1)
        self.assertIn("hudsonhudsonst", errors[0])
        self.assertIn("rg-stranded", errors[0])

    def test_same_rg_hit_is_not_a_conflict(self):
        # A hit whose resourceGroup matches the target RG is our own
        # already-provisioned resource — must not raise.
        graph_stdout = json.dumps(
            [
                {
                    "name": "hudsonhudsonst",
                    "type": "microsoft.storage/storageaccounts",
                    "resourceGroup": "rg-target",
                    "subscriptionId": "00000000-0000-0000-0000-000000000000",
                    "location": "eastus2",
                }
            ]
        )
        domain_stdout = json.dumps({"nameAvailable": True})

        def fake_run(*args, **kwargs):
            cmd = args[0]
            if "graph" in cmd:
                return subprocess.CompletedProcess(cmd, 0, graph_stdout, "")
            if "cognitiveservices" in cmd:
                return subprocess.CompletedProcess(cmd, 0, domain_stdout, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as tmp:
            manifest = self._write_manifest(tmp)
            with mock.patch.object(self.preflight, "MANIFEST", manifest), \
                 mock.patch("shutil.which", return_value="/usr/bin/az"), \
                 mock.patch.object(self.preflight.subprocess, "run", side_effect=fake_run):
                errors: list[str] = []
                self.preflight.check_global_name_collisions(errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
