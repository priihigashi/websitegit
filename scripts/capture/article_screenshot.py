"""
article_screenshot.py — Headless Chromium screenshots of article URLs.

SH-024 + SH-019: Takes a screenshot of an article's main content (headline +
body excerpt), saves PNG to resources/screenshots/ in the work_dir.

Used by:
  capture_pipeline.py  — enriching story research folders (fact-check evidence)
  carousel_builder.py  — evidence screenshots for news carousels

Supports: .gov, .edu, major news domains (globo.com, g1.com.br, cnn.com,
          bbc.com, reuters.com, apnews.com, folha.uol.com.br, etc.)

Returns:
  str  — relative path "resources/screenshots/<filename>.png" on success
  ""   — on failure (not installed, timeout, bad URL, anti-bot block)

Never raises. Always non-fatal.

Install (if not already in workflow):
  pip install playwright
  python -m playwright install chromium --with-deps
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

# ── CSS selector priority for the main article body ──────────────────────────
# Tried in order; first match that is >100px tall wins.
_ARTICLE_SELECTORS = [
    "article",
    "[class*='article-body']", "[class*='article-content']",
    "[class*='story-body']",   "[class*='story-content']",
    "[class*='post-body']",    "[class*='post-content']",
    "[class*='entry-content']","[class*='materia-conteudo']",
    "[class*='noticia-conteudo']","[class*='content-body']",
    "main", "[role='main']",
    "#main-content", "#article", "#conteudo",
    ".post",
]

# Cookie-consent accept selectors (best-effort, non-fatal if not found)
_COOKIE_SELECTORS = [
    "button[id*='accept']", "button[class*='accept']",
    "button[id*='agree']",  "button[class*='agree']",
    "button[id*='cookie']", "[aria-label*='accept']",
    "#onetrust-accept-btn-handler", ".cc-accept",
]

_SCREENSHOT_DIR = "resources/screenshots"


def _safe_domain(url: str) -> str:
    """Extract a filesystem-safe domain slug from a URL."""
    try:
        host = urllib.parse.urlparse(url).netloc or "unknown"
        host = re.sub(r"^www\.", "", host)
        return re.sub(r"[^a-z0-9._-]", "_", host.lower())[:40]
    except Exception:
        return "unknown"


def _make_dest(url: str, work_dir: str) -> Path:
    url_hash = hashlib.sha1(url.encode()).hexdigest()[:8]
    domain = _safe_domain(url)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{domain}_{url_hash}.png"
    dest = Path(work_dir) / _SCREENSHOT_DIR / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _write_sidecar(dest: Path, url: str) -> None:
    sidecar = dest.with_suffix(".source.txt")
    try:
        sidecar.write_text(
            f"url: {url}\n"
            f"tool: playwright/chromium\n"
            f"fetched_at: {datetime.utcnow().isoformat()}Z\n"
            f"license: editorial fair use (screenshot for research/fact-check)\n"
        )
    except Exception:
        pass


def screenshot_article(
    url: str,
    work_dir: str,
    timeout_ms: int = 15000,
    viewport_w: int = 1280,
    viewport_h: int = 900,
    full_page: bool = False,
) -> str:
    """Take a focused screenshot of an article's main content.

    Args:
        url:         Article URL to screenshot.
        work_dir:    Local working directory (dest goes to work_dir/resources/screenshots/).
        timeout_ms:  Navigation + networkidle timeout in ms.
        full_page:   If True, screenshot full page (default: crop to article element).

    Returns:
        Relative path "resources/screenshots/<file>.png" on success, "" on failure.
    """
    if not url or not url.startswith("http"):
        return ""

    # Require playwright — graceful skip if not installed
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("  [article_screenshot] playwright not installed — skipping (pip install playwright)")
        return ""

    dest = _make_dest(url, work_dir)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": viewport_w, "height": viewport_h},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US,pt-BR",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8"},
            )
            page = ctx.new_page()

            # Navigate
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except PWTimeout:
                # Fallback: domcontentloaded is more reliable on heavy pages
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    time.sleep(2)  # let late JS settle
                except Exception:
                    pass

            # Accept cookie banners (best-effort)
            for sel in _COOKIE_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=800):
                        btn.click(timeout=800)
                        time.sleep(0.4)
                        break
                except Exception:
                    continue

            if full_page:
                page.screenshot(path=str(dest), full_page=True)
                print(f"  [article_screenshot] full-page → {dest.name} ({dest.stat().st_size//1024}KB)")
            else:
                # Try to find main content element
                article_el = None
                for sel in _ARTICLE_SELECTORS:
                    try:
                        el = page.locator(sel).first
                        bbox = el.bounding_box(timeout=1500)
                        if bbox and bbox.get("height", 0) > 120:
                            article_el = el
                            break
                    except Exception:
                        continue

                if article_el:
                    try:
                        article_el.screenshot(path=str(dest))
                        print(f"  [article_screenshot] article-element → {dest.name} "
                              f"({dest.stat().st_size//1024}KB)")
                    except Exception:
                        # Element screenshot failed — fall back to viewport crop
                        page.screenshot(path=str(dest), clip={
                            "x": 0, "y": 0, "width": viewport_w, "height": viewport_h
                        })
                        print(f"  [article_screenshot] viewport-crop → {dest.name} "
                              f"({dest.stat().st_size//1024}KB)")
                else:
                    # No article element found — screenshot visible viewport
                    page.screenshot(path=str(dest), clip={
                        "x": 0, "y": 0, "width": viewport_w, "height": viewport_h
                    })
                    print(f"  [article_screenshot] viewport → {dest.name} "
                          f"({dest.stat().st_size//1024}KB)")

            browser.close()

        if not dest.exists() or dest.stat().st_size < 5000:
            print(f"  [article_screenshot] screenshot too small or missing — {url[:60]}")
            return ""

        _write_sidecar(dest, url)
        return f"{_SCREENSHOT_DIR}/{dest.name}"

    except Exception as exc:
        print(f"  [article_screenshot] failed for {url[:60]}: {exc}")
        return ""


def screenshot_urls(
    urls: list[str],
    work_dir: str,
    max_screenshots: int = 5,
    **kwargs,
) -> list[str]:
    """Screenshot a list of URLs. Returns list of relative paths (empty strings filtered out).

    Args:
        urls:            List of URLs to screenshot.
        work_dir:        Local working directory.
        max_screenshots: Cap to avoid runaway usage.
        **kwargs:        Passed to screenshot_article().
    """
    results = []
    for url in urls[:max_screenshots]:
        path = screenshot_article(url, work_dir, **kwargs)
        if path:
            results.append(path)
        time.sleep(1)  # polite delay between requests
    return results


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 3:
        print("Usage: python article_screenshot.py <url> <work_dir>")
        sys.exit(1)
    url_arg = sys.argv[1]
    work_dir_arg = sys.argv[2]
    result = screenshot_article(url_arg, work_dir_arg)
    if result:
        print(f"\nSaved: {result}")
        print(json.dumps({"path": result, "url": url_arg}, indent=2))
    else:
        print("\nFailed — no screenshot produced")
        sys.exit(1)
