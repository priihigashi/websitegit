"""
Item 2.3 — Story arc retry gate tests.

Tests _extract_arc_headlines() helper and ARC_GATE_ENABLED default.

Coverage: local-only (smoke). No live API calls. Uses source parsing to avoid
importing main.py (which has heavy pipeline deps).

Run: python3.12 -m pytest tests/test_arc_gate.py -v
"""
import os, re


# ---------------------------------------------------------------------------
# Extract ARC_GATE_ENABLED and NOTE_PARSER_GATE_ENABLED defaults from source
# ---------------------------------------------------------------------------

def _read_main_source():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "content_creator", "main.py")
    with open(path) as f:
        return f.read()


def test_arc_gate_off_by_default():
    """ARC_GATE_ENABLED must default to '0' (OFF) in main.py source."""
    src = _read_main_source()
    m = re.search(r'ARC_GATE_ENABLED\s*=\s*os\.environ\.get\([^)]+\)(?:\.strip\(\))?\s*==\s*"1"', src)
    assert m, "ARC_GATE_ENABLED assignment not found in main.py"
    # Default must be '0'
    m2 = re.search(r'ARC_GATE_ENABLED\s*=\s*os\.environ\.get\("ARC_GATE_ENABLED",\s*"([^"]+)"\)', src)
    assert m2, "ARC_GATE_ENABLED default value not found"
    assert m2.group(1) == "0", (
        f"ARC_GATE_ENABLED default must be '0'. Found: {m2.group(1)!r}"
    )


def test_note_parser_gate_still_off_by_default():
    """NOTE_PARSER_GATE_ENABLED must still default to '0' — no regression."""
    src = _read_main_source()
    m = re.search(r'NOTE_PARSER_GATE_ENABLED\s*=\s*os\.environ\.get\("NOTE_PARSER_GATE_ENABLED",\s*"([^"]+)"\)', src)
    assert m, "NOTE_PARSER_GATE_ENABLED default not found in main.py"
    assert m.group(1) == "0", (
        f"NOTE_PARSER_GATE_ENABLED must default to '0'. Found: {m.group(1)!r}"
    )


# ---------------------------------------------------------------------------
# Test _extract_arc_headlines() logic — copied verbatim from main.py
# (same approach as test_ocr_html_gate.py for heavy-dep modules)
# Source: scripts/content_creator/main.py — _extract_arc_headlines function
# If the function changes, update the copy below to match.
# ---------------------------------------------------------------------------

def _extract_arc_headlines(content: dict) -> list:
    """Verbatim copy from main.py — see _extract_arc_headlines."""
    headlines = []
    for k, v in sorted(content.items()):
        if not isinstance(v, str):
            continue
        if k == "headline" or k.endswith("_headline"):
            clean = v.strip()
            if clean and clean not in headlines:
                headlines.append(clean)
    return headlines


def test_extract_arc_headlines_opc_content():
    """Extracts all *_headline and top-level 'headline' keys; skips non-headline fields."""
    content = {
        "headline": "5 Signs Your Contractor Is Cutting Corners",
        "slide2_headline": "No Written Contract? Walk Away",
        "slide2_stat": "73%",
        "slide3_headline": "Skipped Permits Cost You Later",
        "slide3_items": [{"title": "Permit A"}],
        "slide4_headline": "Ask for Proof of Insurance Before Day One",
        "slide5_headline": "Sources",
        "caption": "some caption",
        "tone": "journalistic",
    }
    headlines = _extract_arc_headlines(content)
    assert len(headlines) == 5, f"Expected 5 headlines, got {len(headlines)}: {headlines}"
    assert "5 Signs Your Contractor Is Cutting Corners" in headlines
    assert "No Written Contract? Walk Away" in headlines
    assert "Sources" in headlines
    assert "73%" not in headlines
    assert "some caption" not in headlines


def test_extract_arc_headlines_skips_non_strings():
    """None, empty strings, and non-string values are skipped."""
    content = {
        "headline": "Real Headline",
        "slide2_headline": None,
        "slide3_headline": "",
        "slide4_headline": "Good One",
        "_slide_plan": {"slides": []},
    }
    headlines = _extract_arc_headlines(content)
    assert headlines == ["Good One", "Real Headline"] or set(headlines) == {"Good One", "Real Headline"}, \
        f"Got: {headlines}"
    assert len(headlines) == 2


def test_extract_arc_headlines_deduplicates():
    """Duplicate headline strings appear only once."""
    content = {
        "headline": "Same Title",
        "slide2_headline": "Same Title",
        "slide3_headline": "Different",
    }
    headlines = _extract_arc_headlines(content)
    assert headlines.count("Same Title") == 1, f"Duplicates found: {headlines}"
    assert "Different" in headlines


def test_extract_arc_headlines_empty_content():
    """Empty content dict → empty list (no crash)."""
    assert _extract_arc_headlines({}) == []


def test_arc_gate_block_exists_in_source():
    """Verify the ARC_GATE_ENABLED guard block is present in process_one_topic."""
    src = _read_main_source()
    assert "if ARC_GATE_ENABLED and content:" in src, \
        "ARC_GATE_ENABLED guard block not found in main.py"
    assert "[ARC-GATE]" in src, \
        "[ARC-GATE] log line not found in main.py"
    assert "attempt=1 score=" in src, \
        "ARC-GATE log format not found"
