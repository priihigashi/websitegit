import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "content_creator"))

from carousel_builder import (  # noqa: E402
    _is_query_too_generic,
    enforce_specific_context_queries,
)


class OpcQueryPolicyTest(unittest.TestCase):
    def test_single_generic_word_is_too_generic(self):
        self.assertTrue(_is_query_too_generic("kitchen"))
        self.assertTrue(_is_query_too_generic("construction work"))

    def test_specific_material_action_query_is_allowed(self):
        self.assertFalse(_is_query_too_generic("concrete driveway residential pour south florida"))

    def test_generic_context_query_rewritten_from_topic(self):
        content = {
            "slides": [
                {
                    "slide": 2,
                    "visual_hint": "context-image",
                    "context_image_query": "kitchen",
                    "context_image_query_alt": "home project",
                }
            ]
        }

        out = enforce_specific_context_queries(
            content,
            topic="Concrete paver driveway drainage repair cost",
            niche="opc",
        )

        primary = out["slides"][0]["context_image_query"]
        alt = out["slides"][0]["context_image_query_alt"]
        self.assertIn("concrete", primary)
        self.assertIn("residential south florida", primary)
        self.assertIn("driveway", alt)
        self.assertIn("_query_policy", out)
        self.assertEqual(len(out["_query_policy"]["generic_context_query_rewrites"]), 2)

    def test_specific_query_not_rewritten(self):
        content = {
            "slides": [
                {
                    "slide": 2,
                    "visual_hint": "context-image",
                    "context_image_query": "roof shingles installation aerial residential",
                }
            ]
        }

        out = enforce_specific_context_queries(content, topic="Roof permit mistake", niche="opc")

        self.assertEqual(
            out["slides"][0]["context_image_query"],
            "roof shingles installation aerial residential",
        )
        self.assertNotIn("_query_policy", out)


if __name__ == "__main__":
    unittest.main()
