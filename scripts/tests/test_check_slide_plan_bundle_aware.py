import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

google = types.ModuleType("google")
google_oauth2 = types.ModuleType("google.oauth2")
google_oauth2_credentials = types.ModuleType("google.oauth2.credentials")
google_oauth2_credentials.Credentials = object
googleapiclient = types.ModuleType("googleapiclient")
googleapiclient_discovery = types.ModuleType("googleapiclient.discovery")
googleapiclient_http = types.ModuleType("googleapiclient.http")
googleapiclient_discovery.build = lambda *args, **kwargs: None
googleapiclient_http.MediaIoBaseDownload = object
googleapiclient_http.MediaFileUpload = object
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.oauth2", google_oauth2)
sys.modules.setdefault("google.oauth2.credentials", google_oauth2_credentials)
sys.modules.setdefault("googleapiclient", googleapiclient)
sys.modules.setdefault("googleapiclient.discovery", googleapiclient_discovery)
sys.modules.setdefault("googleapiclient.http", googleapiclient_http)

from carousel_reviewer import check_slide_plan  # noqa: E402
from opc_template_chooser import plan_carousel_slides  # noqa: E402


class CheckSlidePlanBundleAwareTest(unittest.TestCase):
    def test_bundle_plan_with_6_slides_passes(self):
        plan = plan_carousel_slides(
            "Concrete vs pavers for a driveway",
            bundle_id="cream_base_v1",
            target_slide_count=6,
        )

        issues = check_slide_plan({
            "_slide_plan": plan,
            "opc_four_card_grid": {
                "eyebrow": "COMPARE · OPC",
                "headline_main": "Four",
                "headline_italic": "checks.",
                "subhead": "Compare before you choose.",
                "badges": ["COST", "DRAIN", "REPAIR", "LOOK"],
                "card_titles": ["COST", "DRAINAGE", "REPAIR", "FINISH"],
                "card_copies": [
                    "Concrete and pavers price differently by scope.",
                    "Concrete and pavers both need drainage planning.",
                    "Concrete and pavers have different repair paths.",
                    "Concrete and pavers fit different finish goals.",
                ],
            },
            "opc_statement": {
                "tag": "FROM THE FIELD",
                "quote_opener": "Compare the whole job.",
                "quote_body": "The hidden cost is usually drainage, base prep, or repair access.",
                "attribution": "MIKE · OPC",
            },
        })

        self.assertEqual(issues, [])

    def test_legacy_plan_still_rejects_non_5_without_bundle_id(self):
        plan = {
            "status": "passed",
            "slides": [
                {"slide": 1, "role": "cover", "template_id": "opc_tip_cover", "production_safe": True},
                {"slide": 2, "role": "sources", "template_id": "opc_tip_sources", "production_safe": True},
            ],
        }

        issues = check_slide_plan({"_slide_plan": plan})

        self.assertTrue(any("expected 5" in issue for issue in issues))

    def test_bundle_plan_rejects_cross_bundle_template(self):
        plan = plan_carousel_slides(
            "Concrete vs pavers for a driveway",
            bundle_id="cream_base_v1",
            target_slide_count=4,
        )
        plan["slides"][1]["template_id"] = "opc_progress_media"

        issues = check_slide_plan({"_slide_plan": plan})

        self.assertTrue(any("not allowed in bundle" in issue for issue in issues))
        self.assertTrue(any("cross-bundle bleed" in issue for issue in issues))

    def test_bundle_plan_rejects_out_of_range_length(self):
        plan = plan_carousel_slides(
            "Concrete vs pavers for a driveway",
            bundle_id="cream_base_v1",
            target_slide_count=8,
        )
        plan["slides"].append(
            {"slide": 9, "role": "sources", "template_id": "opc_tip_sources", "production_safe": True}
        )

        issues = check_slide_plan({"_slide_plan": plan})

        self.assertTrue(any("outside bundle" in issue for issue in issues))

    def test_bundle_plan_rejects_missing_cover_and_sources(self):
        plan = plan_carousel_slides(
            "Concrete vs pavers for a driveway",
            bundle_id="cream_base_v1",
            target_slide_count=4,
        )
        plan["slides"][0]["template_id"] = "opc_statement"
        plan["slides"][-1]["template_id"] = "opc_tip_list"

        issues = check_slide_plan({"_slide_plan": plan})

        self.assertTrue(any("slide 1 must be opc_tip_cover" in issue for issue in issues))
        self.assertTrue(any("must be opc_tip_sources" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
