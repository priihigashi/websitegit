"""test_person_evidence_dispatcher.py — Phase 1.5 dry-run test (no paid APIs).

Mocks subprocess.run + ANTHROPIC client so we can prove the auto-dispatch
flow end-to-end without spending Apify or Anthropic credits.

Run:
  python3 -m unittest scripts/tests/test_person_evidence_dispatcher.py
"""

from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO / "scripts" / "capture"))
sys.path.insert(0, str(_REPO / "scripts" / "research"))

import person_evidence_dispatcher as ped  # noqa: E402


class TriggerDetectionTests(unittest.TestCase):
    def test_explicit_mode_match(self):
        self.assertTrue(ped.is_evidence_mining_request("mode: person_evidence_mining"))

    def test_natural_phrase_match(self):
        self.assertTrue(ped.is_evidence_mining_request(
            "find more clips of this person, same guy"))

    def test_clip_mine_match(self):
        self.assertTrue(ped.is_evidence_mining_request("clip-mine this please"))
        self.assertTrue(ped.is_evidence_mining_request("clip mining run"))

    def test_word_boundary(self):
        # B8 fix: trailing chars must not match
        self.assertFalse(ped.is_evidence_mining_request("person_evidence_miningfoo"))
        self.assertFalse(ped.is_evidence_mining_request(
            "mode: person_evidence_miningfoo"))

    def test_no_match(self):
        self.assertFalse(ped.is_evidence_mining_request("just capture this normally"))
        self.assertFalse(ped.is_evidence_mining_request(""))


class NotesParserTests(unittest.TestCase):
    def test_target_count_numeric(self):
        self.assertEqual(ped.parse_target_count("find 8 more clips"), 8)
        # parse_target_count's "count:" pattern still requires trailing
        # "clips" — confirms regex anchor is intentional.
        self.assertEqual(ped.parse_target_count("count: 12 clips"), 12)

    def test_target_count_word(self):
        self.assertEqual(ped.parse_target_count("find six more clips"), 6)
        self.assertEqual(ped.parse_target_count("ten more clips of him"), 10)

    def test_target_count_clamp(self):
        # n > 20 -> default
        self.assertEqual(ped.parse_target_count("find 99 more clips"), 6)

    def test_person_name(self):
        self.assertEqual(
            ped.parse_person_name("Person: Frei Gilson\nfind same guy"),
            "Frei Gilson",
        )
        self.assertEqual(
            ped.parse_person_name("his name is Mike Smith and he said..."),
            "Mike Smith",
        )

    def test_evidence_requirement(self):
        notes = ("Requirement: statements about women that contradict his "
                 "public religious persona")
        r = ped.parse_evidence_requirement(notes)
        self.assertIn("women", r)
        self.assertIn("religious persona", r)


class DispatchTests(unittest.TestCase):
    """Prove that with mocked subprocess, dispatcher fires the right CLI."""

    @patch("person_evidence_dispatcher.llm_text")
    def test_person_inference_uses_llm_cascade(self, mock_llm):
        mock_llm.return_value = '{"name":"Frei Gilson","confidence":0.92}'
        name, confidence = ped.infer_person_name(
            caption="Trecho de pregação de Frei Gilson",
            transcript="Frei Gilson fala no vídeo sobre mulheres.",
            creator_name="",
        )
        self.assertEqual(name, "Frei Gilson")
        self.assertAlmostEqual(confidence, 0.92)
        kwargs = mock_llm.call_args.kwargs
        self.assertEqual(kwargs["model_tier"], "haiku")
        self.assertEqual(kwargs["temperature"], 0)

    @patch("person_evidence_dispatcher.llm_text")
    def test_person_inference_extracts_json_from_wrapped_response(self, mock_llm):
        mock_llm.return_value = 'Here is the JSON:\n{"name":"Jane Doe","confidence":0.81}'
        name, confidence = ped.infer_person_name(
            caption="Jane Doe says this on camera.",
            transcript="",
            creator_name="",
        )
        self.assertEqual(name, "Jane Doe")
        self.assertAlmostEqual(confidence, 0.81)

    @patch("person_evidence_dispatcher.subprocess.run")
    def test_dispatch_command_shape(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="dispatched", stderr="")
        ok, msg = ped.dispatch_evidence_mining(
            seed_url="https://www.instagram.com/reel/ABC123/",
            person_name="Test Person",
            evidence_requirement="some requirement here that's >= 8 chars",
            target_clip_count=6,
            niche="brazil",
        )
        self.assertTrue(ok)
        # Verify gh CLI args contain all 5 -f flags
        called_args = mock_run.call_args[0][0]
        joined = " ".join(called_args)
        self.assertIn("workflow", joined)
        self.assertIn("video-research.yml", joined)
        self.assertIn("mode=person_evidence_mining", called_args)
        self.assertIn("seed_url=https://www.instagram.com/reel/ABC123/",
                      called_args)
        self.assertIn("person_name=Test Person", called_args)
        self.assertIn("target_clip_count=6", called_args)
        self.assertIn("niche=brazil", called_args)

    @patch("person_evidence_dispatcher.subprocess.run")
    def test_dispatch_failure_logged(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="auth required")
        ok, msg = ped.dispatch_evidence_mining(
            seed_url="https://www.instagram.com/reel/X/",
            person_name="X", evidence_requirement="x" * 20,
            target_clip_count=6, niche="brazil",
        )
        self.assertFalse(ok)
        self.assertIn("auth", msg.lower())

    @patch("person_evidence_dispatcher.subprocess.run")
    @patch("person_evidence_dispatcher.infer_person_name",
           return_value=("Inferred Name", 0.85))
    def test_full_dispatch_from_capture_with_inference(self, _infer, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok", stderr="")
        result = ped.maybe_dispatch_from_capture(
            notes="find more clips of this person, same guy",
            seed_url="https://www.instagram.com/reel/SEED/",
            niche="brazil",
            caption="Inferred Name says...",
            transcript="long transcript with claims",
            creator_name="",
        )
        self.assertTrue(result["triggered"])
        self.assertTrue(result["dispatched"])
        self.assertEqual(result["person_name"], "Inferred Name")
        self.assertEqual(result["target_clip_count"], 6)

    @patch("person_evidence_dispatcher.subprocess.run")
    def test_no_trigger_short_circuits(self, mock_run):
        result = ped.maybe_dispatch_from_capture(
            notes="just a normal capture",
            seed_url="https://x.com/", niche="brazil",
        )
        self.assertFalse(result["triggered"])
        self.assertFalse(result["dispatched"])
        # Critically: subprocess.run was NEVER called
        self.assertFalse(mock_run.called)

    @patch("person_evidence_dispatcher.subprocess.run")
    @patch("person_evidence_dispatcher.infer_person_name",
           return_value=("", 0.0))
    def test_no_person_name_blocks_dispatch(self, _infer, mock_run):
        result = ped.maybe_dispatch_from_capture(
            notes="find more clips of this person",
            seed_url="https://x.com/", niche="brazil",
            caption="", transcript="", creator_name="",
        )
        self.assertTrue(result["triggered"])
        self.assertFalse(result["dispatched"])
        self.assertEqual(result["reason"], "no_person_name_could_be_inferred")
        self.assertFalse(mock_run.called)


class ScoringContractTests(unittest.TestCase):
    """Phase 1 safety contracts that must hold without paid API."""

    def test_invalid_claim_type_relabels_unsafe(self):
        from evidence_scoring import _coerce_to_schema
        out = _coerce_to_schema({
            "same_person": True, "person_confidence": 0.9,
            "same_person_method": "transcript", "requirement_match": True,
            "match_score": 0.8, "claim_type": "hate-speech",
            "best_quote": "x", "safe_to_use": True,
        })
        self.assertEqual(out["claim_type"], "needs-context")
        self.assertFalse(out["safe_to_use"])
        self.assertIn("invalid_claim_type_relabeled", out["context_needed"])
        self.assertEqual(out.get("_rejected_claim_type"), "hate-speech")

    def test_face_match_downgrades(self):
        from evidence_scoring import _coerce_to_schema
        out = _coerce_to_schema({
            "same_person": True, "person_confidence": 0.99,
            "same_person_method": "face_match", "requirement_match": True,
            "match_score": 0.9, "claim_type": "hypocrisy",
            "best_quote": "x", "safe_to_use": True,
        })
        self.assertEqual(out["same_person_method"], "metadata")
        self.assertLessEqual(out["person_confidence"], 0.6)
        self.assertIn("face_match_not_run_phase1", out["context_needed"])

    def test_slug_collision_safety(self):
        from evidence_scoring import slugify_bounded
        a = slugify_bounded("statements about religion that target women", 30)
        b = slugify_bounded("statements about religion that target men", 30)
        # Both inputs share the first 30 chars after slugify -> would collide
        # under the old `slugify(s)[:30]`. Hash suffix must keep them distinct.
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), 30)
        self.assertLessEqual(len(b), 30)


if __name__ == "__main__":
    unittest.main()
