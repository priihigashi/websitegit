import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "content_creator"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "content"))

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
import content_auditor


def _html_fixture() -> str:
    slides = []
    for idx in range(1, 6):
        slides.append(
            f'<div class="slide"><h1>Slide {idx} concrete cost hook</h1>'
            f'<p>This slide has enough visible text to score the carousel narrative.</p></div>'
        )
    return "<html><body>" + "\n".join(slides) + "</body></html>"


class StorytellingTruncationRecoveryTest(unittest.TestCase):
    def test_storytelling_retries_with_compact_prompt_after_truncated_json(self):
        calls = []

        def fake_request(prompt, max_tokens):
            calls.append((prompt, max_tokens))
            if len(calls) == 1:
                return '{"overall": 82, "summary": "truncated"', ""
            return (
                '{"overall": 82, "summary": "compact recovery worked", '
                '"closing_callback_found": true, "closing_callback_text": "Go with pavers when drainage matters."}',
                "",
            )

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
            fh.write(_html_fixture())
            path = fh.name
        try:
            with patch.object(carousel_reviewer, "ANTHROPIC_KEY", "test-key"), patch.object(
                carousel_reviewer, "_request_storytelling_score", fake_request
            ):
                result = carousel_reviewer.score_storytelling(path, "opc")
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertEqual(result["overall"], 82)
        self.assertEqual(result["summary"], "compact recovery worked")
        self.assertEqual(calls[0][1], carousel_reviewer.STORYTELLING_MAX_TOKENS)
        self.assertEqual(calls[1][1], carousel_reviewer.STORYTELLING_RECOVERY_MAX_TOKENS)
        self.assertIn("previous JSON response was invalid", calls[1][0])

    def test_storytelling_unparseable_after_retry_returns_warning_not_empty_score(self):
        def fake_request(_prompt, _max_tokens):
            return '{"overall": 82, "summary": "still truncated"', ""

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
            fh.write(_html_fixture())
            path = fh.name
        try:
            with patch.object(carousel_reviewer, "ANTHROPIC_KEY", "test-key"), patch.object(
                carousel_reviewer, "_request_storytelling_score", fake_request
            ):
                result = carousel_reviewer.score_storytelling(path, "opc")
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertIn("_warning", result)
        self.assertIn("Storytelling JSON unparseable", result["_warning"])
        self.assertNotIn("overall", result)

    def test_content_auditor_failure_reason_prefers_error_then_notes(self):
        self.assertIn(
            "parse_failed",
            content_auditor._audit_failure_reason(
                {"error": "parse_failed: missing OVERALL SCORE and VERDICT", "full_response": "raw"}
            ),
        )
        self.assertEqual(
            content_auditor._audit_failure_reason(
                {
                    "error": None,
                    "full_response": "OVERALL SCORE: 5\nVERDICT: FAIL\nNOTES: Claim lacks source gap.",
                }
            ),
            "Claim lacks source gap.",
        )


if __name__ == "__main__":
    unittest.main()
