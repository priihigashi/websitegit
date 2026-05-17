import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "content_creator"))

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

import carousel_reviewer


class CarouselReviewerPurposeModeTest(unittest.TestCase):
    def test_sources_gate_rejects_banned_opc_numeric_sources(self):
        issues = carousel_reviewer.check_sources_match_claims(
            {
                "slide2_stat": "UP TO $20K",
                "slide2_label": "Repair costs can blow up a bid.",
                "sources": [
                    "Angi/HomeAdvisor aggregate repair data",
                    "NAHB Cost of Constructing a Home",
                ],
            }
        )

        self.assertTrue(any("banned OPC source" in issue for issue in issues))
        self.assertTrue(any("Angi/HomeAdvisor" in issue for issue in issues))

    def test_sources_gate_rejects_aci_314_even_with_numeric_claim(self):
        issues = carousel_reviewer.check_sources_match_claims(
            {
                "slide2_stat": "40% MORE",
                "sources": [
                    "ACI 314.1R concrete maintenance guide",
                    "Oak Park Construction contractor notes",
                ],
            }
        )

        self.assertTrue(any("ACI 314.1R" in issue for issue in issues))

    def test_copy_coherence_uses_purpose_prompt_when_purposes_supplied(self):
        seen = {}

        def fake_score(prompt):
            seen["prompt"] = prompt
            return 2, "purpose-aware prompt used"

        with patch.object(carousel_reviewer, "_sonnet_score", fake_score):
            score, reason = carousel_reviewer._score_copy_coherence(
                [
                    "AVOID THE $5K SURPRISE",
                    "UP TO $15K",
                    "COST SPLIT",
                    "COMPARE TOTAL COST",
                    "WHERE THIS COMES FROM",
                ],
                purposes=[
                    {"slide": 1, "purpose": "hook"},
                    {"slide": 2, "purpose": "cost"},
                    {"slide": 3, "purpose": "teach"},
                    {"slide": 4, "purpose": "apply"},
                    {"slide": 5, "purpose": "sources"},
                ],
            )

        self.assertEqual(score, 2)
        self.assertEqual(reason, "purpose-aware prompt used")
        self.assertIn("declared purpose", seen["prompt"])
        self.assertIn("hook → cost → teach → apply → sources", seen["prompt"])

    def test_copy_coherence_uses_arc_prompt_without_purposes(self):
        seen = {}

        def fake_score(prompt):
            seen["prompt"] = prompt
            return 1, "arc prompt used"

        with patch.object(carousel_reviewer, "_sonnet_score", fake_score):
            score, reason = carousel_reviewer._score_copy_coherence(
                ["A", "B", "C"],
                purposes=None,
            )

        self.assertEqual(score, 1)
        self.assertEqual(reason, "arc prompt used")
        self.assertIn("complete story", seen["prompt"])
        self.assertNotIn("declared purpose", seen["prompt"])


if __name__ == "__main__":
    unittest.main()
