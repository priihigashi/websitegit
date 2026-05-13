"""
Track 1 — Smoke tests for the OCR suspicious-word gate in carousel_reviewer.py.
Tests: Item 2.4 — _check_short_word_artifacts() + _visible_html()

Run: python3.12 -m pytest tests/test_ocr_html_gate.py -v
     (from repo root: ~/ClaudeWorkspace/oak-park-ai-hub/)

Both functions are copied here verbatim from carousel_reviewer.py lines 81-115
so these tests have no dependency on google-auth / Pillow / other optional deps.
If the source ever changes, update the copies below to match.
Source commit: f61377a
"""
import re


# --- Copied verbatim from carousel_reviewer.py lines 81-115 ---

def _visible_html(html: str) -> str:
    """Drop non-visible blocks so CSS/JS tokens don't trip copy checks."""
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<svg[\s\S]*?</svg>", " ", html, flags=re.IGNORECASE)
    return html


_ALLOWED_SHORT_WORDS = {
    "HIRE", "NAIL", "BEAM", "SEAL", "TILE", "DECK", "BOLT", "FOAM",
    "ROOF", "WALL", "DOOR", "TRIM", "COST", "SAVE", "PRO", "DEMO",
    "SALE", "BEST", "FAST", "FREE", "SAFE", "TIPS", "CALL", "DONE",
}
_SUSPICIOUS_SHORT_WORDS = {"HIDE"}


def _check_short_word_artifacts(visible_html: str) -> list:
    issues = []
    plain = re.sub(r"<[^>]+>", " ", visible_html)
    found = set(re.findall(r'\b([A-Z]{3,6})\b', plain))
    suspicious = found & _SUSPICIOUS_SHORT_WORDS
    for word in sorted(suspicious):
        issues.append(
            f'[CONCERN][OCR] Suspicious short word in visible HTML: "{word}". '
            f'Known prior artifact: HIRE appeared as HIDE. Verify before approval.'
        )
    return issues


# ---------------------------------------------------------------------------
# Case 1 — HIDE in visible text → must flag [CONCERN][OCR]
# ---------------------------------------------------------------------------
def test_hide_in_visible_text_flags_concern():
    html = "<p>HIRE A CONTRACTOR TODAY</p><p>HIDE</p>"
    visible = _visible_html(html)
    issues = _check_short_word_artifacts(visible)
    assert len(issues) == 1, f"Expected 1 concern, got {issues}"
    assert "[CONCERN][OCR]" in issues[0], f"Expected [CONCERN][OCR] prefix, got: {issues[0]!r}"
    assert "HIDE" in issues[0], f"Expected HIDE in issue, got: {issues[0]!r}"


# ---------------------------------------------------------------------------
# Case 2 — HIRE in visible text → must NOT flag (allowlisted construction word)
# ---------------------------------------------------------------------------
def test_hire_in_visible_text_passes():
    html = "<p>HIRE A LICENSED CONTRACTOR</p>"
    visible = _visible_html(html)
    issues = _check_short_word_artifacts(visible)
    assert issues == [], f"HIRE should not flag. Got: {issues}"


# ---------------------------------------------------------------------------
# Case 3 — HIDE in CSS/JS only (not visible text) → must NOT flag
# _visible_html() strips style + script blocks before inspection.
# ---------------------------------------------------------------------------
def test_hide_in_css_only_does_not_flag():
    html = (
        "<style>.hidden { display:HIDE; } .HIDE { opacity: 0; }</style>"
        "<p>HIRE A CONTRACTOR</p>"
    )
    visible = _visible_html(html)
    issues = _check_short_word_artifacts(visible)
    assert issues == [], f"CSS-only HIDE must not flag. Got: {issues}"


# ---------------------------------------------------------------------------
# Case 4 — Construction allowlist words (NAIL, BEAM, SEAL, FOAM) → must NOT flag
# ---------------------------------------------------------------------------
def test_construction_words_pass():
    for word in ["NAIL", "BEAM", "SEAL", "FOAM", "ROOF", "TILE", "DECK"]:
        html = f"<p>{word} YOUR ROOF RIGHT</p>"
        visible = _visible_html(html)
        issues = _check_short_word_artifacts(visible)
        assert issues == [], f"{word} should be in allowlist and not flag. Got: {issues}"
