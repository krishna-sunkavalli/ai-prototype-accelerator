"""Contract tests for accelerator/scripts/deploy.py.

deploy.py orchestrates `azd env`/`azd up` and applies auto-recovery for
AI Search Basic-SKU regional exhaustion. The tests here cover the pure
functions (fallback picker, error-pattern detector) without invoking az
or azd — the wrapper's actual azd interactions are covered by end-to-
end runs, not unit tests.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "accelerator" / "scripts"


def _load_deploy():
    # deploy.py has a hyphen-free filename so import.util is used the same
    # way we already load other scripts in the test suite.
    spec = importlib.util.spec_from_file_location("deploy_module", SCRIPTS / "deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeployWrapperModuleTests(unittest.TestCase):
    def test_module_loads(self):
        deploy = _load_deploy()
        self.assertTrue(callable(deploy.main))


class SearchCapacityDetectorTests(unittest.TestCase):
    def setUp(self):
        self.deploy = _load_deploy()

    def test_search_capacity_error_recognized(self):
        stderr = (
            "ERROR: A resource with this name already exists or is in a "
            "conflicting state.\n"
            "Deployment Error Details:\n"
            "InsufficientResourcesAvailable: The region 'eastus2' is "
            "currently out of the resources required to provision "
            "Microsoft.Search/searchServices. Try creating the service in "
            "another region."
        ).lower()
        self.assertTrue(self.deploy._looks_like_search_capacity_error(stderr))

    def test_unrelated_insufficient_resources_not_matched(self):
        stderr = (
            "InsufficientResourcesAvailable: Cosmos DB in southcentralus is "
            "at capacity. Try another region."
        ).lower()
        self.assertFalse(self.deploy._looks_like_search_capacity_error(stderr))

    def test_empty_stderr_not_matched(self):
        self.assertFalse(self.deploy._looks_like_search_capacity_error(""))


class SearchFallbackPickerTests(unittest.TestCase):
    def setUp(self):
        self.deploy = _load_deploy()

    def test_returns_first_untried_neighbor(self):
        fallback = self.deploy._pick_search_fallback("eastus2", set())
        self.assertEqual(fallback, "eastus")

    def test_skips_regions_already_tried(self):
        fallback = self.deploy._pick_search_fallback("eastus2", {"eastus2", "eastus"})
        # Next in the eastus2 fallback list after eastus is centralus.
        self.assertEqual(fallback, "centralus")

    def test_returns_default_for_unknown_region(self):
        fallback = self.deploy._pick_search_fallback("marsprime01", set())
        self.assertEqual(fallback, self.deploy._SEARCH_DEFAULT_FALLBACK)

    def test_returns_none_when_all_options_exhausted(self):
        current = "eastus2"
        tried = set(self.deploy._SEARCH_FALLBACK["eastus2"] + [
            "eastus2", self.deploy._SEARCH_DEFAULT_FALLBACK,
        ])
        self.assertIsNone(self.deploy._pick_search_fallback(current, tried))


if __name__ == "__main__":
    unittest.main()
