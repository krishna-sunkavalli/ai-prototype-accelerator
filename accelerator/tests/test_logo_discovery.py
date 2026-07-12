"""Tests for logo_discovery.py — deterministic brand-mark discovery.

Pins the candidate priority: header logo beats generic imgs beats
apple-touch-icon beats icons beats og:image (which is usually a social
banner, not a mark).
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "logo_discovery", ROOT / "accelerator" / "generators" / "logo_discovery.py"
)
ld = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ld)

BASE = "https://www.example.com/page"


class TestDiscoverLogoUrl(unittest.TestCase):
    def test_header_logo_beats_og_image(self):
        html = """
        <html><head>
          <meta property="og:image" content="https://cdn.example.com/social-banner.jpg">
        </head><body>
          <header><a href="/"><img src="/img/acme-logo.svg" alt="Acme"></a></header>
        </body></html>
        """
        self.assertEqual(
            ld.discover_logo_url(html, BASE), "https://www.example.com/img/acme-logo.svg"
        )

    def test_logo_detected_via_class_and_relative_path_resolved(self):
        html = '<body><nav><img class="site-logo" src="assets/mark.png"></nav></body>'
        self.assertEqual(
            ld.discover_logo_url(html, BASE), "https://www.example.com/assets/mark.png"
        )

    def test_apple_touch_icon_when_no_logo_img(self):
        html = """
        <head>
          <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
          <link rel="icon" sizes="16x16" href="/favicon-16.png">
          <meta property="og:image" content="/banner.jpg">
        </head>
        """
        self.assertEqual(
            ld.discover_logo_url(html, BASE),
            "https://www.example.com/apple-touch-icon.png",
        )

    def test_tiny_favicon_ignored_but_svg_icon_accepted(self):
        html = """
        <head>
          <link rel="icon" href="/favicon.ico">
          <link rel="icon" href="/brand.svg">
        </head>
        """
        self.assertEqual(ld.discover_logo_url(html, BASE), "https://www.example.com/brand.svg")

    def test_og_image_used_as_last_resort(self):
        html = '<head><meta property="og:image" content="https://cdn.example.com/card.png"></head>'
        self.assertEqual(ld.discover_logo_url(html, BASE), "https://cdn.example.com/card.png")

    def test_no_candidates_returns_empty(self):
        self.assertEqual(ld.discover_logo_url("<body><p>hello</p></body>", BASE), "")

    def test_data_uri_imgs_are_skipped(self):
        html = '<header><img class="logo" src="data:image/png;base64,AAAA"></header>'
        self.assertEqual(ld.discover_logo_url(html, BASE), "")

    def test_header_logo_beats_footer_logo(self):
        html = """
        <body>
          <div><img src="/footer-logo-partner.png" alt="partner logo"></div>
          <header><img src="/real-logo.png" alt="Acme logo"></header>
        </body>
        """
        self.assertEqual(
            ld.discover_logo_url(html, BASE), "https://www.example.com/real-logo.png"
        )


if __name__ == "__main__":
    unittest.main()
