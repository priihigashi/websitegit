import sys
import types
import unittest
from pathlib import Path


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

from opc_source_policy import (  # noqa: E402
    OPC_BANNED_SOURCE_PATTERNS,
    enforce_opc_source_policy,
    find_banned_source_hits,
)
import carousel_reviewer  # noqa: E402


class OpcSourcePolicyTest(unittest.TestCase):
    def test_strips_banned_sources_and_adds_safe_defaults(self):
        content = {
            "headline": "AVOID THE $20K TRAP",
            "proof_needed": "Angi/HomeAdvisor aggregate repair data",
            "slide2_stat": "UP TO $20K",
            "slide2_label": "Repair costs can blow up a bid (Angi/HomeAdvisor).",
            "sources": [
                "Angi/HomeAdvisor aggregate repair data",
                "Oak Park Construction contractor notes",
            ],
        }

        out = enforce_opc_source_policy(content, "Concrete driveway repair")

        self.assertIn("_opc_source_policy", out)
        self.assertTrue(out["_opc_source_policy"]["changed"])
        self.assertGreaterEqual(len(out["sources"]), 3)
        self.assertFalse(find_banned_source_hits(out))
        self.assertTrue(any("Florida Building Code" in src for src in out["sources"]))
        self.assertFalse(
            any("banned OPC source" in issue for issue in carousel_reviewer.check_sources_match_claims(out))
        )

    def test_clean_content_is_unchanged(self):
        content = {
            "headline": "FRAMING COST GAP",
            "slide2_stat": "16.6%",
            "slide2_label": "Framing is a major new-home cost category (NAHB).",
            "sources": [
                "NAHB Cost of Constructing a Home - construction cost category benchmarks",
                "Florida Building Code - residential building requirements",
                "International Residential Code - residential construction standards",
            ],
        }

        out = enforce_opc_source_policy(content, "Concrete block vs wood framing")

        self.assertIs(out, content)
        self.assertNotIn("_opc_source_policy", out)

    def test_reviewer_uses_shared_banned_source_policy(self):
        reviewer_tokens = {token for token, _ in carousel_reviewer._OPC_BANNED_SOURCE_PATTERNS}
        policy_tokens = {token for token, _ in OPC_BANNED_SOURCE_PATTERNS}

        self.assertEqual(reviewer_tokens, policy_tokens)

    def test_new_consumer_aggregators_are_blocked(self):
        for token in ("thumbtack", "fixr", "reddit", "quora", "wikihow"):
            with self.subTest(token=token):
                issues = carousel_reviewer.check_sources_match_claims(
                    {
                        "slide2_stat": "UP TO $12K",
                        "sources": [
                            f"{token.title()} repair cost guide",
                            "NAHB Cost of Constructing a Home",
                        ],
                    }
                )
                self.assertTrue(any("banned OPC source" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
