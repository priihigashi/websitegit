"""Unit tests for shared image vision fallback payloads.

Run:
  python3 -m unittest scripts/tests/test_vision_validator.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "content_creator"))

import vision_validator  # noqa: E402


class _FakeResponse:
    def read(self):
        return json.dumps({
            "choices": [{
                "message": {"content": "YES — matches the requested subject."}
            }]
        }).encode()


class VisionValidatorTests(unittest.TestCase):
    def test_openai_fallback_uses_data_url_image_payload(self):
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["payload"] = json.loads(req.data.decode())
            return _FakeResponse()

        with patch.object(vision_validator, "OPENAI_KEY", "sk-test"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, reason = vision_validator._validate_via_openai(
                "abc123",
                "image/png",
                "concrete foundation inspection",
            )

        self.assertTrue(ok)
        self.assertIn("openai-fallback", reason)
        self.assertEqual(captured["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(captured["payload"]["model"], "gpt-4o-mini")
        content = captured["payload"]["messages"][0]["content"]
        image_part = content[0]
        self.assertEqual(image_part["type"], "image_url")
        self.assertEqual(
            image_part["image_url"]["url"],
            "data:image/png;base64,abc123",
        )

    def test_openai_only_path_skips_anthropic_call(self):
        captured = {}

        def fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode())
            return _FakeResponse()

        image_bytes = (
            b"\x89PNG\r\n\x1a\n" + b"0" * 6000
        )
        with patch.object(vision_validator, "ANTHROPIC_KEY", ""), \
             patch.object(vision_validator, "OPENAI_KEY", "sk-test"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, reason = vision_validator.validate_image_bytes(
                image_bytes,
                "slide.png",
                "kitchen renovation",
            )

        self.assertTrue(ok)
        self.assertIn("openai-fallback", reason)
        self.assertEqual(captured["url"], "https://api.openai.com/v1/chat/completions")
        image_part = captured["payload"]["messages"][0]["content"][0]
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
