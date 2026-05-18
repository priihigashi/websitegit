"""4AM agent LLM cascade should not hard-crash before fallback can run."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "scripts" / "4am_agent"


class _AnthropicShouldBeLazy:
    def __init__(self, *args, **kwargs):
        raise AssertionError("Anthropic client should not be constructed at import time")


def _install_import_stubs():
    sys.path.insert(0, str(AGENT_DIR))
    sys.modules["requests"] = types.SimpleNamespace(get=lambda *a, **k: None, post=lambda *a, **k: None, put=lambda *a, **k: None)
    sys.modules["pytz"] = types.SimpleNamespace(timezone=lambda name: ZoneInfo(name), UTC=timezone.utc)
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=_AnthropicShouldBeLazy)
    sys.modules["context_reader"] = types.SimpleNamespace(get_context_summary=lambda: "")

    googleapiclient = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda *a, **k: None
    google = types.ModuleType("google")
    oauth2 = types.ModuleType("google.oauth2")
    credentials = types.ModuleType("google.oauth2.credentials")
    credentials.Credentials = object
    sys.modules["googleapiclient"] = googleapiclient
    sys.modules["googleapiclient.discovery"] = discovery
    sys.modules["google"] = google
    sys.modules["google.oauth2"] = oauth2
    sys.modules["google.oauth2.credentials"] = credentials


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FourAmLazyClientTests(unittest.TestCase):
    def test_4am_modules_import_without_claude_key_and_use_cascade(self):
        _install_import_stubs()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "openai-ok"}, clear=True):
            modules = {
                "self_healer": _load_module(AGENT_DIR / "self_healer.py", "self_healer_lazy_test"),
                "pattern_learner": _load_module(AGENT_DIR / "pattern_learner.py", "pattern_learner_lazy_test"),
                "chat_log_reader": _load_module(AGENT_DIR / "chat_log_reader.py", "chat_log_reader_lazy_test"),
                "script_generator": _load_module(AGENT_DIR / "script_generator.py", "script_generator_lazy_test"),
            }

        modules["self_healer"].llm_text = lambda *a, **k: "ok"
        modules["pattern_learner"].llm_text = lambda *a, **k: "ok"
        modules["chat_log_reader"]._llm_text_cascade = lambda *a, **k: "ok"
        modules["script_generator"]._llm_text_cascade = lambda *a, **k: "ok"

        self.assertEqual(modules["self_healer"]._llm("prompt"), "ok")
        self.assertEqual(modules["pattern_learner"]._llm("prompt", tier="haiku", max_tokens=10), "ok")
        self.assertEqual(modules["chat_log_reader"]._llm("prompt", tier="haiku", max_tokens=10, context="test"), "ok")
        self.assertEqual(modules["script_generator"]._llm("prompt", tier="sonnet", max_tokens=10), "ok")


if __name__ == "__main__":
    unittest.main()
