"""D (2026-05-18) — build_opc_from_slide_plan() must accept 4-8 slide bundle plans.

Verifies:
  - Legacy plan (no bundle_id) with len != 5 falls back to _build_opc_html.
  - Bundle plan with len in [4, 5, 6, 7, 8] renders without falling back.
  - Bundle plan with len 3 or 9 falls back (out of range).
  - Tip components in bundle mode read their OWN slot, not hardcoded 2/3/4.

These tests stub render functions to keep the test fast and pure-Python — no
HTML/PNG/MP4 actually written. The goal is to assert plan acceptance behavior
and slot routing, not byte-for-byte HTML.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

import carousel_builder  # noqa: E402


def _make_plan(n_slides: int, bundle_id: str | None = None):
    """Build a minimal valid plan with n_slides entries."""
    # All-tip middle for simplicity (one cover, n-2 tip_list, one sources).
    slides = [{"slide": 1, "role": "cover", "template_id": "opc_tip_cover"}]
    for i in range(2, n_slides):
        slides.append({"slide": i, "role": "definition", "template_id": "opc_tip_list"})
    slides.append({"slide": n_slides, "role": "sources", "template_id": "opc_tip_sources"})
    plan = {"status": "passed", "slides": slides}
    if bundle_id:
        plan["bundle_id"] = bundle_id
    return plan


def _make_content(n_slides: int, bundle_id: str | None = None):
    return {
        "headline": "THE TEST GUIDE",
        "subhead": "Test sub.",
        "accent_word": "GUIDE",
        "slide3_items": [{"title": "A", "sub": "B"}],
        "sources": ["src1.com"],
        "slide4_headline": "THE PRO MOVE",
        "cta": "SAVE.",
        "slides": [],
        "_slide_plan": _make_plan(n_slides, bundle_id),
    }


class BuildOpcVariableSlideCountTest(unittest.TestCase):
    """build_opc_from_slide_plan acceptance + dispatch under variable slide counts."""

    def setUp(self):
        # Track whether _build_opc_html (legacy fallback) was called.
        self._legacy_called = False
        self._original_build_opc_html = carousel_builder._build_opc_html

        def _spy_build_opc_html(content, slug, work_dir, media_paths=None):
            self._legacy_called = True
            return str(Path(work_dir) / "cover.html")

        self._patcher = mock.patch.object(
            carousel_builder, "_build_opc_html", side_effect=_spy_build_opc_html
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _run(self, n_slides, bundle_id, tmp_path):
        content = _make_content(n_slides, bundle_id)
        return carousel_builder.build_opc_from_slide_plan(
            content, slug="test-slug", work_dir=str(tmp_path), media_paths={},
        )

    def test_legacy_5_slide_plan_does_NOT_fall_back(self):
        with mock.patch.object(carousel_builder, "_build_opc_html") as legacy:
            content = _make_content(5, bundle_id=None)
            tmp = Path("/tmp/test_opc_d_legacy")
            tmp.mkdir(exist_ok=True)
            carousel_builder.build_opc_from_slide_plan(content, "s", str(tmp), media_paths={})
            self.assertFalse(legacy.called, "5-slide legacy plan should render, not fall back")

    def test_legacy_4_slide_plan_FALLS_BACK(self):
        # Without bundle_id, anything != 5 must fall back.
        tmp = Path("/tmp/test_opc_d_legacy4")
        tmp.mkdir(exist_ok=True)
        self._run(4, None, tmp)
        self.assertTrue(self._legacy_called, "4-slide non-bundle plan must fall back")

    def test_bundle_4_slide_plan_does_NOT_fall_back(self):
        tmp = Path("/tmp/test_opc_d_b4")
        tmp.mkdir(exist_ok=True)
        with mock.patch.object(carousel_builder, "_build_opc_html") as legacy:
            content = _make_content(4, bundle_id="cream_base_v1")
            carousel_builder.build_opc_from_slide_plan(content, "s", str(tmp), media_paths={})
            self.assertFalse(legacy.called, "4-slide bundle plan should render, not fall back")

    def test_bundle_6_slide_plan_does_NOT_fall_back(self):
        tmp = Path("/tmp/test_opc_d_b6")
        tmp.mkdir(exist_ok=True)
        with mock.patch.object(carousel_builder, "_build_opc_html") as legacy:
            content = _make_content(6, bundle_id="cream_base_v1")
            carousel_builder.build_opc_from_slide_plan(content, "s", str(tmp), media_paths={})
            self.assertFalse(legacy.called, "6-slide bundle plan should render")

    def test_bundle_8_slide_plan_does_NOT_fall_back(self):
        tmp = Path("/tmp/test_opc_d_b8")
        tmp.mkdir(exist_ok=True)
        with mock.patch.object(carousel_builder, "_build_opc_html") as legacy:
            content = _make_content(8, bundle_id="cream_base_v1")
            carousel_builder.build_opc_from_slide_plan(content, "s", str(tmp), media_paths={})
            self.assertFalse(legacy.called, "8-slide bundle plan should render")

    def test_bundle_3_slide_plan_FALLS_BACK(self):
        # Below 4 → out of bundle range, falls back.
        tmp = Path("/tmp/test_opc_d_b3")
        tmp.mkdir(exist_ok=True)
        self._run(3, "cream_base_v1", tmp)
        self.assertTrue(self._legacy_called, "3-slide bundle plan out of range must fall back")

    def test_bundle_9_slide_plan_FALLS_BACK(self):
        # Above 8 → out of bundle range, falls back.
        tmp = Path("/tmp/test_opc_d_b9")
        tmp.mkdir(exist_ok=True)
        self._run(9, "cream_base_v1", tmp)
        self.assertTrue(self._legacy_called, "9-slide bundle plan out of range must fall back")

    def test_tip_stat_in_bundle_mode_reads_its_own_slot(self):
        """If opc_tip_stat lands at slide 4 in a bundle, it must fetch slot 4's image,
        NOT the hardcoded slot 2 from the legacy 5-slide assumption.

        This test verifies the D fix at carousel_builder.py:5783 — slide_num
        is passed from rs["slide"] instead of literal 2."""
        content = _make_content(6, bundle_id="cream_base_v1")
        # Put opc_tip_stat at slide 4 (not the usual slide 2).
        content["_slide_plan"]["slides"][3]["template_id"] = "opc_tip_stat"
        # Provide a distinct image at slot 4 vs slot 2 so we can assert which was fetched.
        media_paths = {
            "cover": "cover.jpg",
            "slides": {
                2: "resources/images/SLOT_2_WRONG.jpg",
                4: "resources/images/SLOT_4_CORRECT.jpg",
            },
        }
        tmp = Path("/tmp/test_opc_d_slot")
        tmp.mkdir(exist_ok=True)
        called_with = []

        def _spy_slot(slide_num, label, meta, paths):
            called_with.append(slide_num)
            return ""

        with mock.patch.object(carousel_builder, "_opc_tip_context_slot", side_effect=_spy_slot):
            carousel_builder.build_opc_from_slide_plan(
                content, "s", str(tmp), media_paths=media_paths,
            )
        # Slide 4 (tip_stat) should be in called_with — proves dynamic slot routing.
        self.assertIn(4, called_with,
                      f"tip_stat at slide 4 must read slot 4, but slot calls were {called_with}")


if __name__ == "__main__":
    unittest.main()
