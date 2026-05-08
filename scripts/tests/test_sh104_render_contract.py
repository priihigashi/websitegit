"""Render contract tests for SH-104 carousel HTML.

These tests build HTML only. They do not run Playwright or upload to Drive.

Run:
  python3 -m unittest scripts/tests/test_sh104_render_contract.py
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "research"))

import evidence_carousel  # noqa: E402
import manifest_renderer  # noqa: E402


FIXTURE = _REPO / "scripts" / "tests" / "fixtures" / "sample_evidence_manifest.json"


class Sh104RenderContractTests(unittest.TestCase):
    def _load_spec(self):
        manifest = json.loads(FIXTURE.read_text(encoding="utf-8"))
        ok, issues = manifest_renderer.audit_pre_render(manifest)
        self.assertTrue(ok, issues)
        return manifest_renderer.build_carousel_spec(manifest)

    def test_brazil_chosen_html_uses_exporter_contract(self):
        spec = self._load_spec()
        with tempfile.TemporaryDirectory() as td:
            html_path = evidence_carousel.build_carousel_html(spec, td)
            html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn('class="slides-container"', html)
        self.assertIn('id="track"', html)
        self.assertIn('class="slide cover"', html)
        self.assertIn('class="slide biography"', html)
        self.assertIn('class="slide sources"', html)
        self.assertEqual(len(re.findall(r'class="slide evidence"', html)), 3)
        self.assertEqual(len(re.findall(r'class="slide ', html)), spec["slide_count"] + 1)

    def test_biography_slide_is_injected_as_slide_two(self):
        spec = self._load_spec()
        with tempfile.TemporaryDirectory() as td:
            html = Path(evidence_carousel.build_carousel_html(spec, td)).read_text(encoding="utf-8")

        slide_classes = re.findall(r'class="slide ([a-z]+)"', html)
        self.assertGreaterEqual(len(slide_classes), 3)
        self.assertEqual(slide_classes[0], "cover")
        self.assertEqual(slide_classes[1], "biography")
        self.assertEqual(slide_classes[-1], "sources")

    def test_context_warning_chip_renders_for_sensitive_claims(self):
        spec = self._load_spec()
        with tempfile.TemporaryDirectory() as td:
            html = Path(evidence_carousel.build_carousel_html(spec, td)).read_text(encoding="utf-8")

        self.assertIn("Contexto necessário", html)
        self.assertIn("DECLARAÇÃO · CONTRADIÇÃO MORAL", html)
        self.assertIn("Sample Public Figure", html)


if __name__ == "__main__":
    unittest.main()
