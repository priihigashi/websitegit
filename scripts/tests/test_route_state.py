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
import evidence_scoring  # noqa: E402
import person_evidence_runner as runner  # noqa: E402
import route_state  # noqa: E402
import transcription  # noqa: E402


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
            "HTTP 402 [billing] Monthly usage quota exceeded",
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
    def test_hashtag_402_disables_apify_globally(self, post_run):
        post_run.return_value = (
            None,
            "HTTP 402 [billing] Monthly usage quota exceeded",
        )
        state = route_state.reset_state("auto")

        out = cc._ig_via_apify(["freigilson"], max_per_query=1)

        self.assertEqual(out, [])
        self.assertFalse(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "failed")

    @patch("candidate_collectors.APIFY_API_KEY", "apify_api_test")
    @patch("candidate_collectors._apify_post_run")
    def test_actor_specific_403_does_not_disable_apify(self, post_run):
        post_run.return_value = (
            None,
            "HTTP 403 [insufficient-permissions] Insufficient permissions for the Actor",
        )
        state = route_state.reset_state("auto")

        out = cc._ig_via_apify(["freigilson"], max_per_query=1)

        self.assertEqual(out, [])
        self.assertTrue(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "untried")

    def test_youtube_actor_403_is_stage_scoped(self):
        self.assertFalse(transcription._apify_failure_disables_route(
            "HTTP 403 [insufficient-permissions] Insufficient permissions for the Actor"
        ))

    def test_instagram_display_url_is_not_transcript_media(self):
        item = {
            "displayUrl": "https://example.com/thumb.jpg",
            "shortCode": "IMGONLY",
        }

        self.assertEqual(transcription._extract_ig_media_url(item), ("", ""))
        self.assertFalse(cc._item_has_video_media(item))

    def test_instagram_video_item_is_candidate_media(self):
        item = {
            "videoUrl": "https://example.com/video.mp4",
            "shortCode": "VID123",
            "url": "https://www.instagram.com/reel/VID123/",
        }

        self.assertTrue(cc._item_has_video_media(item))
        self.assertEqual(
            transcription._extract_ig_media_url(item),
            ("https://example.com/video.mp4", "videoUrl"),
        )

    def test_instagram_handle_variations_from_person_name(self):
        handles = cc._handle_variations("Frei Gilson", ["freigilsonpolemica"])

        self.assertIn("freigilson", handles)
        self.assertIn("frei.gilson", handles)
        self.assertIn("freigilsonoficial", handles)
        self.assertIn("freigilsonpolemica", handles)

    @patch("candidate_collectors.APIFY_API_KEY", "apify_api_test")
    def test_instagram_discovery_order_is_subject_first(self):
        calls = []

        def web(*args, **kwargs):
            calls.append("name_keyword")
            return []

        def username(*args, **kwargs):
            calls.append("person_handle")
            return []

        def hashtag(*args, **kwargs):
            calls.append("hashtag")
            return []

        def seed(*args, **kwargs):
            calls.append("seed_uploader")
            return []

        route_state.reset_state("auto")
        with patch("candidate_collectors._ig_via_web_search", side_effect=web), \
             patch("candidate_collectors._ig_via_username", side_effect=username), \
             patch("candidate_collectors._ig_via_apify", side_effect=hashtag), \
             patch("candidate_collectors._ig_via_verified_seed_uploader", side_effect=seed), \
             patch("candidate_collectors._ig_profile_handles_from_web", return_value=[]):
            cc.search_instagram_candidates(
                ["freigilsonpolemica"], person_name="Frei Gilson",
                seed_url="https://www.instagram.com/reel/SEED/",
            )

        self.assertEqual(calls, ["name_keyword", "person_handle", "hashtag", "seed_uploader"])

    def test_instagram_child_post_video_is_transcript_media(self):
        item = {
            "displayUrl": "https://example.com/thumb.jpg",
            "childPosts": [{"videoUrl": "https://example.com/child.mp4"}],
        }

        self.assertTrue(cc._item_has_video_media(item))
        self.assertEqual(
            transcription._extract_ig_media_url(item),
            ("https://example.com/child.mp4", "childPosts[0].videoUrl"),
        )

    def test_manifest_status_evidence_weak(self):
        manifest = evidence_scoring.build_manifest(
            seed_url="https://www.instagram.com/reel/SEED/",
            person_name="Frei Gilson",
            person_confidence=1.0,
            person_method="user_passed",
            requirement="same person evidence",
            niche="brazil",
            queries={},
            candidates_collected=13,
            candidates_transcribed=8,
            verified=[],
            rejected=[{"reason": "requirement_not_matched"}],
            target_count=6,
        )

        self.assertEqual(manifest["status"], "Needs Research — Evidence Weak")
        self.assertFalse(manifest["ready_for_render"])
        self.assertEqual(manifest["diagnostics"]["transcribed_count"], 8)
        self.assertEqual(
            manifest["diagnostics"]["candidate_count_by_route"],
            {"unknown": 1},
        )

    def test_manifest_status_ready_for_review(self):
        verified = [{"score": {"claim_type": "needs-context"}}] * 3
        manifest = evidence_scoring.build_manifest(
            seed_url="https://www.instagram.com/reel/SEED/",
            person_name="Frei Gilson",
            person_confidence=1.0,
            person_method="user_passed",
            requirement="same person evidence",
            niche="brazil",
            queries={},
            candidates_collected=3,
            candidates_transcribed=3,
            verified=verified,
            rejected=[],
            target_count=3,
        )

        self.assertEqual(manifest["status"], "Ready for Manifest Review")
        self.assertTrue(manifest["ready_for_render"])
        self.assertTrue(manifest["build_gates"]["ready_for_render"])

    def test_content_queue_range_extends_past_z(self):
        headers = [f"H{i}" for i in range(30)]

        self.assertEqual(runner._a1_col(1), "A")
        self.assertEqual(runner._a1_col(26), "Z")
        self.assertEqual(runner._a1_col(27), "AA")
        self.assertEqual(runner._header_range("📋 Content Queue", headers), "'📋 Content Queue'!A:AD")

    @patch("candidate_collectors.APIFY_API_KEY", "apify_api_test")
    @patch("candidate_collectors._apify_post_run")
    def test_no_paid_mode_does_not_call_apify(self, post_run):
        route_state.reset_state("no_paid_anthropic_apify")

        out = cc._ig_via_apify(["freigilson"], max_per_query=1)

        self.assertEqual(out, [])
        post_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
