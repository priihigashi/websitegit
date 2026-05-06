"""test_sh104_approval.py — Phase 4 reply-token parser tests (no network).

Verifies approval_handler.parse_sh104_reply + is_sh104_reply +
_extract_sh104_metadata work correctly against fixture replies, before
any live Gmail polling.
"""

from __future__ import annotations
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "content_creator"))

import approval_handler as ah  # noqa: E402


class Sh104DetectionTests(unittest.TestCase):
    def test_sh104_subject_detected(self):
        self.assertTrue(ah.is_sh104_reply(
            "Re: [SH-104] Evidence manifest ready — Frei Gilson — brazil"))
        self.assertTrue(ah.is_sh104_reply("re: [sh-104] foo"))

    def test_non_sh104_subject_passes_through(self):
        self.assertFalse(ah.is_sh104_reply("Re: [REVIEW] OPC — daily content"))
        self.assertFalse(ah.is_sh104_reply("Re: DAILY CONTENT — 2026-05-06"))


class Sh104ReplyParserTests(unittest.TestCase):
    def test_approve_manifest(self):
        r = ah.parse_sh104_reply("APPROVE MANIFEST")
        self.assertEqual(r["action"], "approve_manifest")

    def test_render_carousel(self):
        r = ah.parse_sh104_reply("RENDER CAROUSEL")
        self.assertEqual(r["action"], "render_carousel")

    def test_render_remotion_with_extra_text(self):
        r = ah.parse_sh104_reply("RENDER REMOTION\nplease use evidence #2 as cover")
        self.assertEqual(r["action"], "render_remotion")
        self.assertIn("evidence", r["feedback"])

    def test_needs_more_evidence(self):
        self.assertEqual(
            ah.parse_sh104_reply("NEEDS MORE EVIDENCE")["action"],
            "needs_more_evidence",
        )

    def test_reject_manifest(self):
        self.assertEqual(
            ah.parse_sh104_reply("REJECT MANIFEST")["action"],
            "reject_manifest",
        )

    def test_render_preview_tokens(self):
        self.assertEqual(ah.parse_sh104_reply("APPROVE PREVIEW")["action"],
                         "approve_preview")
        self.assertEqual(ah.parse_sh104_reply("CHANGE")["action"],
                         "render_change")
        self.assertEqual(ah.parse_sh104_reply("REJECT")["action"],
                         "render_reject")

    def test_unknown_token(self):
        r = ah.parse_sh104_reply("looks good thanks")
        self.assertEqual(r["action"], "unknown")
        self.assertEqual(r["raw_token"], "")

    def test_case_insensitive(self):
        # Tokens use upper() comparison — caller must accept any case.
        self.assertEqual(
            ah.parse_sh104_reply("approve manifest")["action"],
            "approve_manifest",
        )

    def test_empty_reply(self):
        r = ah.parse_sh104_reply("")
        self.assertEqual(r["action"], "unknown")


class SubjectMetadataTests(unittest.TestCase):
    def test_extract_person_and_niche_em_dash(self):
        m = ah._extract_sh104_metadata(
            "Re: [SH-104] Evidence manifest ready — Frei Gilson — brazil")
        self.assertEqual(m["person_name"], "Frei Gilson")
        self.assertEqual(m["niche"], "brazil")

    def test_extract_person_and_niche_hyphen(self):
        m = ah._extract_sh104_metadata(
            "Re: [SH-104] Evidence manifest ready - Test Person - usa")
        self.assertEqual(m["person_name"], "Test Person")
        self.assertEqual(m["niche"], "usa")

    def test_no_metadata(self):
        m = ah._extract_sh104_metadata("Re: weird subject")
        self.assertEqual(m["person_name"], "")
        self.assertEqual(m["niche"], "brazil")


if __name__ == "__main__":
    unittest.main()
