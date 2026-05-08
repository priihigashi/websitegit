import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

from opc_template_chooser import extract_comparison_pair, plan_carousel_slides
from carousel_builder import enforce_opc_comparison_parity
from carousel_reviewer import check_standalone_content


class OpcComparisonParityTest(unittest.TestCase):
    def test_extract_comparison_pair_from_vs_topic(self):
        pair = extract_comparison_pair("Concrete vs Pavers: Which Wins for Driveways?")
        self.assertEqual(pair, {"left": "Concrete", "right": "Pavers"})

    def test_extract_comparison_pair_strips_context_phrase(self):
        pair = extract_comparison_pair("Concrete vs pavers for a driveway: which wins?")
        self.assertEqual(pair, {"left": "Concrete", "right": "pavers"})

    def test_plan_carries_pair_and_avoids_singular_material_profile(self):
        plan = plan_carousel_slides("Concrete vs Pavers: Which Wins for Driveways?")
        self.assertEqual(plan["status"], "passed")
        self.assertEqual(plan.get("comparison_pair"), {"left": "Concrete", "right": "Pavers"})
        self.assertEqual(plan["slides"][2]["template_id"], "opc_four_card_grid")
        self.assertNotEqual(plan["slides"][1]["template_id"], "opc_material_profile")

    def test_enforce_comparison_parity_balances_media_queries(self):
        content = {
            "headline": "PAVERS WIN",
            "cover_visual": {"option_a": {"search_query": "paver driveway residential"}},
            "slides": [
                {"slide": 2, "context_image_query": "paver driveway residential"},
                {"slide": 3, "context_image_query": "paver installation residential"},
                {"slide": 4, "context_image_query": "paver repair residential"},
            ],
            "opc_four_card_grid": {
                "card_image_queries": [
                    "paver driveway residential",
                    "paver installation residential",
                    "paver repair residential",
                    "paver patio residential",
                ]
            },
        }
        out = enforce_opc_comparison_parity(
            content, "Concrete vs Pavers: Which Wins for Driveways?"
        )
        pair = out["_comparison_pair"]
        self.assertEqual(pair["left"], "Concrete")
        self.assertEqual(pair["right"], "Pavers")
        queries = out["opc_four_card_grid"]["card_image_queries"]
        joined = " ".join(queries).lower()
        self.assertIn("concrete", joined)
        self.assertIn("pavers", joined)
        self.assertIn("concrete", out["cover_visual"]["option_a"]["search_query"].lower())
        self.assertIn("pavers", out["cover_visual"]["option_a"]["search_query"].lower())

    def test_reviewer_flags_one_sided_cards(self):
        content = {
            "_comparison_pair": {"left": "Concrete", "right": "Pavers"},
            "opc_four_card_grid": {
                "eyebrow": "COMPARE · OPC",
                "headline_main": "Four",
                "headline_italic": "checks.",
                "subhead": "Compare before you choose.",
                "badges": ["A", "B", "C", "D"],
                "card_titles": ["DURABILITY", "INSTALL", "REPAIR", "COST"],
                "card_copies": [
                    "Concrete lasts 20-30 years.",
                    "Concrete pours faster.",
                    "Concrete needs slab repair.",
                    "Concrete is often cheaper.",
                ],
                "card_image_queries": [
                    "concrete driveway residential",
                    "concrete installation residential",
                    "concrete repair residential",
                    "concrete slab residential",
                ],
            },
        }
        issues = check_standalone_content(content, 3, "opc_four_card_grid")
        self.assertTrue(any("comparison pair" in issue for issue in issues))
        self.assertTrue(any("one-sided" in issue for issue in issues))

    def test_reviewer_passes_balanced_cards(self):
        content = {
            "_comparison_pair": {"left": "Concrete", "right": "Pavers"},
            "opc_four_card_grid": {
                "eyebrow": "COMPARE · OPC",
                "headline_main": "Four",
                "headline_italic": "checks.",
                "subhead": "Compare before you choose.",
                "badges": ["A", "B", "C", "D"],
                "card_titles": ["DURABILITY", "INSTALL", "REPAIR", "COST"],
                "card_copies": [
                    "Concrete: 20-30y. Pavers: 25-50y when reset.",
                    "Concrete: faster pour. Pavers: slower hand-set install.",
                    "Concrete: slab patch. Pavers: lift and reset stones.",
                    "Concrete: lower entry. Pavers: higher upfront cost.",
                ],
                "card_image_queries": [
                    "concrete driveway residential south florida",
                    "pavers driveway residential south florida",
                    "concrete installation detail residential",
                    "pavers installation detail residential",
                ],
            },
        }
        issues = check_standalone_content(content, 3, "opc_four_card_grid")
        self.assertEqual([], issues)


if __name__ == "__main__":
    unittest.main()
