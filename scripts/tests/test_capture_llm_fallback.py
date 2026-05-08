import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CAPTURE_PATH = ROOT / "scripts" / "capture" / "capture_pipeline.py"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _load_capture_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    name = "capture_pipeline_test_module"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaptureLlmFallbackTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-openai-key",
                "CLAUDE_KEY_4_CONTENT": "",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_generate_content_brief_uses_openai_when_claude_key_missing(self):
        module = _load_capture_module()
        payload = {"choices": [{"message": {"content": "CONTENT BRIEF\nStatus: DRAFT"}}]}
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)) as urlopen:
            brief = module.generate_content_brief(
                "Transcript about a remodel.",
                "https://example.com/reel",
                {"niche": "Oak Park", "content_type": "Product Tips"},
                "make it practical",
            )

        self.assertIn("CONTENT BRIEF", brief)
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["model"], "gpt-4o")
        self.assertIn("Transcript about a remodel", sent["messages"][0]["content"])

    def test_detect_project_uses_openai_when_claude_key_missing(self):
        module = _load_capture_module()
        payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"project":"opc","confidence":0.91,"reason":"construction transcript"}'
                    }
                }
            ]
        }
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
            project, confidence, reason = module.detect_project(
                "Kitchen remodel permit and countertop installation tips.",
                "",
                "",
            )

        self.assertEqual(project, "opc")
        self.assertGreaterEqual(confidence, 0.9)
        self.assertIn("construction", reason)


if __name__ == "__main__":
    unittest.main()
