"""Self-heal repo-local reference discovery tests.

Run:
  python3 -m unittest scripts/tests/test_self_heal_reference_context.py
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


class SelfHealReferenceContextTests(unittest.TestCase):
    def _auth_task(self):
        return {
            "ID": "SH-046",
            "Title": "Sibling-file pattern discovery before AI patch generation",
            "Description": (
                "AI patched photo_matcher.py without finding the working OAuth "
                "refresh-token exchange pattern in sibling image_library.py. "
                "Require reference_files_consulted for SHEETS_TOKEN fixes."
            ),
            "Verification Method": "Prompt should include sibling OAuth reference context.",
        }

    def test_auth_task_discovers_sibling_oauth_pattern(self):
        target = _REPO / "scripts" / "content_creator" / "photo_matcher.py"
        task = self._auth_task()

        context, refs = orchestrator.discover_reference_context(
            task,
            "scripts/content_creator/photo_matcher.py",
            target.read_text(encoding="utf-8"),
        )

        self.assertIn("scripts/content_creator/image_library.py", refs)
        self.assertIn("_oauth_token", context)
        self.assertIn("refresh_token", context)

    def test_missing_target_directory_returns_empty_context(self):
        task = {
            "ID": "SH-999",
            "Title": "No local sibling files",
            "Description": "Synthetic task for a missing path.",
            "Verification Method": "No crash.",
        }

        context, refs = orchestrator.discover_reference_context(
            task,
            "scripts/does_not_exist/nope.py",
            "",
        )

        self.assertEqual(context, "")
        self.assertEqual(refs, [])

    def test_patch_prompt_schema_requires_references(self):
        self.assertIn("reference_files_consulted", orchestrator.PATCH_SYSTEM_PROMPT)
        self.assertIn("REFERENCE FILE CONTEXT", orchestrator.PATCH_SYSTEM_PROMPT)

    def test_auth_patch_missing_reference_is_rejected(self):
        target = _REPO / "scripts" / "content_creator" / "photo_matcher.py"

        ok, reason = orchestrator.patch_satisfies_reference_guard(
            self._auth_task(),
            "scripts/content_creator/photo_matcher.py",
            target.read_text(encoding="utf-8"),
            {"decision": "PATCH", "reference_files_consulted": []},
        )

        self.assertFalse(ok)
        self.assertIn("omitted", reason)

    def test_auth_patch_with_discovered_reference_is_allowed(self):
        target = _REPO / "scripts" / "content_creator" / "photo_matcher.py"

        ok, reason = orchestrator.patch_satisfies_reference_guard(
            self._auth_task(),
            "scripts/content_creator/photo_matcher.py",
            target.read_text(encoding="utf-8"),
            {
                "decision": "PATCH",
                "reference_files_consulted": [
                    "scripts/content_creator/image_library.py",
                ],
            },
        )

        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
