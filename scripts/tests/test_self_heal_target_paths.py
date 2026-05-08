"""Self-heal target-file path resolution tests.

Run:
  python3 -m unittest scripts/tests/test_self_heal_target_paths.py
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "self_heal"))

sys.modules.setdefault("github", types.SimpleNamespace(Github=object))

import orchestrator  # noqa: E402


class _FakeRepo:
    def __init__(self, existing):
        self.existing = set(existing)

    def get_contents(self, path, ref="main"):
        if path not in self.existing:
            raise FileNotFoundError(path)
        return object()


class SelfHealTargetPathTests(unittest.TestCase):
    def test_stale_drive_map_scan_alias_resolves_to_builder(self):
        repo = _FakeRepo({"scripts/drive_map_builder.py"})

        path, note = orchestrator.resolve_target_file_path(
            repo,
            "scripts/drive_map_scan.py",
        )

        self.assertEqual(path, "scripts/drive_map_builder.py")
        self.assertIn("target_file_alias", note)

    def test_daily_content_js_alias_resolves_to_python(self):
        repo = _FakeRepo({"scripts/daily_content_processor.py"})

        path, note = orchestrator.resolve_target_file_path(
            repo,
            "scripts/daily_content_processor.js",
        )

        self.assertEqual(path, "scripts/daily_content_processor.py")
        self.assertIn("target_file_alias", note)

    def test_or_similar_path_resolves_exact_alias(self):
        repo = _FakeRepo({"scripts/add-gsc-headers.js"})

        path, note = orchestrator.resolve_target_file_path(
            repo,
            "scripts/add-gsc-headers.js or similar",
        )

        self.assertEqual(path, "scripts/add-gsc-headers.js")
        self.assertIn("target_file_alias", note)

    def test_existing_path_is_left_unchanged(self):
        repo = _FakeRepo({"scripts/research.js"})

        path, note = orchestrator.resolve_target_file_path(
            repo,
            "scripts/research.js",
        )

        self.assertEqual(path, "scripts/research.js")
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
