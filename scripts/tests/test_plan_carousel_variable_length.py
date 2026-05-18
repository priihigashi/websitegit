import os
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

from opc_template_chooser import plan_carousel_slides  # noqa: E402


class PlanCarouselVariableLengthTest(unittest.TestCase):
    def test_default_legacy_plan_still_returns_5(self):
        os.environ["OPC_DISABLE_STANDALONES"] = "1"
        plan = plan_carousel_slides("Concrete vs pavers for a driveway")

        self.assertEqual(plan["status"], "passed")
        self.assertEqual(len(plan["slides"]), 5)
        self.assertEqual(plan["slides"][0]["role"], "cover")
        self.assertEqual(plan["slides"][-1]["role"], "sources")

    def test_cream_bundle_can_return_4_slides(self):
        plan = plan_carousel_slides(
            "The one cost contractors hide",
            bundle_id="cream_base_v1",
            target_slide_count=4,
        )

        self.assertEqual(plan["status"], "passed")
        self.assertEqual(plan["bundle_id"], "cream_base_v1")
        self.assertEqual(len(plan["slides"]), 4)
        self.assertEqual(plan["slides"][0]["template_id"], "opc_tip_cover")
        self.assertEqual(plan["slides"][-1]["template_id"], "opc_tip_sources")
        self.assertIn("opc_statement", [s["template_id"] for s in plan["slides"]])

    def test_comparison_bundle_prefers_four_card_grid(self):
        plan = plan_carousel_slides(
            "Concrete vs pavers for a driveway",
            bundle_id="cream_base_v1",
            target_slide_count=6,
        )

        self.assertEqual(len(plan["slides"]), 6)
        self.assertIn("opc_four_card_grid", [s["template_id"] for s in plan["slides"]])
        self.assertEqual(plan["comparison_pair"], {"left": "Concrete", "right": "pavers"})

    def test_dark_bundle_clamps_to_max_7(self):
        plan = plan_carousel_slides(
            "Kitchen renovation before and after project update",
            bundle_id="dark_base_v1",
            target_slide_count=8,
        )

        self.assertEqual(plan["status"], "passed")
        self.assertEqual(plan["bundle_id"], "dark_base_v1")
        self.assertEqual(len(plan["slides"]), 7)
        self.assertEqual(plan["color_family"], "dark")
        self.assertIn("opc_progress_media", [s["template_id"] for s in plan["slides"]])

    def test_unknown_bundle_blocks_without_legacy_fallback(self):
        plan = plan_carousel_slides("Concrete vs pavers", bundle_id="missing_bundle")

        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["slides"], [])


if __name__ == "__main__":
    unittest.main()
