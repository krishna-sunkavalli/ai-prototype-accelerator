#!/usr/bin/env python3
"""
accelerator/generators/logo_discovery.py — Find a company's logo URL on its website.

The accelerator's zero-touch contract: the user gives a company name and a
website, agents do everything else — including branding. Logo discovery is
deterministic HTML parsing, so it lives in a script rather than agent
improvisation (see RESOLVED.md #18): the business-analyst runs the CLI at
spec-authoring time, and fill-templates calls fetch_and_discover() as a
build-time fallback when the spec has a website but no logo_url.

Candidate priority (why: og:image is usually a 1200x630 social-share banner,
not a clean mark — it is the last resort, not the first):
  1. header/nav <img> whose src/alt/class/id mentions "logo"
  2. any <img> mentioning "logo"
  3. apple-touch-icon (bigger declared size scores higher)
  4. <link rel="icon"> when SVG, or raster with declared size >= 64
  5. og:image

Stdlib only (html.parser + urllib) — no new dependency for the accelerator.

Usage:
    py -3 accelerator/generators/logo_discovery.py https://www.example.com
Prints the best absolute logo URL (exit 0), or nothing (exit 1).
"""
from __future__ import annotations

import sys
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

_HEADER_TAGS = {"header", "nav"}
_MAX_HTML_BYTES = 2 * 1024 * 1024


def _parse_size(sizes: str) -> int:
    """'180x180' → 180; anything unparseable → 0."""
    try:
        return int(sizes.lower().split("x")[0].strip())
    except (ValueError, IndexError, AttributeError):
        return 0


class _LogoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._header_depth = 0
        self.candidates: list[tuple[int, str]] = []  # (score, raw url)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag in _HEADER_TAGS:
            self._header_depth += 1

        elif tag == "img":
            src = a.get("src", "")
            blob = " ".join(
                (src, a.get("alt", ""), a.get("class", ""), a.get("id", ""))
            ).lower()
            if src and "logo" in blob and not src.startswith("data:"):
                score = 100 if self._header_depth else 80
                if src.split("?", 1)[0].lower().endswith(".svg"):
                    score += 10
                self.candidates.append((score, src))

        elif tag == "link":
            rel = a.get("rel", "").lower()
            href = a.get("href", "")
            if not href:
                return
            if "apple-touch-icon" in rel:
                size = _parse_size(a.get("sizes", ""))
                self.candidates.append((60 + min(size // 10, 20), href))
            elif "icon" in rel:
                if href.split("?", 1)[0].lower().endswith(".svg"):
                    self.candidates.append((55, href))
                elif _parse_size(a.get("sizes", "")) >= 64:
                    self.candidates.append((50, href))

        elif tag == "meta":
            prop = (a.get("property", "") or a.get("name", "")).lower()
            if prop == "og:image" and a.get("content"):
                self.candidates.append((20, a["content"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADER_TAGS and self._header_depth:
            self._header_depth -= 1


def discover_logo_url(html: str, base_url: str) -> str:
    """Best logo candidate in the page as an absolute URL, or ''."""
    parser = _LogoParser()
    try:
        parser.feed(html)
    except Exception:
        pass  # malformed HTML — score whatever was collected before the error
    if not parser.candidates:
        return ""
    _, url = max(parser.candidates, key=lambda c: c[0])
    return urljoin(base_url, url)


def fetch_and_discover(website: str, timeout: float = 20.0) -> str:
    """Fetch the site's homepage and return the best logo URL, or ''."""
    try:
        req = urllib.request.Request(
            website,
            headers={"User-Agent": "Mozilla/5.0 (ai-prototype-accelerator logo discovery)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(_MAX_HTML_BYTES).decode("utf-8", errors="replace")
            base = resp.geturl()  # follow redirects for relative resolution
    except Exception as exc:
        print(f"WARN: could not fetch {website} for logo discovery: {exc}", file=sys.stderr)
        return ""
    return discover_logo_url(html, base)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    url = fetch_and_discover(sys.argv[1])
    if url:
        print(url)
        return 0
    print("no logo candidate found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
