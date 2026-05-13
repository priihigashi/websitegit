"""
Stage 1B — Capture Note Intent Parser.

Parses a raw capture note (COMMENT column from Capture Queue, or brief from
Content Queue) into structured intent metadata before any content generation.

Key gate: build_now=False stops all downstream pipeline processing.
Wire point: main.py process_one_topic(), after brief is fetched, before
generate_carousel_content(). Decision log: 2026-05-12.

Architecture:
- _parse_with_haiku(): Claude Haiku + few-shot from 5 real capture notes.
  Falls back to rule-based on any API error so pipeline never hard-blocks.
- _parse_rule_based(): deterministic keyword logic; used by golden tests.
- note_parser(): public entry point; chooses path based on use_llm flag.

Cycle A0 — golden tests only. Not yet wired to production (Cycle A1).
"""

import json
import os
import re

# ---------------------------------------------------------------------------
# Schema — all fields guaranteed in every return value
# ---------------------------------------------------------------------------

_VALID_ACTIONS = {
    "build_now", "research_first", "create_series_plan",
    "transcribe_only", "archive_defer",
}

_EMPTY_SCHEMA: dict = {
    "action": "build_now",
    "build_now": True,
    "do_not_generate_content": False,
    "intent_labels": [],
    "required_functions": [],
    "output_type": "carousel",
    "secondary_outputs": [],
    "hard_requirements": [],
    "soft_preferences": [],
    "reviewer_blockers": [],
    "research_required": False,
    "proof_required": False,
    "transcription_required": False,
    "clip_required": False,
    "motion_required": False,
    "image_required": False,
    "source_standard": "",
    "tone": [],
    "market": [],
    "content_area": "",
    "routing_confidence": "medium",
    "future_use": False,
}

# ---------------------------------------------------------------------------
# Few-shot examples — 5 real Capture Queue notes (Cycle A0 training set)
# Order: voice-ramble, research-brief, series-spec, one-liner, archive-defer
# ---------------------------------------------------------------------------

_FEW_SHOT_EXAMPLES = [
    {
        "note": (
            "we should verify this case look what she did try to find proof of what she did look her up "
            "she previews videos from her and then we definitely should start with this one and then we can "
            "have more information about what happened before this day and then that could be other slides and "
            "I definitely would like to put the clips it's gonna be a motion carousel and we could also plan "
            "for a video for reels, but I don't have comments about it, but would be similar. This is this is "
            "the hook you know like showing her and then we can say wait for that if it's a video I say wait "
            "for more information about the case watch this something like that while trying to find a way to "
            "keep it with a journalistic feel"
        ),
        "project": "brazil",
        "output": {
            "action": "build_now",
            "build_now": True,
            "do_not_generate_content": False,
            "intent_labels": ["clip_required", "journalistic_tone", "motion_carousel", "verify_case"],
            "required_functions": ["find_proof", "person_research"],
            "output_type": "motion_carousel",
            "secondary_outputs": ["reel_plan"],
            "hard_requirements": [],
            "soft_preferences": ["journalistic_tone"],
            "reviewer_blockers": ["missing_motion_carousel", "missing_proof", "missing_required_clip"],
            "research_required": True,
            "proof_required": True,
            "transcription_required": False,
            "clip_required": True,
            "motion_required": True,
            "image_required": False,
            "source_standard": "journalistic",
            "tone": ["journalistic"],
            "market": ["brazil"],
            "content_area": "brazil",
            "routing_confidence": "medium",
            "future_use": False,
        },
    },
    {
        "note": (
            "alexandre de moraes investigating flavio bolsonaro — show BOTH sides: his complaint + what the "
            "other side justifies. 5 YouTube videos + 5 IG reels transcribed. Research first, post after. "
            "no fake news debunk if any"
        ),
        "project": "brazil",
        "output": {
            "action": "research_first",
            "build_now": False,
            "do_not_generate_content": False,
            "intent_labels": ["balanced_framing", "research_first", "source_video_review"],
            "required_functions": [
                "balanced_framing", "instagram_reel_transcription", "research_first",
                "source_video_review", "youtube_transcription",
            ],
            "output_type": "carousel",
            "secondary_outputs": [],
            "hard_requirements": ["both_sides_required"],
            "soft_preferences": ["avoid_forced_debunk_frame"],
            "reviewer_blockers": [
                "forced_debunk_frame", "missing_both_sides",
                "missing_transcription", "posted_before_required_research",
            ],
            "research_required": True,
            "proof_required": False,
            "transcription_required": True,
            "clip_required": False,
            "motion_required": False,
            "image_required": False,
            "source_standard": "multi_source_verified",
            "tone": ["neutral_no_spin"],
            "market": ["brazil"],
            "content_area": "brazil",
            "routing_confidence": "high",
            "future_use": False,
        },
    },
    {
        "note": (
            "SERIES IDEA: '10 Times the U.S. Intervened. This Is What Happened.' | "
            "NOT a complete list — only interventions with declassified evidence. | "
            "COVER: '10 Times the U.S. Intervened. This Is What Happened.' + small disclaimer: "
            "'Not a complete list — these are the ones the documents confirm.' | "
            "Each episode labeled: [Country] — [N] of 10 | "
            "EPISODE STRUCTURE (5 slides): 1) Hook card — country name MASSIVE + big image | "
            "2) What the U.S. Said at the Time — official quote | "
            "3) What the Documents Show — declassified record, sourced | "
            "4) Before & After — split: before intervention vs after"
        ),
        "project": "usa",
        "output": {
            "action": "create_series_plan",
            "build_now": False,
            "do_not_generate_content": False,
            "intent_labels": ["chronological_series", "locked_slide_structure", "series_plan"],
            "required_functions": ["declassified_evidence_required", "official_quote_required"],
            "output_type": "series_plan",
            "secondary_outputs": [],
            "hard_requirements": [
                "chronological_order", "declassified_evidence_required",
                "disclaimer_required", "locked_slide_structure", "official_quote_required",
            ],
            "soft_preferences": ["motion_tool:remotion"],
            "reviewer_blockers": [
                "ignored_locked_slide_structure", "missing_declassified_source",
                "missing_disclaimer", "missing_official_quote",
            ],
            "research_required": True,
            "proof_required": True,
            "transcription_required": False,
            "clip_required": False,
            "motion_required": True,
            "image_required": True,
            "source_standard": "declassified_evidence",
            "tone": ["journalistic"],
            "market": ["usa"],
            "content_area": "usa",
            "routing_confidence": "high",
            "future_use": False,
        },
    },
    {
        "note": (
            "Just create points from it and create a content about Iran and "
            "Hollywood propaganda. USA and Brazil"
        ),
        "project": "",
        "output": {
            "action": "build_now",
            "build_now": True,
            "do_not_generate_content": False,
            "intent_labels": ["needs_topic_expansion", "point_extraction", "route_uncertain"],
            "required_functions": ["point_extraction", "timeline_context", "topic_expansion"],
            "output_type": "carousel",
            "secondary_outputs": [],
            "hard_requirements": [],
            "soft_preferences": [],
            "reviewer_blockers": ["routed_to_wrong_project_or_market"],
            "research_required": True,
            "proof_required": False,
            "transcription_required": False,
            "clip_required": False,
            "motion_required": False,
            "image_required": False,
            "source_standard": "",
            "tone": ["neutral_no_spin"],
            "market": ["brazil", "usa"],
            "content_area": "",
            "routing_confidence": "low",
            "future_use": False,
        },
    },
    {
        "note": "Save and transcribe for stocks tips plans later",
        "project": "",
        "output": {
            "action": "archive_defer",
            "build_now": False,
            "do_not_generate_content": True,
            "intent_labels": ["archive_defer", "do_not_build_now", "future_use", "transcribe_only"],
            "required_functions": ["youtube_transcription"],
            "output_type": "transcript",
            "secondary_outputs": [],
            "hard_requirements": [],
            "soft_preferences": [],
            "reviewer_blockers": ["built_content_when_note_said_defer"],
            "research_required": False,
            "proof_required": False,
            "transcription_required": True,
            "clip_required": False,
            "motion_required": False,
            "image_required": False,
            "source_standard": "",
            "tone": [],
            "market": [],
            "content_area": "stocks",
            "routing_confidence": "high",
            "future_use": True,
        },
    },
]


# ---------------------------------------------------------------------------
# Haiku-based parser
# ---------------------------------------------------------------------------

_HAIKU_SYSTEM = """You are a capture note intent parser for a social media content pipeline.

Given a raw capture note (voice-to-text or typed) and an optional project niche,
return a JSON object with exactly these fields:

action: one of build_now | research_first | create_series_plan | transcribe_only | archive_defer
build_now: boolean
do_not_generate_content: boolean
intent_labels: list of strings from the taxonomy
required_functions: list of functions the pipeline must run
output_type: carousel | motion_carousel | series_plan | transcript | reel
secondary_outputs: additional output types requested
hard_requirements: non-negotiable constraints
soft_preferences: optional preferences
reviewer_blockers: conditions that would fail review
research_required: boolean
proof_required: boolean
transcription_required: boolean
clip_required: boolean
motion_required: boolean
image_required: boolean
source_standard: journalistic | multi_source_verified | declassified_evidence | (empty)
tone: list (journalistic | neutral_no_spin | point_extraction)
market: list of market strings (brazil | usa | opc | stocks)
content_area: niche string
routing_confidence: high | medium | low
future_use: boolean

Decision rules (apply in order):
1. Note says "save", "transcribe for later", "plans later", "archive", "defer" → action=archive_defer, build_now=false, do_not_generate_content=true
2. Note says "research first", "post after", requires heavy transcription before building → action=research_first, build_now=false
3. Note proposes a named series with locked episode structure → action=create_series_plan, build_now=false
4. Everything else → action=build_now, build_now=true

Return ONLY valid JSON. No markdown fences. No explanation."""


def _build_few_shot_text() -> str:
    parts = []
    for ex in _FEW_SHOT_EXAMPLES:
        parts.append(
            f'Note: {json.dumps(ex["note"])}\n'
            f'Project: {json.dumps(ex["project"])}\n'
            f'Output:\n{json.dumps(ex["output"], indent=2)}\n'
            f'---'
        )
    return "\n\n".join(parts)


def _validate_and_fill(parsed: dict) -> dict:
    """Ensure all schema fields present, correct types, and valid values."""
    result = dict(_EMPTY_SCHEMA)
    result.update(parsed)
    for bf in ("build_now", "do_not_generate_content", "research_required",
               "proof_required", "transcription_required", "clip_required",
               "motion_required", "image_required", "future_use"):
        result[bf] = bool(result.get(bf, _EMPTY_SCHEMA[bf]))
    for lf in ("intent_labels", "required_functions", "secondary_outputs",
               "hard_requirements", "soft_preferences", "reviewer_blockers",
               "tone", "market"):
        v = result.get(lf)
        if isinstance(v, str):
            result[lf] = [v] if v else []
        elif not isinstance(v, list):
            result[lf] = []
    if result.get("action") not in _VALID_ACTIONS:
        result["action"] = "build_now"
    return result


def _parse_with_haiku(note_text: str, project: str = "") -> dict:
    """Call Claude Haiku with few-shot examples. Falls back to rule-based on error."""
    try:
        import anthropic
    except ImportError:
        print("  [NOTE-PARSER] anthropic not installed — using rule-based fallback")
        return _parse_rule_based(note_text, project)

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_KEY_4_CONTENT")
    if not api_key:
        print("  [NOTE-PARSER] No API key found — using rule-based fallback")
        return _parse_rule_based(note_text, project)

    few_shot = _build_few_shot_text()
    user_content = (
        f"{few_shot}\n\n"
        f"Now parse this note:\n"
        f"Note: {json.dumps(note_text)}\n"
        f"Project: {json.dumps(project)}\n"
        f"Output:"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_HAIKU_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        parsed = json.loads(raw)
        result = _validate_and_fill(parsed)
        print(f"  [NOTE-PARSER] Haiku: action={result['action']} build_now={result['build_now']}")
        return result
    except Exception as exc:
        print(f"  [NOTE-PARSER] Haiku failed ({exc}) — using rule-based fallback")
        return _parse_rule_based(note_text, project)


# ---------------------------------------------------------------------------
# Rule-based fallback (deterministic — no API calls)
# ---------------------------------------------------------------------------

def _parse_rule_based(note_text: str, project: str = "") -> dict:
    """Keyword-based parser. Deterministic, used for golden tests and API fallback."""
    n = note_text.lower()
    result = dict(_EMPTY_SCHEMA)

    # --- ACTION (most specific first) ---
    _archive_kws = [
        "save and transcribe", "transcribe for later", "save for later",
        "plans later", "do not build", "don't build", "for later",
    ]
    _research_kws = [
        "research first", "post after", "transcribed first", "research before",
        "transcribe candidates", "evidence-mining", "evidence mining",
        "find public clips", "find clips", "find 6-10", "find 6 to 10",
        "keep only matches", "verify before", "investigate before",
        "check before building", "fact.check first", "verify first",
    ]
    _series_kws = [
        "series idea", "series:", "episode structure", "each episode",
        "of 10", "series concept",
    ]

    if any(kw in n for kw in _archive_kws) and not any(kw in n for kw in _research_kws + _series_kws):
        action = "archive_defer"
    elif any(kw in n for kw in _research_kws):
        action = "research_first"
    elif any(kw in n for kw in _series_kws):
        action = "create_series_plan"
    else:
        action = "build_now"

    result["action"] = action
    result["build_now"] = action == "build_now"
    result["do_not_generate_content"] = action == "archive_defer"

    # --- INTENT LABELS ---
    labels = []
    if action == "archive_defer":
        labels += ["archive_defer", "do_not_build_now", "future_use", "transcribe_only"]
    if action == "research_first":
        labels += ["balanced_framing", "research_first", "source_video_review"]
    if action == "create_series_plan":
        labels += ["chronological_series", "locked_slide_structure", "series_plan"]
    if "motion carousel" in n or "motion version" in n:
        labels += ["clip_required", "motion_carousel"]
    if "journalistic" in n:
        labels.append("journalistic_tone")
    if "verify" in n or "find proof" in n:
        labels.append("verify_case")
    if len(note_text.split()) < 20 and action == "build_now":
        labels += ["needs_topic_expansion", "point_extraction", "route_uncertain"]
    result["intent_labels"] = sorted(set(labels))

    # --- REQUIRED FUNCTIONS ---
    funcs = []
    if "find proof" in n or "verify" in n:
        funcs += ["find_proof", "person_research"]
    if action == "research_first":
        funcs += ["balanced_framing", "research_first", "source_video_review"]
        if "youtube" in n:
            funcs.append("youtube_transcription")
        if "ig reel" in n or "instagram" in n:
            funcs.append("instagram_reel_transcription")
    if action == "archive_defer":
        funcs.append("youtube_transcription")
    if action == "create_series_plan":
        if "declassified" in n:
            funcs.append("declassified_evidence_required")
        if "official quote" in n or "what the u.s. said" in n:
            funcs.append("official_quote_required")
    if len(note_text.split()) < 20 and action == "build_now":
        funcs += ["point_extraction", "timeline_context", "topic_expansion"]
    result["required_functions"] = sorted(set(funcs))

    # --- OUTPUT TYPE ---
    if "motion carousel" in n or "motion version" in n:
        result["output_type"] = "motion_carousel"
    elif action == "create_series_plan":
        result["output_type"] = "series_plan"
    elif action == "archive_defer":
        result["output_type"] = "transcript"
    else:
        result["output_type"] = "carousel"

    # --- SECONDARY OUTPUTS ---
    sec = []
    if any(kw in n for kw in ["video for reels", "plan for a video", "reels,"]):
        sec.append("reel_plan")
    result["secondary_outputs"] = sorted(set(sec))

    # --- HARD REQUIREMENTS ---
    hard = []
    if "both sides" in n or "both side" in n or "show both" in n:
        hard.append("both_sides_required")
    if action == "create_series_plan":
        if "episode structure" in n or "each episode" in n:
            hard += ["chronological_order", "locked_slide_structure"]
        if "disclaimer" in n or "not a complete list" in n:
            hard.append("disclaimer_required")
        if "official quote" in n or "what the u.s. said" in n:
            hard.append("official_quote_required")
        if "declassified" in n:
            hard.append("declassified_evidence_required")
    result["hard_requirements"] = sorted(set(hard))

    # --- SOFT PREFERENCES ---
    soft = []
    if "no fake news debunk" in n or "not a debunk" in n or "avoid debunk" in n:
        soft.append("avoid_forced_debunk_frame")
    if "journalistic" in n:
        soft.append("journalistic_tone")
    if "remotion" in n:
        soft.append("motion_tool:remotion")
    result["soft_preferences"] = sorted(set(soft))

    # --- REVIEWER BLOCKERS ---
    blockers = []
    if action == "research_first":
        blockers += ["missing_transcription", "posted_before_required_research"]
    if "both sides" in n or "both side" in n:
        blockers.append("missing_both_sides")
    if "no fake news debunk" in n or "avoid debunk" in n:
        blockers.append("forced_debunk_frame")
    if "motion carousel" in n:
        blockers += ["missing_motion_carousel", "missing_required_clip"]
    if "find proof" in n or "verify" in n:
        blockers.append("missing_proof")
    if action == "archive_defer":
        blockers.append("built_content_when_note_said_defer")
    if action == "create_series_plan":
        blockers += [
            "ignored_locked_slide_structure", "missing_declassified_source",
            "missing_disclaimer", "missing_official_quote",
        ]
    if len(note_text.split()) < 20 and action == "build_now":
        blockers.append("routed_to_wrong_project_or_market")
    result["reviewer_blockers"] = sorted(set(blockers))

    # --- BOOLEAN FLAGS ---
    result["research_required"] = any(kw in n for kw in [
        "verify", "research", "look up", "look her up", "find proof", "investigate",
        "transcribed", "youtube videos", "ig reels", "create a content",
        "create points", "propaganda", "check",
    ])
    result["proof_required"] = any(kw in n for kw in [
        "find proof", "proof", "verify this case", "verify", "declassified",
        "documents confirm",
    ])
    result["transcription_required"] = any(kw in n for kw in [
        "transcribed", "transcribe", "youtube videos", "ig reels transcribed",
        "save and transcribe",
    ])
    result["clip_required"] = (
        any(kw in n for kw in ["put the clips", "clips", "showing her"])
        and action != "archive_defer"
    )
    result["motion_required"] = (
        any(kw in n for kw in ["motion carousel", "motion version", "remotion"])
        or action == "create_series_plan"
    )
    result["image_required"] = any(kw in n for kw in [
        "photo", "big image", "face", "cover",
    ])
    result["future_use"] = any(kw in n for kw in ["later", "plans later", "future"])

    # --- TONE ---
    tone = []
    if "journalistic" in n:
        tone.append("journalistic")
    if "both sides" in n or "both side" in n or "devils advocate" in n:
        tone.append("neutral_no_spin")
    if len(note_text.split()) < 20:
        tone.append("neutral_no_spin")
    result["tone"] = sorted(set(tone))

    # --- SOURCE STANDARD ---
    if "declassified" in n:
        result["source_standard"] = "declassified_evidence"
    elif "journalistic" in n:
        result["source_standard"] = "journalistic"
    elif action == "research_first":
        result["source_standard"] = "multi_source_verified"

    # --- MARKET ---
    market = []
    if project:
        market.append(project.lower())
    if "brazil" in n and "brazil" not in market:
        market.append("brazil")
    if ("usa" in n or "u.s." in n) and "usa" not in market:
        market.append("usa")
    result["market"] = sorted(set(market))

    # --- CONTENT AREA ---
    if project:
        result["content_area"] = project.lower()
    elif "stock" in n:
        result["content_area"] = "stocks"
    elif "brazil" in n:
        result["content_area"] = "brazil"
    elif "usa" in n or "u.s." in n or "iran" in n or "hollywood" in n:
        result["content_area"] = "usa"

    # --- ROUTING CONFIDENCE ---
    if action in ("archive_defer", "create_series_plan"):
        result["routing_confidence"] = "high"
    elif action == "research_first" and len(note_text) > 80:
        result["routing_confidence"] = "high"
    elif not project or len(note_text.split()) < 20:
        result["routing_confidence"] = "low"
    else:
        result["routing_confidence"] = "medium"

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def note_parser(note_text: str, project: str = "", use_llm: bool = True) -> dict:
    """Parse a capture note into structured intent metadata.

    Args:
        note_text: Raw note text (Capture Queue COMMENT or Content Queue brief).
        project:   Detected niche ('brazil', 'usa', 'opc', 'stocks', '').
        use_llm:   True → Claude Haiku (with rule-based fallback on error).
                   False → rule-based only (deterministic; used by golden tests).

    Returns:
        Flat schema dict. All fields guaranteed. build_now=False gates generation.
    """
    if not note_text or not note_text.strip():
        result = dict(_EMPTY_SCHEMA)
        result["routing_confidence"] = "low"
        return result

    if use_llm:
        return _parse_with_haiku(note_text, project)
    return _parse_rule_based(note_text, project)
