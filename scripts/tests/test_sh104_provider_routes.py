"""Provider-route regression tests for SH-104.

These tests mock Apify/network calls. They do not spend provider credits.

Run:
  python3 -m unittest scripts/tests/test_sh104_provider_routes.py
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
import transcription  # noqa: E402


class _FakeDownload:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"0" * 8000


class Sh104ProviderRouteTests(unittest.TestCase):
    def tearDown(self):
        route_state.reset_state("auto")
        transcription._apify_limit_hit = False
        cc._apify_search_limit_hit = False

    def test_apidojo_youtube_prefers_streamingdata_audio_url(self):
        calls = []

        def fake_apify(method, path, *, params=None, json_body=None, timeout=30):
            calls.append((method, path, json_body))
            if method == "POST":
                return {"data": {"id": "run123"}}, None
            if path == "/actor-runs/run123":
                return {"data": {"status": "SUCCEEDED"}}, None
            if path == "/actor-runs/run123/dataset/items":
                return [{
                    "streamingData": {
                        "formats": [{
                            "mimeType": "video/mp4",
                            "url": "https://rr1---sn-video.googlevideo.com/videoplayback",
                        }],
                        "adaptiveFormats": [{
                            "mimeType": "audio/mp4",
                            "url": "https://rr1---sn-audio.googlevideo.com/videoplayback",
                        }],
                    }
                }], None
            return {}, None

        opened_urls = []

        def fake_urlopen(url, timeout=120):
            opened_urls.append(url)
            return _FakeDownload()

        route_state.reset_state("auto")
        with patch.object(transcription, "APIFY_API_KEY", "apify_test"), \
             patch.object(transcription, "OPENAI_API_KEY", "openai_test"), \
             patch.object(transcription, "_apify_request", side_effect=fake_apify), \
             patch.object(transcription.time, "sleep", return_value=None), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("os.path.getsize", return_value=8000), \
             patch.object(transcription, "_whisper_transcribe", return_value="transcript text"):
            text = transcription._apify_yt_whisper("dQw4w9WgXcQ")

        self.assertEqual(text, "transcript text")
        self.assertEqual(opened_urls, ["https://rr1---sn-audio.googlevideo.com/videoplayback"])
        post_payload = calls[0][2]
        self.assertEqual(post_payload["maxItems"], 1)
        self.assertEqual(
            post_payload["startUrls"][0]["url"],
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_apidojo_youtube_no_media_is_stage_scoped(self):
        def fake_apify(method, path, *, params=None, json_body=None, timeout=30):
            if method == "POST":
                return {"data": {"id": "run123"}}, None
            if path == "/actor-runs/run123":
                return {"data": {"status": "SUCCEEDED"}}, None
            if path == "/actor-runs/run123/dataset/items":
                return [{"title": "metadata only", "url": "https://youtube.com/watch?v=dQw4w9WgXcQ"}], None
            return {}, None

        state = route_state.reset_state("auto")
        with patch.object(transcription, "APIFY_API_KEY", "apify_test"), \
             patch.object(transcription, "_apify_request", side_effect=fake_apify), \
             patch.object(transcription.time, "sleep", return_value=None):
            text = transcription._apify_yt_whisper("dQw4w9WgXcQ")

        self.assertEqual(text, "")
        snap = state.snapshot()
        self.assertEqual(snap["route_status"]["apify"], "untried")
        self.assertIn("yt_audio_no_media", snap["route_failures"][0]["stage"])

    def test_tiktok_discovery_maps_dataset_items(self):
        route_state.reset_state("auto")
        with patch.object(cc, "APIFY_API_KEY", "apify_test"), \
             patch.object(cc, "_apify_post_run", return_value=("run123", None)), \
             patch.object(cc, "_apify_poll_run", return_value="SUCCEEDED"), \
             patch.object(cc, "_apify_fetch_items", return_value=[{
                 "id": "735",
                 "webVideoUrl": "https://www.tiktok.com/@freigilson/video/735",
                 "text": "Frei Gilson fala sobre mulheres",
                 "authorMeta": {"name": "freigilson"},
                 "videoMeta": {"duration": 42},
                 "createTimeISO": "2026-05-07T12:00:00Z",
                 "searchQuery": "Frei Gilson mulheres",
             }]):
            out = cc.search_tiktok_candidates(["Frei Gilson mulheres"], max_per_query=2)

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["platform"], "tiktok")
        self.assertEqual(out[0]["route"], "tt_search")
        self.assertEqual(out[0]["duration"], 42)
        self.assertEqual(out[0]["upload_date"], "2026-05-07")

    def test_tiktok_400_is_stage_scoped_but_402_disables_apify(self):
        state = route_state.reset_state("auto")
        with patch.object(cc, "APIFY_API_KEY", "apify_test"), \
             patch.object(cc, "_apify_post_run", return_value=(
                 None, "HTTP 400 [invalid-input] Field input.searchQueries is invalid"
             )):
            self.assertEqual(cc.search_tiktok_candidates(["Frei Gilson"]), [])
        self.assertTrue(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "untried")

        state = route_state.reset_state("auto")
        with patch.object(cc, "APIFY_API_KEY", "apify_test"), \
             patch.object(cc, "_apify_post_run", return_value=(
                 None, "HTTP 402 [billing] Monthly usage quota exceeded"
             )):
            self.assertEqual(cc.search_tiktok_candidates(["Frei Gilson"]), [])
        self.assertFalse(state.should_try_apify())
        self.assertEqual(state.snapshot()["route_status"]["apify"], "failed")


if __name__ == "__main__":
    unittest.main()
