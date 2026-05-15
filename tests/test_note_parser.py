"""
Cycle A0 — Golden tests for note_parser() against 5 real Capture Queue notes.

All tests use use_llm=False for determinism. Haiku path verified separately.
Note texts are pasted directly from the Capture Queue — NOT imported from the
parser module, so tests remain independent of _FEW_SHOT_EXAMPLES changes.

Run: python3.12 -m pytest tests/test_note_parser.py -v
     (from repo root: ~/ClaudeWorkspace/oak-park-ai-hub/)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "content_creator"))

from note_parser import note_parser

_KB = {"use_llm": False}  # rule-based for deterministic golden tests

# ---------------------------------------------------------------------------
# Independent note fixtures — pasted from real Capture Queue rows, NOT from
# _FEW_SHOT_EXAMPLES. Tests must stay valid even if training examples change.
# ---------------------------------------------------------------------------

_NOTE_1_BRAZIL_VOICE_RAMBLE = (
    "we should verify this case look what she did try to find proof of what she did look her up "
    "she previews videos from her and then we definitely should start with this one and then we can "
    "have more information about what happened before this day and then that could be other slides and "
    "I definitely would like to put the clips it's gonna be a motion carousel and we could also plan "
    "for a video for reels, but I don't have comments about it, but would be similar. This is this is "
    "the hook you know like showing her and then we can say wait for that if it's a video I say wait "
    "for more information about the case watch this something like that while trying to find a way to "
    "keep it with a journalistic feel"
)

_NOTE_2_BRAZIL_RESEARCH_BRIEF = (
    "alexandre de moraes investigating flavio bolsonaro — show BOTH sides: his complaint + what the "
    "other side justifies. 5 YouTube videos + 5 IG reels transcribed. Research first, post after. "
    "no fake news debunk if any"
)

_NOTE_3_USA_SERIES_SPEC = (
    "SERIES IDEA: '10 Times the U.S. Intervened. This Is What Happened.' | "
    "NOT a complete list — only interventions with declassified evidence. | "
    "COVER: '10 Times the U.S. Intervened. This Is What Happened.' + small disclaimer: "
    "'Not a complete list — these are the ones the documents confirm.' | "
    "Each episode labeled: [Country] — [N] of 10 | "
    "EPISODE STRUCTURE (5 slides): 1) Hook card — country name MASSIVE + big image | "
    "2) What the U.S. Said at the Time — official quote | "
    "3) What the Documents Show — declassified record, sourced | "
    "4) Before & After — split: before intervention vs after"
)

_NOTE_4_ONE_LINER = (
    "Just create points from it and create a content about Iran and "
    "Hollywood propaganda. USA and Brazil"
)

_NOTE_5_ARCHIVE_DEFER = "Save and transcribe for stocks tips plans later"

# ---------------------------------------------------------------------------
# Regression fixtures — phrases that must NOT be classified as build_now
# Added after Brazil run 25772351907 proved rule-based allowed these through.
# ---------------------------------------------------------------------------

_NOTE_6_EVIDENCE_MINING = (
    "Brazil / evidence-mining route seed. Person: Frei Gilson. Use seed clip as hook, then find "
    "6-10 more same-person public clips; transcribe candidates; keep only matches where transcript "
    "evidence shows target behavior. Build carousel from evidence chain."
)

_NOTE_7_TRANSCRIBE_CANDIDATES = (
    "transcribe candidates from the 5 videos, verify before building. post after research is done."
)

_NOTE_8_FIND_PUBLIC_CLIPS = (
    "find public clips of this senator, cross-check claims with official records, then build."
)


# ---------------------------------------------------------------------------
# Test 1 — Row 1, brazil, 2026-04-17
# Voice ramble / proof-seeking / motion carousel
# ---------------------------------------------------------------------------
def test_note1_voice_ramble_brazil():
    r = note_parser(_NOTE_1_BRAZIL_VOICE_RAMBLE, project="brazil", **_KB)

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
    r = note_parser(_NOTE_2_BRAZIL_RESEARCH_BRIEF, project="brazil", **_KB)

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
    r = note_parser(_NOTE_3_USA_SERIES_SPEC, project="usa", **_KB)

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
    r = note_parser(_NOTE_4_ONE_LINER, project="", **_KB)

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
    r = note_parser(_NOTE_5_ARCHIVE_DEFER, project="", **_KB)

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
# Test 6 — Regression: evidence-mining note must NOT be build_now
# Proved by Brazil run 25772351907: rule-based allowed this through, pipeline
# built a post with unsourced placeholders and reviewer blocked it.
# ---------------------------------------------------------------------------
def test_note6_evidence_mining_is_research_first():
    r = note_parser(_NOTE_6_EVIDENCE_MINING, project="brazil", **_KB)

    assert r["action"] in ("research_first", "archive_defer"), \
        f"Evidence-mining note must not be build_now. Got action={r['action']}"
    assert r["build_now"] is False, \
        f"CRITICAL: evidence-mining note must hold build. Got build_now={r['build_now']}\nFull: {r}"


# ---------------------------------------------------------------------------
# Test 7 — Regression: "transcribe candidates / verify before / post after"
# ---------------------------------------------------------------------------
def test_note7_transcribe_verify_is_research_first():
    r = note_parser(_NOTE_7_TRANSCRIBE_CANDIDATES, project="brazil", **_KB)

    assert r["action"] == "research_first", \
        f"Expected research_first, got {r['action']}"
    assert r["build_now"] is False, \
        f"'transcribe candidates / post after' must hold build. Got: {r['build_now']}"


# ---------------------------------------------------------------------------
# Test 8 — Regression: "find public clips" phrase
# ---------------------------------------------------------------------------
def test_note8_find_public_clips_is_research_first():
    r = note_parser(_NOTE_8_FIND_PUBLIC_CLIPS, project="brazil", **_KB)

    assert r["build_now"] is False, \
        f"'find public clips' must hold build. Got build_now={r['build_now']}, action={r['action']}"


# ---------------------------------------------------------------------------
# Sanity: empty note returns safe default (build_now=True, low confidence)
# ---------------------------------------------------------------------------
def test_empty_note_safe_default():
    r = note_parser("", project="", **_KB)
    assert r["build_now"] is True   # safe default — don't accidentally block
    assert r["routing_confidence"] == "low"
    assert r["action"] == "build_now"


def test_note9_note_links_create_download_jobs():
    note = (
        "Use these two videos in the carousel. "
        "https://www.instagram.com/reel/ABC123/ cut the hook on slide 1. "
        "https://www.youtube.com/watch?v=xyz987 use main point on slide 4."
    )
    r = note_parser(note, project="brazil", **_KB)

    assert r["build_now"] is True
    assert r["clip_required"] is True
    assert "download_note_links" in r["intent_labels"]
    assert "download_note_links" in r["required_functions"]
    assert "write_clip_manifest" in r["required_functions"]
    assert len(r["note_urls"]) == 2
    jobs = [j for j in r["resource_requests"] if j["type"] == "download_note_link"]
    assert len(jobs) == 2
    assert all(j["target"] == "resources/clips" for j in jobs)
    assert any(j["slide_hint"] == "slide 1" for j in jobs)
    assert jobs[0]["target_slide"] == 1
    assert jobs[1]["target_slide"] == 4


def test_note_links_extract_per_url_slide_and_role():
    note = (
        "Sophia Barclay case. Use https://www.instagram.com/p/DW4tVaJkQAb/ "
        "as hook on slide 1. Use https://www.instagram.com/reel/C98BzR7SmNX/ "
        "as the apology video on slide 2."
    )
    r = note_parser(note, project="brazil", **_KB)
    jobs = [j for j in r["resource_requests"] if j["type"] == "download_note_link"]

    assert len(jobs) == 2
    assert jobs[0]["target_slide"] == 1
    assert jobs[0]["role"] == "hook"
    assert jobs[1]["target_slide"] == 2
    assert jobs[1]["role"] == "apology_video"


def test_note10_research_more_videos_creates_research_job():
    note = (
        "I don't have the links. Go research on this topic and bring videos "
        "about this senator, find public clips, then use the best proof."
    )
    r = note_parser(note, project="brazil", **_KB)

    assert r["action"] == "research_first"
    assert r["build_now"] is False
    assert r["research_required"] is True
    assert r["clip_required"] is True
    assert "video_research_needed" in r["intent_labels"]
    assert "video_research" in r["required_functions"]
    jobs = [j for j in r["resource_requests"] if j["type"] == "research_videos"]
    assert len(jobs) == 1
    assert jobs[0]["target"] == "Clip Collections"
    assert jobs[0]["downstream_target"] == "resources/clips"
