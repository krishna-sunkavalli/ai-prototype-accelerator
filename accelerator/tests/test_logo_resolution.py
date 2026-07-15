"""Tests for build-time logo resolution in fill-templates.py.

The spec's branding.logo_url can be empty (generated badge), a data URI,
a manually placed local asset, or a remote URL that must be downloaded at
hydration time with graceful fallback to the badge.
"""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "fill_templates", ROOT / "accelerator" / "generators" / "fill-templates.py"
)
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)

_ARGS = ("AcmeBot", "#005DAA", "#7AB800")


class TestResolveLogoUrl(unittest.TestCase):
    def setUp(self):
        # Isolate the per-build logo cache so tests can't leak resolved
        # results into each other.
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_cache_path = ft.LOGO_CACHE_PATH
        ft.LOGO_CACHE_PATH = pathlib.Path(self._tmp.name) / "logo-cache.json"

    def tearDown(self):
        ft.LOGO_CACHE_PATH = self._orig_cache_path
        self._tmp.cleanup()

    def test_empty_yields_generated_badge(self):
        result = ft._resolve_logo_url("", *_ARGS)
        self.assertTrue(result.startswith("data:image/svg+xml,"))
        self.assertIn("%23005DAA", result)

    def test_badge_initials_split_camelcase(self):
        result = ft._resolve_logo_url("", "ConsumersOutagePilot", "#005DAA", "#7AB800")
        self.assertIn("%3ECO%3C", result)  # >CO< inside the <text> element

    def test_data_uri_passes_through(self):
        uri = "data:image/svg+xml,%3Csvg%3E"
        self.assertEqual(ft._resolve_logo_url(uri, *_ARGS), uri)

    def test_local_asset_path_passes_through(self):
        self.assertEqual(ft._resolve_logo_url("/assets/logo.png", *_ARGS), "/assets/logo.png")

    def test_remote_url_uses_downloaded_local_path(self):
        original = ft._fetch_remote_logo
        ft._fetch_remote_logo = lambda url: "/assets/brand-logo.png"
        try:
            result = ft._resolve_logo_url("https://example.com/logo.png", *_ARGS)
        finally:
            ft._fetch_remote_logo = original
        self.assertEqual(result, "/assets/brand-logo.png")

    def test_remote_failure_falls_back_to_badge(self):
        original = ft._fetch_remote_logo
        ft._fetch_remote_logo = lambda url: None
        try:
            result = ft._resolve_logo_url("https://example.com/logo.png", *_ARGS)
        finally:
            ft._fetch_remote_logo = original
        self.assertTrue(result.startswith("data:image/svg+xml,"))


if __name__ == "__main__":
    unittest.main()
