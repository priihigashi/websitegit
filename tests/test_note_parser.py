"""
Cycle A0 — Golden tests for note_parser() against 5 real Capture Queue notes.

All 5 must pass. Test 5 (archive/defer) is the critical gate:
  build_now must be False, do_not_generate_content must be True.

Run: python3.12 -m pytest tests/test_note_parser.py -v
     (from repo root: ~/ClaudeWorkspace/oak-park-ai-hub/)

Uses use_llm=False for determinism. LLM path verified separately.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "content_creator"))

from note_parser import note_parser, _FEW_SHOT_EXAMPLES

_KB = {"use_llm": False}  # rule-based for deterministic golden tests


# ---------------------------------------------------------------------------
# Test 1 — Row 1, brazil, 2026-04-17
# Voice ramble / proof-seeking / motion carousel
# ---------------------------------------------------------------------------
def test_note1_voice_ramble_brazil():
    note = _FEW_SHOT_EXAMPLES[0]["note"]
    r = note_parser(note, project="brazil", **_KB)

    assert r["action"] == "build_now", f"Expected build_now, got {r['action']}"
    assert r["build_now"] is True
    assert r["output_type"] == "motion_carousel", f"Expected motion_carousel, got {r['output_type']}"
    assert r["research_required"] is True
    assert r["proof_required"] is True
    assert r["clip_required"] is True
    assert r["motion_required"] is True
    assert "journalistic" in r["tone"], f"tone={r['tone']}"
    assert "reel_plan" in r["secondary_outputs"], f"secondary_outputs={r['secondary_outputs']}"


# ---------------------------------------------------------------------------
# Test 2 — Row 2, brazil, 2026-04-17
# Typed research brief / both sides / research first
# ---------------------------------------------------------------------------
def test_note2_research_brief_brazil():
    note = _FEW_SHOT_EXAMPLES[1]["note"]
    r = note_parser(note, project="brazil", **_KB)

    assert r["action"] == "research_first", f"Expected research_first, got {r['action']}"
    assert r["build_now"] is False, f"CRITICAL: build_now must be False for research_first"
    assert r["transcription_required"] is True
    assert r["research_required"] is True
    assert "both_sides_required" in r["hard_requirements"], f"hard_requirements={r['hard_requirements']}"
    assert "posted_before_required_research" in r["reviewer_blockers"], f"blockers={r['reviewer_blockers']}"
    assert "missing_both_sides" in r["reviewer_blockers"]
    assert "avoid_forced_debunk_frame" in r["soft_preferences"], f"soft_preferences={r['soft_preferences']}"


# ---------------------------------------------------------------------------
# Test 3 — Row 6, usa, 2026-04-20
# Structured series spec / locked slide structure / motion version
# ---------------------------------------------------------------------------
def test_note3_series_spec_usa():
    note = _FEW_SHOT_EXAMPLES[2]["note"]
    r = note_parser(note, project="usa", **_KB)

    assert r["action"] == "create_series_plan", f"Expected create_series_plan, got {r['action']}"
    assert "locked_slide_structure" in r["intent_labels"], f"intent_labels={r['intent_labels']}"
    assert r["motion_required"] is True
    assert "declassified_evidence" in r["source_standard"], f"source_standard={r['source_standard']}"
    assert "chronological_order" in r["hard_requirements"], f"hard_requirements={r['hard_requirements']}"
    assert "disclaimer_required" in r["hard_requirements"]


# ---------------------------------------------------------------------------
# Test 4 — Row 10, no project detected
# One-liner topic extraction / routing uncertain / dual market
# ---------------------------------------------------------------------------
def test_note4_one_liner_topic_expansion():
    note = _FEW_SHOT_EXAMPLES[3]["note"]
    r = note_parser(note, project="", **_KB)

    assert r["action"] == "build_now", f"Expected build_now, got {r['action']}"
    assert "needs_topic_expansion" in r["intent_labels"], f"intent_labels={r['intent_labels']}"
    assert r["routing_confidence"] in ("low", "medium"), f"routing_confidence={r['routing_confidence']}"
    assert "usa" in r["market"], f"market={r['market']}"
    assert "brazil" in r["market"], f"market={r['market']}"
    assert r["research_required"] is True


# ---------------------------------------------------------------------------
# Test 5 — Row 27, stocks
# Archive/defer / transcribe for later / do not build now — CRITICAL TEST
# ---------------------------------------------------------------------------
def test_note5_archive_defer_stocks():
    note = _FEW_SHOT_EXAMPLES[4]["note"]
    r = note_parser(note, project="", **_KB)

    assert r["action"] in ("archive_defer", "transcribe_only"), \
        f"Expected archive_defer or transcribe_only, got {r['action']}"
    assert r["build_now"] is False, \
        f"CRITICAL: build_now MUST be False for archive/defer notes. Got: {r['build_now']}\nFull result: {r}"
    assert r["do_not_generate_content"] is True, \
        f"do_not_generate_content must be True. Got: {r['do_not_generate_content']}"
    assert r["transcription_required"] is True
    assert r["content_area"] == "stocks", f"content_area={r['content_area']}"
    assert r["future_use"] is True


# ---------------------------------------------------------------------------
# Sanity: empty note returns safe default (build_now=True, low confidence)
# ---------------------------------------------------------------------------
def test_empty_note_safe_default():
    r = note_parser("", project="", **_KB)
    assert r["build_now"] is True   # safe default — don't accidentally block
    assert r["routing_confidence"] == "low"
    assert r["action"] == "build_now"
