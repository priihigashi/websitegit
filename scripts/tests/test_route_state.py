"""test_route_state.py — SH-104 route-state fallback contracts.

Run:
  python3 -m unittest scripts/tests/test_route_state.py
"""

from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "research"))

import candidate_collectors as cc  # noqa: E402
import route_state  # noqa: E402


class ApifyRouteStateTests(unittest.TestCase):
    def tearDown(self):
        route_state.reset_state("auto")
        cc._apify_search_limit_hit = False

    def test_stage_400_does_not_disable_apify(self):
        state = route_state.reset_state("auto")
        state.mark_stage_failed(
            "apify",
            "ig_hashtag_start:apify~instagram-hashtag-scraper",
            "HTTP 400 [invalid-input] Field input.username is required",
        )

        self.assertTrue(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "untried")
        self.assertEqual(len(state.snapshot()["route_failures"]), 1)

    def test_billing_or_auth_failure_disables_apify(self):
        state = route_state.reset_state("auto")
        state.mark_failed(
            "apify",
            "ig_audio_start_quota",
            "HTTP 403 [provider-access] Forbidden",
        )

        self.assertFalse(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "failed")

    def test_no_paid_mode_skips_apify(self):
        state = route_state.reset_state("no_paid_anthropic_apify")

        self.assertFalse(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "skipped")

    @patch("candidate_collectors.APIFY_API_KEY", "apify_api_test")
    @patch("candidate_collectors._apify_post_run")
    def test_hashtag_400_does_not_block_direct_url_route(self, post_run):
        post_run.return_value = (
            None,
            "HTTP 400 [invalid-input] Field input.hashtags is invalid",
        )
        state = route_state.reset_state("auto")

        out = cc._ig_via_apify(["freigilson"], max_per_query=1)

        self.assertEqual(out, [])
        self.assertTrue(state.should_try_apify())
        snap = state.snapshot()
        self.assertEqual(snap["route_status"]["apify"], "untried")
        self.assertEqual(snap["route_failures"][0]["route"], "apify")
        self.assertIn("ig_hashtag_start", snap["route_failures"][0]["stage"])

    @patch("candidate_collectors.APIFY_API_KEY", "apify_api_test")
    @patch("candidate_collectors._apify_post_run")
    def test_hashtag_403_disables_apify_globally(self, post_run):
        post_run.return_value = (
            None,
            "HTTP 403 [provider-access] Forbidden",
        )
        state = route_state.reset_state("auto")

        out = cc._ig_via_apify(["freigilson"], max_per_query=1)

        self.assertEqual(out, [])
        self.assertFalse(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "failed")

    @patch("candidate_collectors.APIFY_API_KEY", "apify_api_test")
    @patch("candidate_collectors._apify_post_run")
    def test_no_paid_mode_does_not_call_apify(self, post_run):
        route_state.reset_state("no_paid_anthropic_apify")

        out = cc._ig_via_apify(["freigilson"], max_per_query=1)

        self.assertEqual(out, [])
        post_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
