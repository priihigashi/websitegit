#!/usr/bin/env python3
"""
carousel_reviewer.py — Post-build quality check for carousel output.
Runs automatically after content_creator.yml finishes building carousels.

Checks each built post for:
  1. Photo presence: sticker-slot has a real image (not "@..._STICKER" placeholder text)
  2. Context-image slots: "[ IMG: ... ]" placeholders not present in rendered HTML
  3. Slide count: at least 5 slides built (OPC) or 4 slides (Brazil/USA)
  4. PNG size sanity: every PNG > 10KB (blank-slide detection)
  5. Motion folder: at least 1 MP4 present

Reports via email if any check fails. Exits with code 0 always (non-blocking).

Usage:
  python carousel_reviewer.py   ← reads CONTENT_CREATOR_RUN env var (JSON list of results)
  python carousel_reviewer.py --dry-run  ← print checks without emailing
"""

import base64, io, json, os, re, subprocess, sys
from pathlib import Path
from datetime import datetime
import urllib.request, urllib.parse

ANTHROPIC_KEY = (
    os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    or os.environ.get("ANTHROPIC_API_KEY", "")
)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from image_library import search_library
try:
    from PIL import Image, ImageStat  # type: ignore
except Exception:
    Image = None
    ImageStat = None

# Env vars
SHEETS_TOKEN     = os.environ.get("SHEETS_TOKEN", "")
ALERT_EMAIL      = os.environ.get("ALERT_EMAIL", "priscila@oakpark-construction.com")
RUN_RESULTS_JSON = os.environ.get("CONTENT_CREATOR_RUN", "[]")  # JSON array of result dicts
REVIEW_DRIVE_FOLDERS = os.environ.get("REVIEW_DRIVE_FOLDERS", "").strip()  # CSV folder ids or links
REVIEW_STRICT = os.environ.get("REVIEW_STRICT", "").strip().lower() in {"1", "true", "yes"}

# FIX_MODE: "analyze_only" (default) = detect + email
#          "analyze_and_fix"        = detect, auto-fix [fix_type=regenerate] issues,
#                                     then email before/after change log
FIX_MODE = os.environ.get("FIX_MODE", "analyze_only").strip().lower()
SLIDE_PURPOSE_PILOT = os.environ.get("SLIDE_PURPOSE_PILOT", "0").strip() in {"1", "true", "yes"}

DRY_RUN = "--dry-run" in sys.argv

STOPWORDS = {
    "the","and","for","with","this","that","from","into","your","you","are","was","have",
    "how","what","when","where","why","a","an","of","to","in","on","by","or","at","as",
    "oak","park","construction","tip","week","pro","move","list","real","number","save",
    "more","less","using","use","build","project","process","action","guide"
}
GENERIC_IMAGE_QUERY_TOKENS = {
    "construction","home","house","building","project","work","process","outdoor","indoor",
    "renovation","contractor","garden","kitchen","bathroom","tools","site","job"
}


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {p for p in parts if p not in STOPWORDS}


def _entity_in_text(entity: str, text: str) -> bool:
    tokens = [
        t for t in re.findall(r"[a-z0-9]{3,}", (entity or "").lower())
        if t not in STOPWORDS and t not in {"the", "and", "with"}
    ]
    hay = (text or "").lower()
    return any(re.search(rf"\b{re.escape(t)}s?\b", hay) for t in tokens)


# ─── Checks ──────────────────────────────────────────────────────────────────

def _visible_html(html: str) -> str:
    """Drop non-visible blocks so CSS/JS tokens don't trip copy checks."""
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<svg[\s\S]*?</svg>", " ", html, flags=re.IGNORECASE)
    return html


# Short uppercase words known to be valid in construction/service copy — must not be flagged.
_ALLOWED_SHORT_WORDS = {
    "HIRE", "NAIL", "BEAM", "SEAL", "TILE", "DECK", "BOLT", "FOAM",
    "ROOF", "WALL", "DOOR", "TRIM", "COST", "SAVE", "PRO", "DEMO",
    "SALE", "BEST", "FAST", "FREE", "SAFE", "TIPS", "CALL", "DONE",
}
# Words that should not appear in construction/service headline copy — documented prior artifact.
_SUSPICIOUS_SHORT_WORDS = {"HIDE"}


def _check_short_word_artifacts(visible_html: str) -> list[str]:
    """Flag suspicious short uppercase words in visible HTML that may be substitution artifacts.

    Documented prior case: cream variant showed HIDE where HIRE was intended.
    This check catches the case where corruption occurred before/during HTML generation.
    PNG-only distortions are outside this gate's scope.
    """
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


def _first_variant_html(html: str) -> str:
    """Review only one variant from HTML files that contain v2 + v3 slides."""
    marker = "<!-- V3 -->"
    return html.split(marker, 1)[0] if marker in html else html


def _slide_blocks_for_review(html: str, limit=None) -> list[str]:
    visible = _visible_html(_first_variant_html(html))
    blocks = re.findall(
        r'<div class="slide(?:\s[^"]*)?"[^>]*>([\s\S]*?)(?=<div class="slide(?:\s|")|\Z)',
        visible,
    )
    return blocks[:limit] if limit else blocks


def _plain_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", text).strip()


def _extract_class_text(block: str, class_tokens: tuple[str, ...]) -> str:
    token_alt = "|".join(re.escape(t) for t in class_tokens)
    m = re.search(
        rf'class="[^"]*(?:{token_alt})[^"]*"[^>]*>([\s\S]*?)</(?:div|p|h[1-6])',
        block,
        flags=re.IGNORECASE,
    )
    return _plain_text(m.group(1)) if m else ""


def check_html_placeholders(html_path: str) -> list[str]:
    """Return list of issue strings found in the HTML file."""
    issues = []
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return [f"Cannot read HTML: {e}"]

    # Placeholder sticker text — pattern: @WORD_STICKER
    placeholder_matches = re.findall(r'@\w+_STICKER', html)
    if placeholder_matches:
        issues.append(
            f"PLACEHOLDER sticker(s) found — real photo NOT embedded: {', '.join(set(placeholder_matches))}"
        )

    # Context-image slot still has query text (not replaced with real image)
    ctx_matches = re.findall(r'\[ IMG: ([^\]]{3,60}) \]', html)
    if ctx_matches:
        issues.append(
            f"CONTEXT-IMAGE slot(s) still have placeholder text — image not sourced: "
            + "; ".join(ctx_matches[:3])
        )

    visible_html = _visible_html(html)

    # Short-word artifact gate — catches documented HIRE→HIDE class of substitution.
    issues.extend(_check_short_word_artifacts(visible_html))

    # Label-leak: structural labels that Haiku sometimes emits verbatim into slide copy.
    # Inspect visible markup only; CSS declarations like "--c-body:" are not copy.
    _LABEL_PATTERNS = [
        r'\bSlide\s+\d+\s*[:\-]',          # "Slide 1:", "Slide 2 -"
        r'\b(?:Hook|CTA|Body|Title|Intro|Outro|Headline|Subhead|Caption)\s*:',  # field names
        r'\[INSERT\b', r'\[ADD\b', r'\[REPLACE\b', r'\[PUT\b',  # imperative placeholders
        r'\[YOUR\s+\w', r'\[WRITE\b',       # authoring reminders
        r'\bNUM_\w+\b', r'\bDATE_\w+\b',   # token stubs
    ]
    label_hits = []
    for pat in _LABEL_PATTERNS:
        matches = re.findall(pat, visible_html, re.IGNORECASE)
        if matches:
            label_hits.extend(set(m.strip() for m in matches[:3]))
    if label_hits:
        issues.append(
            "Label-leak: structural labels visible in rendered HTML — "
            "Haiku returned field names verbatim: " + ", ".join(f"'{h}'" for h in label_hits[:6])
        )

    # @HANDLE_PLACEHOLDER in rendered HTML — source_handle was never resolved
    if "@HANDLE_PLACEHOLDER" in html:
        issues.append(
            "Brazil handle not resolved — '@HANDLE_PLACEHOLDER' visible in HTML; "
            "check source_handle field in content JSON (generate_dados_content retry logic)"
        )
    generic_placeholders = sorted(set(re.findall(
        r"(?:@[A-Z0-9_]*PLACEHOLDER|[A-Z0-9_]+_PLACEHOLDER|TODO_[A-Z0-9_]+|LOREM_IPSUM)",
        html
    )))
    if generic_placeholders:
        issues.append(
            "Placeholder token(s) visible in HTML: " + ", ".join(generic_placeholders[:8])
        )

    # Generic safe-margin check for swipe indicators across all templates.
    if re.search(r"(?<!S)WIPE\s*(?:→|&#8594;)", html):
        issues.append("Swipe label typo/clipping artifact detected ('WIPE →').")
    for m in re.finditer(r"\.swipe[^{]*\{([\s\S]*?)\}", html):
        block = m.group(1)
        m_right = re.search(r"right\s*:\s*(?:var\(--P\)|([0-9]+)px)", block)
        m_bottom = re.search(r"bottom\s*:\s*(?:var\(--P\)|([0-9]+)px)", block)
        if m_right and m_right.group(1) and int(m_right.group(1)) < 48:
            issues.append(f"Swipe indicator too close to right edge ({m_right.group(1)}px).")
        if m_bottom and m_bottom.group(1) and int(m_bottom.group(1)) < 36:
            issues.append(f"Swipe indicator too close to bottom edge ({m_bottom.group(1)}px).")

    # Generic hook/overflow checks for generated covers and stat slides.
    cover_text = ""
    m_cover = re.search(r'<div class="slide[^"]*(?:cover|s1)[^"]*"[^>]*>([\s\S]*?)(?:<div class="slide|\Z)', html)
    if m_cover:
        cover_text = re.sub(r"<[^>]+>", " ", m_cover.group(1))
        cover_text = re.sub(r"\s+", " ", cover_text).strip()
        has_hook_signal = (
            "?" in cover_text
            or any(c.isdigit() for c in cover_text)
            or "$" in cover_text
            or "%" in cover_text
            or any(w in cover_text.lower() for w in (
                "why", "how", "who", "what", "decidiu", "quem", "por que", "verdade",
                "claim", "fact", "warning", "red flag", "mistake", "risk", "dados", "agenda"
            ))
        )
        if cover_text and not has_hook_signal:
            issues.append("Cover hook weak: no question, number, claim/fact cue, or urgency word detected.")
    # Stat slide guard: length alone is a weak proxy, but long stat copy with the
    # old oversized CSS can crowd into the footer/legal lane.
    for idx, stat_txt in enumerate(re.findall(r'<[^>]+class="[^"]*stat-big[^"]*"[^>]*>([\s\S]*?)</[^>]+>', html), start=1):
        clean = re.sub(r"<[^>]+>", "", stat_txt).strip()
        if len(clean) >= 22:
            issues.append(f"Stat clipping risk: stat-big {idx} is {len(clean)} chars ('{clean[:24]}').")
    if "slide-stat" in html:
        m_stat_css = re.search(r"\.stat-big\s*\{([\s\S]*?)\}", html)
        stat_css = m_stat_css.group(1) if m_stat_css else ""
        m_max = re.search(r"clamp\([^,]+,[^,]+,\s*([0-9]+)px\)", stat_css)
        m_top = re.search(r"margin-top\s*:\s*([0-9]+)px", stat_css)
        max_px = int(m_max.group(1)) if m_max else 0
        top_px = int(m_top.group(1)) if m_top else 0
        if max_px > 280 or top_px > 190:
            issues.append(
                f"Stat layout risk: .stat-big max/top is {max_px}px/{top_px}px; "
                "3-line stats can collide with the footer/legal lane."
            )
    for cls, limit in (
        ("headline", 70),
        ("body-text", 180),
        ("stat-body", 170),
        ("prog-caption", 95),
    ):
        for idx, raw in enumerate(re.findall(rf'<[^>]+class="[^"]*{cls}[^"]*"[^>]*>([\s\S]*?)</[^>]+>', html), start=1):
            clean = re.sub(r"<[^>]+>", "", raw).strip()
            if len(clean) > limit:
                issues.append(f"Text overflow risk: .{cls} #{idx} is {len(clean)} chars (limit {limit}).")

    # OPC-specific quality checks for the legacy tip template. Smart slide-plan
    # carousels mix standalone templates and won't always contain legacy
    # .context-img-slot or .project-note blocks.
    is_smart_opc = (
        "OPC — Smart Plan" in html
        or re.search(r'class="slide opc-(?:mp|fcg|is|st|bs|pm|dt)\b', html)
    )
    if "Tip of the Week · Oak Park Construction" in html and not is_smart_opc:
        # Match the class as a token inside any class attribute. The builder emits
        # both `class="context-img-slot"` and multi-class forms like
        # `class="context-img-slot context-img-placeholder"`. The old literal regex
        # missed the multi-class form and returned slot_count=0 (run 25498995171).
        slot_count = len(re.findall(r'class="[^"]*\bcontext-img-slot\b[^"]*"', html))
        # Lowered from 3 → 2: visual rhythm rule below already enforces image
        # density on body slides; structural slot count is a soft floor.
        if slot_count < 2:
            issues.append(
                f"OPC layout issue: expected >=2 context image slots on slides 2-4, found {slot_count}"
            )

        img_count = len(re.findall(r'<div class="[^"]*\bcontext-img-slot\b[^"]*"[^>]*>\s*<img ', html))
        if img_count < 2:
            issues.append(
                f"OPC visual floor miss: only {img_count} context slot(s) have real images; require >=2"
            )
        # If there are more than 3 body slides (future longer topics), keep visual rhythm:
        # at least every other body slide should have a real image.
        if slot_count > 3:
            min_by_rhythm = (slot_count + 1) // 2
            if img_count < min_by_rhythm:
                issues.append(
                    f"OPC rhythm miss: {img_count}/{slot_count} body visuals have real images; require >= {min_by_rhythm}"
                )

        fallback_count = len(re.findall(r'class="ctx-fallback"', html))
        if fallback_count > 1:
            issues.append(
                f"OPC fallback overuse: {fallback_count} context slots still fallback text (max 1)"
            )

        if "class=\"project-note\"" not in html:
            issues.append(
                "OPC explanation missing: project-note block not found on stat slide"
            )
        # Cover subhead length guardrail (creator enforces <=110 chars).
        m_sub = re.search(r'<div class="body-text">([\s\S]*?)</div>', html)
        if m_sub:
            sub_txt = re.sub(r"<[^>]+>", "", m_sub.group(1)).strip()
            if len(sub_txt) > 110:
                issues.append(
                    f"OPC cover subhead too long ({len(sub_txt)} chars) — max 110 to avoid HUD overlap."
                )
            # REV-03: Hook strength — subhead must contain a number, $, %, or urgency word.
            _HOOK_URGENCY = {
                "save", "stop", "never", "always", "warning", "mistake", "wrong", "truth",
                "secret", "hidden", "real", "actually", "biggest", "worst", "must", "avoid",
                "danger", "risk", "fail", "don't", "shouldn't", "every", "most", "red flag",
            }
            sub_lower = sub_txt.lower()
            has_hook = (
                any(c.isdigit() for c in sub_txt)
                or "$" in sub_txt
                or "%" in sub_txt
                or any(w in sub_lower for w in _HOOK_URGENCY)
            )
            if not has_hook:
                issues.append(
                    "OPC hook miss: cover subtitle has no number, $, %, or urgency word — too weak to stop scroll."
                )
        # Swipe text integrity + no clipping-prone typo patterns.
        if re.search(r"(?<!S)WIPE\s*→", html):
            issues.append("OPC swipe label typo/clipping artifact detected ('WIPE →').")
        swipe_count = html.count("SWIPE →") + html.count("SWIPE &#8594;")
        if swipe_count < 4:
            issues.append(f"OPC swipe indicator missing on expected slides (found {swipe_count}, expected >=4).")
        # Ensure cover HUD lane classes are present.
        if ".slide-cover .arrow" not in html or ".slide-cover .slide-logo" not in html:
            issues.append("OPC cover HUD lane styles missing (.slide-cover .arrow / .slide-cover .slide-logo).")
        # Safe-margin check for cover HUD: avoid edge clipping.
        m_arrow = re.search(r"\.slide-cover\s+\.arrow\s*\{([\s\S]*?)\}", html)
        if m_arrow:
            m_right = re.search(r"right\s*:\s*(\d+)px", m_arrow.group(1))
            m_bottom = re.search(r"bottom\s*:\s*(\d+)px", m_arrow.group(1))
            if m_right and int(m_right.group(1)) < 56:
                issues.append(f"OPC cover swipe too close to right edge ({m_right.group(1)}px).")
            if m_bottom and int(m_bottom.group(1)) < 40:
                issues.append(f"OPC cover swipe too close to bottom edge ({m_bottom.group(1)}px).")
        m_logo = re.search(r"\.slide-cover\s+\.slide-logo\s*\{([\s\S]*?)\}", html)
        if m_logo:
            m_left = re.search(r"left\s*:\s*(\d+)px", m_logo.group(1))
            m_bottom = re.search(r"bottom\s*:\s*(\d+)px", m_logo.group(1))
            if m_left and int(m_left.group(1)) < 56:
                issues.append(f"OPC cover license too close to left edge ({m_left.group(1)}px).")
            if m_bottom and int(m_bottom.group(1)) < 40:
                issues.append(f"OPC cover license too close to bottom edge ({m_bottom.group(1)}px).")
        # Last slide should mirror cover style with hero background.
        sources_blocks = len(re.findall(r'<div class="slide slide-sources', html))
        sources_with_bg = len(re.findall(r'<div class="slide slide-sources[^"]*">\s*<div class="bg-photo"', html))
        if sources_blocks and sources_with_bg < sources_blocks:
            issues.append("OPC last slide miss: sources slide is missing hero background image block.")
        # Relevance checks: each body slide context image query should share keywords with slide copy.
        for slide_cls in ("slide-list", "slide-tip"):  # slide-stat has no image slot by design
            m_slide = re.search(rf'<div class="slide {slide_cls}[^"]*">([\s\S]*?)</div>\s*<div class="slide', html)
            if not m_slide:
                continue
            block = m_slide.group(1)
            m_q = re.search(r'data-query="([^"]*)"', block)
            if not m_q:
                issues.append(f"OPC relevance miss: {slide_cls} context slot has no data-query metadata.")
                continue
            query = m_q.group(1).replace("&quot;", '"').strip()
            if not query:
                issues.append(f"OPC relevance miss: {slide_cls} has empty image query.")
                continue
            q_tokens = _tokens(query)
            specific_tokens = {t for t in q_tokens if len(t) >= 6 and t not in GENERIC_IMAGE_QUERY_TOKENS}
            if len(specific_tokens) < 1:
                issues.append(
                    f"OPC relevance miss: {slide_cls} image query too generic for reliable match: '{query[:80]}'"
                )
        # Text overflow/collision lint (enforce before render problems happen).
        m_hl = re.search(r'<div class="headline">([\s\S]*?)</div>', html)
        if m_hl:
            hl_txt = re.sub(r"<[^>]+>", "", m_hl.group(1)).strip()
            if len(hl_txt) > 42:
                issues.append(f"OPC cover headline too long ({len(hl_txt)} chars) — risk of overflow/collision.")
        items = re.findall(r'<div class="list-text">([\s\S]*?)</div>', html)
        for idx, it in enumerate(items, start=1):
            it_txt = re.sub(r"<[^>]+>", "", it).strip()
            if len(it_txt) > 34:
                issues.append(f"OPC list item {idx} too long ({len(it_txt)} chars) — risk of line wrap collision.")
        # Source/readability quality checks.
        sources = re.findall(
            r'<div class="src-row">\s*<span class="src-num">[\s\S]*?</span>\s*<span>([\s\S]*?)</span>\s*</div>',
            html
        )
        if len(sources) < 3:
            issues.append(f"OPC sources quality miss: only {len(sources)} source line(s); require >=3.")
        for idx, src in enumerate(sources, start=1):
            s = re.sub(r"<[^>]+>", "", src).strip()
            if len(s) < 12:
                issues.append(f"OPC sources quality miss: source line {idx} too short/readability-poor.")
            if len(s) > 120:
                issues.append(f"OPC sources quality miss: source line {idx} too long ({len(s)} chars), likely tiny text.")
        # CSS readability lints (font-size floor + known low-contrast cover bug).
        def _css_px(selector: str):
            m = re.search(rf'{re.escape(selector)}\s*\{{([\s\S]*?)\}}', html)
            if not m:
                return None
            m2 = re.search(r'font-size\s*:\s*([0-9]+)px', m.group(1))
            return int(m2.group(1)) if m2 else None
        fs_tag = _css_px(".tag")
        fs_body = _css_px(".body-text")
        fs_src = _css_px(".src-row")
        if fs_tag is not None and fs_tag < 16:
            issues.append(f"OPC readability miss: .tag font-size too small ({fs_tag}px).")
        if fs_body is not None and fs_body < 28:
            issues.append(f"OPC readability miss: .body-text font-size too small ({fs_body}px).")
        if fs_src is not None and fs_src < 20:
            issues.append(f"OPC readability miss: .src-row font-size too small ({fs_src}px).")
        # Headline floor check — catches inline style overrides that crush headlines below readable size.
        # export_variants.js auto-shrink floor was raised to 48px for headlines; flag anything below that.
        for hl_sel in (".headline", ".headline-main", ".tip-big", ".stat-big"):
            for m_hl_style in re.finditer(
                rf'class="[^"]*{re.escape(hl_sel.lstrip("."))}[^"]*"[^>]*style="[^"]*font-size\s*:\s*([0-9]+)px',
                html,
            ):
                fs_hl = int(m_hl_style.group(1))
                if fs_hl < 48:
                    issues.append(
                        f"[P0] OPC headline too small: {hl_sel} rendered at {fs_hl}px "
                        f"(minimum 48px). Auto-shrink may have crushed the title. Do not approve."
                    )
        if ".v2.slide-cover .headline" in html:
            m_v2 = re.search(r"\.v2\.slide-cover\s+\.headline[^{]*\{([\s\S]*?)\}", html)
            if m_v2 and "#0A0A0A" in m_v2.group(1):
                issues.append("OPC readability miss: v2 cover headline uses near-black color over dark overlay.")

    # ── Structural content checks ─────────────────────────────────────────────
    # Cover headline has text (OPC: .headline on .slide-cover; Brazil: .cover-hl)
    hl_matches = re.findall(
        r'class="(?:headline|cover-hl)"[^>]*>(.*?)</div>', html, re.DOTALL
    )
    has_hl = any(re.sub(r'<[^>]+>', '', h).strip() for h in hl_matches)
    if not has_hl:
        issues.append("Cover headline empty — carousel has no visible title")

    # Sources slide present
    if "slide-sources" not in html:
        issues.append("Sources slide missing — no attribution/CTA slide")

    # CTA present (OPC: .save-cta, Brazil: .cta-pt)
    if "save-cta" not in html and "cta-pt" not in html:
        issues.append("CTA missing on sources slide — no 'save this' prompt")

    # <img> tags with empty src attribute
    empty_src = re.findall(r'<img\s[^>]*src\s*=\s*["\'\']["\'\'][^>]*>', html)
    if empty_src:
        issues.append(f"{len(empty_src)} <img> tag(s) have empty src — broken image slot(s)")

    return issues


def check_png_folder(png_dir: str, min_slides: int = 4) -> list[str]:
    """Check PNG output folder for count + size sanity."""
    issues = []
    if not Path(png_dir).exists():
        return [f"PNG folder missing: {png_dir}"]

    pngs = sorted(Path(png_dir).glob("*.png"))
    if len(pngs) < min_slides:
        issues.append(f"Too few PNGs: {len(pngs)} found, expected ≥ {min_slides}")

    tiny = [p.name for p in pngs if p.stat().st_size < 10_000]
    if tiny:
        issues.append(f"Suspiciously small PNGs (blank slide?): {', '.join(tiny)}")

    # Rendered PNG QA: dimension + blankness check.
    if Image is not None and ImageStat is not None:
        for p in pngs:
            try:
                with Image.open(p) as im:
                    w, h = im.size
                    if (w, h) != (1080, 1350):
                        issues.append(f"{p.name}: wrong dimensions {w}x{h} (expected 1080x1350).")
                    stat = ImageStat.Stat(im.convert("L"))
                    mean = stat.mean[0]
                    stdv = stat.stddev[0]
                    if stdv < 8:
                        issues.append(f"{p.name}: low visual variance (stddev={stdv:.1f}) — likely flat/blank render.")
                    if mean < 10 or mean > 245:
                        issues.append(f"{p.name}: extreme brightness mean={mean:.1f} — likely rendering issue.")
            except Exception as e:
                issues.append(f"{p.name}: PNG QA read failed ({e})")

    return issues


def check_motion_folder(motion_dir: str) -> list[str]:
    """Check that at least 1 MP4 was rendered."""
    if not Path(motion_dir).exists():
        return ["Motion folder missing entirely"]
    mp4s = list(Path(motion_dir).glob("*.mp4"))
    if not mp4s:
        return ["No MP4 files in motion folder — motion render failed"]
    return []


# ─── Placeholder auto-fix: Wikimedia fetch + HTML patch + re-render + Drive upload ──

def _fetch_wikimedia_image(search_query: str, dest_path) -> bool:
    """Download a CC-licensed image from Wikimedia Commons.
    Mirrors carousel_builder._fetch_person_photo — no API key needed.
    Returns True on success. All exceptions caught.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return True
    try:
        q = urllib.parse.quote_plus(search_query)
        search_url = (
            f"https://commons.wikimedia.org/w/api.php?action=query&list=search"
            f"&srsearch={q}&srnamespace=6&srlimit=8&format=json&srprop="
        )
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "oak-park-carousel/1.0 (github.com/priihigashi)"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        for hit in data.get("query", {}).get("search", [])[:8]:
            title = hit.get("title", "")
            if not any(title.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                continue
            enc = urllib.parse.quote(title.replace(" ", "_"))
            info_url = (
                f"https://commons.wikimedia.org/w/api.php?action=query"
                f"&titles={enc}&prop=imageinfo&iiprop=url&iiurlwidth=600&format=json"
            )
            info = json.loads(urllib.request.urlopen(
                urllib.request.Request(
                    info_url, headers={"User-Agent": "oak-park-carousel/1.0"}
                ),
                timeout=10,
            ).read())
            for page in info.get("query", {}).get("pages", {}).values():
                ii = (page.get("imageinfo") or [{}])[0]
                img_url = ii.get("thumburl") or ii.get("url", "")
                if not img_url:
                    continue
                with urllib.request.urlopen(img_url, timeout=15) as r:
                    raw = r.read()
                if len(raw) < 2000:
                    continue
                dest_path.write_bytes(raw)
                print(f"    [wiki-fix] {dest_path.name} ({len(raw)//1024}KB) <- {title[:55]}")
                return True
        print(f"    [wiki-fix] No Wikimedia result: {search_query[:60]}")
    except Exception as e:
        print(f"    [wiki-fix] Fetch failed ({search_query[:40]}): {e}")
    return False


def _patch_html_placeholders(html_path: str, work_dir) -> tuple:
    """Patch [ IMG: query ] and @NAME_STICKER placeholders with Wikimedia images.

    Covers both execution paths:
      Path A (local): html_path is in WORK_DIR, work_dir = post_dir
      Path B (Drive): html_path is a /tmp download, work_dir = scratch dir

    Returns (patched: bool, fixes: list[str], remaining: list[str]).
    patched=True means HTML was modified on disk and re-render is needed.
    """
    html_file = Path(html_path)
    if not html_file.exists():
        return False, [], []
    html = html_file.read_text(encoding="utf-8", errors="ignore")
    original = html
    img_dir = Path(work_dir) / "resources" / "images"
    fixes = []
    remaining = []

    # Fix 1: [ IMG: query ] context-image slots
    # HTML pattern: <span class="ctx-query">[ IMG: Câmara dos Deputados ]</span>
    img_re = re.compile(r"\[ IMG: ([^\]]{3,120}) \]")
    for idx, m in enumerate(img_re.finditer(html)):
        query = m.group(1).strip()
        safe = re.sub(r"[^\w]", "_", query.lower())[:40] + f"_{idx}.jpg"
        dest = img_dir / safe
        if _fetch_wikimedia_image(query, dest):
            rel = f"resources/images/{safe}"
            old = f'<span class="ctx-query">[ IMG: {query} ]</span>'
            new = (
                f'<img src="{rel}" alt="{query}" '
                f'style="width:100%;height:100%;object-fit:cover;border-radius:4px;">'
            )
            html = html.replace(old, new, 1)
            fixes.append(f"IMG '{query[:50]}' -> Wikimedia")
        else:
            remaining.append(
                f"CONTEXT-IMAGE slot not auto-resolved (no Wikimedia match): "
                f"'[ IMG: {query[:50]} ]' — source manually"
            )

    # Fix 2: @NAME_STICKER Brazil profile sticker slots
    # HTML pattern: <div class="sticker-placeholder">@LASTNAME_STICKER</div>
    sticker_re = re.compile(r"@(\w+)_STICKER")
    for m in sticker_re.finditer(html):
        raw_name = m.group(1)
        query = raw_name.replace("_", " ")
        safe = re.sub(r"[^\w]", "_", query.lower())[:30] + "_sticker.jpg"
        dest = img_dir / safe
        if _fetch_wikimedia_image(query, dest):
            rel = f"resources/images/{safe}"
            old_slot = (
                f'<div class="sticker-slot">'
                f'<div class="sticker-placeholder">@{raw_name}_STICKER</div></div>'
            )
            new_slot = (
                f'<div class="sticker-slot sticker-photo" '
                f'style="background-image:url(\'{rel}\');background-size:cover;'
                f'background-position:center top;border:none;border-radius:4px;"></div>'
            )
            html = html.replace(old_slot, new_slot, 1)
            fixes.append(f"Sticker '@{raw_name}_STICKER' -> Wikimedia")
        else:
            remaining.append(
                f"STICKER placeholder not auto-resolved (no Wikimedia match): "
                f"'@{raw_name}_STICKER' — source headshot manually"
            )

    if html != original:
        html_file.write_text(html, encoding="utf-8")
        return True, fixes, remaining
    return False, fixes, remaining


def _replace_pngs_in_drive(folder_id: str, local_png_dir, drive,
                            skip_cover: bool = False) -> None:
    """Delete all PNGs from folder_id and re-upload from local_png_dir.
    skip_cover=True preserves cover in motion/ so existing cover MP4 still aligns.
    """
    from googleapiclient.http import MediaFileUpload
    try:
        existing = drive.files().list(
            q=f"\'{folder_id}\' in parents and mimeType='image/png' and trashed=false",
            fields="files(id)",
            supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
        ).execute().get("files", [])
        for f in existing:
            drive.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
    except Exception as e:
        print(f"    [rerender] PNG delete failed: {e}")
    for p in sorted(Path(local_png_dir).glob("*.png")):
        if skip_cover and "_01_cover" in p.name:
            continue
        try:
            drive.files().create(
                body={"name": p.name, "parents": [folder_id]},
                media_body=MediaFileUpload(str(p), mimetype="image/png"),
                supportsAllDrives=True, fields="id",
            ).execute()
        except Exception as e:
            print(f"    [rerender] PNG upload failed ({p.name}): {e}")


def _rerender_and_upload(html_path: str, png_dir: str,
                         version_folder_id: str, motion_folder_id: str = "") -> bool:
    """Re-render PNGs from a patched HTML file and push them back to Drive.

    Runs on BOTH execution paths:
      Path A (local WORK_DIR): version_folder_id from result dict
      Path B (Drive manual):   version_folder_id = folder under review

    Steps:
      1. Re-run export_variants.js (Playwright) -> local png_dir
      2. Upload patched cover.html to version_folder root
      3. Replace PNGs in version_folder/png/ subfolder
      4. Replace non-cover PNGs in motion_folder (preserves MP4s/GIFs)

    Returns True if re-render succeeded.
    Drive failures are logged but non-fatal — re-render success is the key signal.
    """
    export_js = os.environ.get(
        "EXPORT_SCRIPT", str(Path(__file__).parent / "export_variants.js")
    )
    if not Path(export_js).exists():
        print(f"    [rerender] export_variants.js not found at {export_js} — skipping")
        return False

    os.makedirs(png_dir, exist_ok=True)
    try:
        res = subprocess.run(
            ["node", export_js, html_path, png_dir],
            capture_output=True, text=True, timeout=120,
        )
        if res.returncode != 0:
            print(f"    [rerender] FAILED: {res.stderr[:300]}")
            return False
        last_line = res.stdout.strip().split("\n")[-1]
        print(f"    [rerender] {last_line}")
        for f in Path(png_dir).glob("blue_*"):
            f.rename(f.parent / f.name.replace("blue_", "lime_"))
    except Exception as e:
        print(f"    [rerender] Error running export_variants.js: {e}")
        return False

    if not version_folder_id:
        return True
    try:
        drive = _build_drive_service()
        if not drive:
            print("    [rerender] Drive service unavailable — patched assets not pushed to Drive")
            return True

        from googleapiclient.http import MediaInMemoryUpload

        # Update cover.html at version folder root
        html_bytes = Path(html_path).read_bytes()
        existing_html = drive.files().list(
            q=f"\'{version_folder_id}\' in parents and name='cover.html' and trashed=false",
            fields="files(id)",
            supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
        ).execute().get("files", [])
        if existing_html:
            drive.files().update(
                fileId=existing_html[0]["id"],
                media_body=MediaInMemoryUpload(html_bytes, mimetype="text/html"),
                supportsAllDrives=True,
            ).execute()
        else:
            drive.files().create(
                body={"name": "cover.html", "parents": [version_folder_id]},
                media_body=MediaInMemoryUpload(html_bytes, mimetype="text/html"),
                supportsAllDrives=True, fields="id",
            ).execute()
        print("    [rerender] Drive: cover.html updated")

        # Replace PNGs in png/ subfolder
        png_folder_id = _find_folder_id(drive, version_folder_id, "png")
        if not png_folder_id:
            png_folder_id = drive.files().create(
                body={"name": "png", "mimeType": "application/vnd.google-apps.folder",
                      "parents": [version_folder_id]},
                supportsAllDrives=True, fields="id",
            ).execute()["id"]
        _replace_pngs_in_drive(png_folder_id, Path(png_dir), drive, skip_cover=False)
        print("    [rerender] Drive: png/ subfolder updated")

        # Replace non-cover PNGs in motion/ subfolder (keep MP4s/GIFs)
        if motion_folder_id:
            _replace_pngs_in_drive(motion_folder_id, Path(png_dir), drive, skip_cover=True)
            print("    [rerender] Drive: motion/ non-cover PNGs updated")

    except Exception as e:
        print(f"    [rerender] Drive upload failed (non-fatal): {e}")

    return True


def _extract_drive_id(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", text):
        return text
    return ""


def _build_drive_service():
    if not SHEETS_TOKEN:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(SHEETS_TOKEN))
    return build("drive", "v3", credentials=creds)


def _list_children(drive, folder_id: str, mime=None):
    q = f"'{folder_id}' in parents and trashed=false"
    if mime:
        q += f" and mimeType='{mime}'"
    return drive.files().list(
        q=q,
        fields="files(id,name,mimeType,size,webViewLink)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
    ).execute().get("files", [])


def _find_folder_id(drive, parent_id: str, name: str) -> str:
    q = (
        f"'{parent_id}' in parents and trashed=false and "
        f"mimeType='application/vnd.google-apps.folder' and name='{name}'"
    )
    files = drive.files().list(
        q=q,
        fields="files(id,name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
    ).execute().get("files", [])
    return files[0]["id"] if files else ""


def _download_drive_text(drive, file_id: str) -> str:
    req = drive.files().get_media(fileId=file_id)
    return req.execute().decode("utf-8", errors="ignore")


def _download_drive_bytes(drive, file_id: str) -> bytes:
    req = drive.files().get_media(fileId=file_id)
    return req.execute()


def _check_drive_png_bytes(name: str, raw: bytes) -> list[str]:
    issues = []
    if Image is None or ImageStat is None:
        return issues
    try:
        import io
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
            if (w, h) != (1080, 1350):
                issues.append(f"{name}: wrong dimensions {w}x{h} (expected 1080x1350).")
            stat = ImageStat.Stat(im.convert("L"))
            mean = stat.mean[0]
            stdv = stat.stddev[0]
            if stdv < 8:
                issues.append(f"{name}: low visual variance (stddev={stdv:.1f}) — likely flat/blank render.")
            if mean < 10 or mean > 245:
                issues.append(f"{name}: extreme brightness mean={mean:.1f} — likely rendering issue.")
    except Exception as e:
        issues.append(f"{name}: PNG QA read failed ({e})")
    return issues


def _resolve_version_root(drive, folder_id: str) -> str:
    """If a child folder like png/motion/resources is passed, move up to version root."""
    meta = drive.files().get(
        fileId=folder_id, fields="id,name,parents", supportsAllDrives=True
    ).execute()
    name = (meta.get("name") or "").strip().lower()
    if name not in {"png", "motion", "resources"}:
        return folder_id
    parents = meta.get("parents") or []
    if not parents:
        return folder_id
    return parents[0]


_AI_PROVIDERS = {"gemini", "seedream", "dall-e-3", "sdxl"}


# Phase 10 — wrong-image gate. Topics about structural work (concrete/CMU/
# rebar/foundation/formwork) MUST NOT pair with interior catalog photos
# (Kitchens/Bathrooms/Cabinets). photo_matcher applies a category-mismatch
# penalty at SCORING time but if no other photo qualified, a wrong-category
# match can still ship. Reviewer is the last gate.
_STRUCTURAL_TOPIC_TOKENS = {
    "concrete", "cmu", "rebar", "foundation", "formwork", "slab",
    "footing", "drainage", "waterproof", "block wall", "masonry",
    "stem wall", "tie beam",
}
_INTERIOR_CATALOG_CATEGORIES = {
    "kitchens", "kitchen", "bathrooms", "bathroom", "cabinets",
    "tile", "countertops", "flooring", "interiors",
}


# Phase 10 — sources-vs-claims gate. Any slide containing a $, %, or "X years"
# claim must have a credible source on the sources slide. "Credible" = at
# least one source naming an external authority (FBC, ACI, NAHB, ASCE, IBC,
# IRC, EPA, OSHA, Houzz, NAHB, Remodeling Magazine, Census, BLS, CDC, USDA,
# DOE). OPC self-citation alone is NOT enough when numbers are present.
# Phase 11/M2 — only flag claim-shaped numerics, not calendar years.
# - $ amounts: $X, $X-$Y, $XK, $X.XK
# - percentages: NN%, NN-NN%
# - durability claims: "30+ years", "30 to 50 years" — but NOT 4-digit
#   calendar years (1900-2099) which appear in source citations.
_NUMERIC_CLAIM_RE = re.compile(
    r"\$\s*\d"                                # any dollar amount
    r"|\d+(?:\.\d+)?\s*%"                      # percentage
    r"|(?<![12]\d{3})\b\d{1,3}\+?\s*years?\b", # "30+ years" but not "2024 years" (calendar)
    re.IGNORECASE,
)
_CREDIBLE_SOURCE_TOKENS = {
    "fbc", "florida building code", "irc", "ibc", "icc",
    "aci", "asce", "asme", "osha", "epa", "doe",
    "nahb", "houzz", "remodeling magazine", "remodeling.com",
    "census", "bls", "cdc", "usda", "energy star", "ashrae",
    "national association", "department of",
}


def _flatten_text_from_content(content: dict) -> str:
    """Concatenate all string values from content dict + nested standalones."""
    out = []
    for k, v in (content or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict):
                    for vv in x.values():
                        if isinstance(vv, str):
                            out.append(vv)
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str):
                    out.append(vv)
                elif isinstance(vv, list):
                    out.extend(s for s in vv if isinstance(s, str))
    return " ".join(out)


def check_sources_match_claims(content: dict) -> list[str]:
    """If slides contain $/%/years claims, the sources slide MUST cite at
    least one credible external authority. OPC self-data alone is NOT enough.
    """
    if not isinstance(content, dict):
        return []
    text = _flatten_text_from_content(content)
    has_numeric = bool(_NUMERIC_CLAIM_RE.search(text))
    if not has_numeric:
        return []
    sources = content.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    src_blob = " ".join(s.lower() for s in sources if isinstance(s, str))
    if not src_blob.strip():
        return [
            "[content] slides contain numeric claims ($/%/years) but sources "
            "list is empty — every cost/stat needs a cited source."
        ]
    has_credible = any(tok in src_blob for tok in _CREDIBLE_SOURCE_TOKENS)
    only_opc = all(
        ("oak park" in s.lower() or "opc" in s.lower())
        for s in sources if isinstance(s, str) and s.strip()
    )
    if only_opc and not has_credible:
        return [
            "[content] numeric claims appear but the only source is OPC self-data — "
            "add at least one external authority (FBC, ACI, NAHB, Houzz, etc.) "
            "or downgrade the claim to non-numeric wording."
        ]
    if not has_credible:
        return [
            "[content] numeric claims present but no credible external source "
            f"in the sources slide ({len(sources)} entries, none cite FBC/ACI/"
            "NAHB/etc.). Either add a real source or remove the number."
        ]
    return []


def _check_provenance(prov: dict, topic: str = "") -> list[str]:
    """Read media_provenance.json dict and flag AI-sourced images +
    category mismatches (Phase 10 wrong-image gate).

    Per IMAGE_QUALITY_RULES.md:
      - Any slide with source_type=="ai" means all real-photo tiers (Wikimedia/Pexels/Pixabay) missed.
        These are flagged with [fix_type=regenerate] — the fix is always to improve the query and re-fetch.
      - Cover with source_type=="ai" AND subject_type=="person" is a CRITICAL violation (editorial rule).
      - Phase 10: structural topic + interior catalog photo (Kitchens etc) → wrong-image.
    """
    issues = []
    topic_l = (topic or "").lower()
    is_structural_topic = any(tok in topic_l for tok in _STRUCTURAL_TOPIC_TOKENS)

    # Cover check
    cover = prov.get("cover", {})
    if isinstance(cover, dict) and cover.get("source_type") == "ai":
        provider = cover.get("provider", "unknown")
        query = cover.get("query", "")
        # Check if subject_type was recorded (not all builds store it, but flag either way)
        subject_type = cover.get("subject_type", "")
        if subject_type == "person":
            issues.append(
                f"[fix_type=regenerate] CRITICAL: cover image is AI-generated ({provider}) for a named person — "
                f"editorial rule requires real CC photo only. Query was: '{query[:60]}'"
            )
        else:
            issues.append(
                f"[fix_type=regenerate] Cover image is AI-generated ({provider}) — "
                f"real-photo tiers (Wikimedia/Pexels/Pixabay) all missed. Improve query: '{query[:60]}'"
            )

    # Per-slide check
    slides = prov.get("slides", {})
    for slide_key, slide_data in slides.items():
        if not isinstance(slide_data, dict):
            continue
        if slide_data.get("source_type") == "ai":
            provider = slide_data.get("provider", "unknown")
            query = slide_data.get("query", "")
            issues.append(
                f"[fix_type=regenerate] Slide {slide_key}: AI image ({provider}) used — "
                f"real-photo tiers missed. Make query more specific: '{query[:60]}'"
            )

    # Phase 10/M1 — wrong-category gate. If the topic is structural and an
    # opc_catalog photo's description suggests interior category, flag it.
    # Prefer service_type when present; fall back to description heuristic
    # but require STRONG signal (≥2 interior tokens AND no structural tokens)
    # to avoid false-positives on descriptions like "kitchen demo before slab"
    # (which is actually a structural project that happens to mention kitchen).
    if is_structural_topic:
        def _flag_if_wrong_cat(slot_label, sd):
            if not isinstance(sd, dict):
                return
            if sd.get("provider", "").lower() != "opc_catalog":
                return
            # Tier 1 — explicit service_type wins (added in Phase 10 prov writer).
            svc = (sd.get("service_type") or sd.get("service") or "").strip().lower()
            if svc:
                if any(c in svc for c in _INTERIOR_CATALOG_CATEGORIES):
                    issues.append(
                        f"[fix_type=wrong-image] {slot_label}: structural topic "
                        f"'{topic[:40]}' got interior service '{svc}' photo."
                    )
                return  # service_type was authoritative — done
            # Tier 2 — heuristic from description. Require ≥2 interior tokens
            # AND zero structural tokens to confidently flag.
            desc = (sd.get("query", "") or "").lower()
            interior_hits = sum(1 for c in _INTERIOR_CATALOG_CATEGORIES if c in desc)
            structural_hits = sum(1 for s in _STRUCTURAL_TOPIC_TOKENS if s in desc)
            if interior_hits >= 2 and structural_hits == 0:
                issues.append(
                    f"[fix_type=wrong-image] {slot_label}: structural topic "
                    f"'{topic[:40]}' got interior catalog photo "
                    f"({interior_hits} interior tokens, 0 structural). "
                    f"Description: '{desc[:80]}'"
                )
        _flag_if_wrong_cat("Cover", cover)
        for slide_key, slide_data in slides.items():
            _flag_if_wrong_cat(f"Slide {slide_key}", slide_data)

    # Summary ratio warning (kept for dashboards/email scannability)
    all_providers = []
    if isinstance(cover, dict) and cover.get("provider"):
        all_providers.append(cover["provider"].lower())
    for v in slides.values():
        if isinstance(v, dict) and v.get("provider"):
            all_providers.append(v["provider"].lower())
    if all_providers:
        ai_count = sum(1 for p in all_providers if p in _AI_PROVIDERS)
        ratio = ai_count / len(all_providers)
        if ratio >= 0.5 and ai_count > 1:
            issues.append(
                f"Realism risk: {ai_count}/{len(all_providers)} images are AI-generated "
                f"({ratio:.0%}) — target is mostly real photos/stock."
            )

    return issues


def _vision_check_image(image_bytes: bytes, filename: str, query: str) -> str:
    """Ask Claude Haiku Vision: does this image match the query? Returns 'YES ...' or 'NO ...'."""
    if not ANTHROPIC_KEY or len(image_bytes) < 5000:
        return "SKIP"
    try:
        mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
        b64 = base64.b64encode(image_bytes).decode()
        payload = json.dumps({
            "model": "claude-sonnet-4-6",  # Phase 10 — Sonnet for vision/text quality
            "max_tokens": 80,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                    {"type": "text", "text": (
                        f"Does this image visually represent: '{query}'?\n"
                        f"YES if it clearly shows the correct subject, action, or setting.\n"
                        f"NO if it shows something completely unrelated (wrong object, wrong scene).\n"
                        f"Start your answer with YES or NO, then one sentence."
                    )},
                ],
            }],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return resp["content"][0]["text"].strip()
    except Exception as e:
        print(f"  Vision check error (non-fatal): {e}")
        return "SKIP"


def check_resource_images_local(images_dir: str, prov: dict) -> list[str]:
    """Check local resource images for corruption and vision relevance.

    Runs during the GitHub Actions build while /tmp still exists.
    Two checks per file:
      1. Corruption — Pillow im.load() catches truncated/empty/non-image files.
      2. Relevance — Haiku vision check against the query from media_provenance.json.
    """
    if Image is None:
        print("  WARNING: Pillow not available — image integrity check skipped")
        return []

    issues = []
    images_path = Path(images_dir)
    if not images_path.exists():
        return []

    # Build filename → (query, slot_label) from provenance
    query_map: dict = {}
    cover = prov.get("cover", {})
    if isinstance(cover, dict) and cover.get("path"):
        fname = Path(cover["path"]).name
        query_map[fname] = (cover.get("query", ""), "cover")
    for slide_key, slide_data in prov.get("slides", {}).items():
        if isinstance(slide_data, dict) and slide_data.get("path"):
            fname = Path(slide_data["path"]).name
            query_map[fname] = (slide_data.get("query", ""), f"slide_{slide_key}")

    vision_checked = 0
    for img_path in sorted(images_path.iterdir()):
        if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            continue

        # Step 1 — corruption check
        try:
            raw = img_path.read_bytes()
            with Image.open(io.BytesIO(raw)) as im:
                im.load()  # force full decode — catches truncated / corrupt files
        except Exception as e:
            issues.append(
                f"[fix_type=corrupt-image] {img_path.name}: corrupt or unreadable ({e})"
            )
            continue  # no point running vision on a broken file

        # Step 2 — vision relevance (only when provenance query known + API key present)
        slot_info = query_map.get(img_path.name)
        if slot_info and ANTHROPIC_KEY:
            query, slot_label = slot_info
            if query:
                verdict = _vision_check_image(raw, img_path.name, query)
                vision_checked += 1
                if verdict.upper().startswith("NO"):
                    issues.append(
                        f"[fix_type=wrong-image] {slot_label}: '{img_path.name}' does not match "
                        f"query '{query[:60]}'. Vision: {verdict[:120]}"
                    )

    if vision_checked:
        mismatch = sum(1 for i in issues if "[fix_type=wrong-image]" in i)
        print(f"  Vision relevance (local): checked {vision_checked} image(s), {mismatch} mismatch(es)")

    corrupt = sum(1 for i in issues if "[fix_type=corrupt-image]" in i)
    if corrupt:
        print(f"  Image integrity: {corrupt} corrupt file(s) detected in {images_dir}")

    return issues


def _check_image_relevance_drive(prov: dict, images_folder_id: str, drive,
                                 max_checks: int = 8) -> list[str]:
    """Download each resource image and verify visual match via Claude Vision.

    Checks ALL sourced images (stock + AI) — stock images are the main failure mode
    (wrong Pixabay result) but AI images can also miss the intent.
    Caps at max_checks images per carousel to control API cost.
    """
    if not ANTHROPIC_KEY:
        return []

    # Collect all slots that have a stored image path
    slots = []
    cover = prov.get("cover", {})
    if isinstance(cover, dict) and cover.get("path"):
        slots.append(("cover", cover.get("query", ""), cover["path"].split("/")[-1]))
    for slide_key, slide_data in prov.get("slides", {}).items():
        if isinstance(slide_data, dict) and slide_data.get("path"):
            slots.append((f"slide_{slide_key}", slide_data.get("query", ""), slide_data["path"].split("/")[-1]))

    if not slots:
        return []

    # List images/ folder
    try:
        res = drive.files().list(
            q=f"'{images_folder_id}' in parents and trashed=false",
            fields="files(id,name)", supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        img_map = {f["name"]: f["id"] for f in res.get("files", [])}
    except Exception as e:
        return [f"Vision check: could not list images folder ({e})"]

    from googleapiclient.http import MediaIoBaseDownload

    issues = []
    checked = 0
    for slot_label, query, filename in slots[:max_checks]:
        if not query or filename not in img_map:
            continue
        try:
            req = drive.files().get_media(fileId=img_map[filename], supportsAllDrives=True)
            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
            raw = buf.getvalue()
        except Exception as e:
            print(f"  Vision check: could not download {filename} ({e})")
            continue

        # Corruption check before spending tokens on vision
        if Image is not None:
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    im.load()
            except Exception as e:
                issues.append(
                    f"[fix_type=corrupt-image] {slot_label}: '{filename}' corrupt in Drive ({e})"
                )
                continue

        verdict = _vision_check_image(raw, filename, query)
        checked += 1

        if verdict.upper().startswith("NO"):
            issues.append(
                f"[fix_type=wrong-image] {slot_label}: '{filename}' does not match "
                f"query '{query[:60]}'. Vision: {verdict[:120]}"
            )

    if checked:
        print(f"  Vision relevance: checked {checked} image(s), {len(issues)} mismatch(es)")
    return issues


_NICHE_HINTS = {
    "opc": "opc", "oak": "opc", "tip-of-the-week": "opc",
    "brazil": "brazil", "verificamos": "brazil", "rachadinha": "brazil",
    "quem-decidiu": "brazil", "conta-que-ninguem-pagou": "brazil", "arquivo-aberto": "brazil",
    "usa": "usa", "the-chain": "usa", "history-they-left-out": "usa",
    "higashi": "higashi", "hig-": "higashi",
}


def _infer_niche_from_folder(folder_name: str, parent_path: str = "") -> str:
    """Best-effort niche inference from folder name + path. Defaults to 'opc'."""
    blob = f"{folder_name} {parent_path}".lower()
    for token, niche in _NICHE_HINTS.items():
        if token in blob:
            return niche
    return "opc"


def check_drive_folder(folder_id: str, drive, input_ref: str = "") -> dict:
    issues = []
    original_id = folder_id
    folder_id = _resolve_version_root(drive, folder_id)
    folder_meta = drive.files().get(
        fileId=folder_id, fields="id,name,webViewLink", supportsAllDrives=True
    ).execute()
    folder_name = folder_meta.get("name", folder_id)

    files = _list_children(drive, folder_id)
    html_file = next((f for f in files if f.get("name") == "cover.html"), None)
    _tmp_html: Path | None = None
    if html_file:
        try:
            html_text = _download_drive_text(drive, html_file["id"])
            tmp = Path("/tmp") / f"review_{folder_id}.html"
            tmp.write_text(html_text, encoding="utf-8")
            _tmp_html = tmp
            issues.extend(check_html_placeholders(str(tmp)))
            # Placeholder auto-fix (Path B — Drive folder review)
            # IMG/STICKER placeholders -> Wikimedia -> HTML patch -> re-render -> Drive upload.
            if not DRY_RUN:
                _b_work = tmp.parent / f"pfx_work_{folder_id[:12]}"
                _b_work.mkdir(exist_ok=True)
                _b_patched, _b_fixes, _b_remaining = _patch_html_placeholders(
                    str(tmp), _b_work
                )
                if _b_fixes:
                    issues.append(
                        f"[auto-fixed] {len(_b_fixes)} placeholder(s) resolved via Wikimedia: "
                        + "; ".join(_b_fixes[:3])
                    )
                issues.extend(_b_remaining)
                if _b_patched:
                    _b_motion = _find_folder_id(drive, folder_id, "motion")
                    _b_re_ok = _rerender_and_upload(
                        str(tmp),
                        str(_b_work / "png"),
                        folder_id,
                        _b_motion,
                    )
                    if not _b_re_ok:
                        issues.append(
                            "Placeholder fix: HTML patched but re-render failed — "
                            "PNGs in Drive may still show placeholders"
                        )
            # SH-028: storytelling score on Drive path (mirrors check_built_post logic)
            if ANTHROPIC_KEY:
                niche_guess = _infer_niche_from_folder(folder_name, input_ref or "")
                issues.extend(check_text_quality(str(tmp), niche_guess))
                st_scores = score_storytelling(str(tmp), niche_guess)
                if st_scores:
                    overall = st_scores.get("overall", 0)
                    if overall < 60:
                        issues.append(
                            f"[storytelling] Overall quality score {overall}/100 — "
                            f"{st_scores.get('summary', '')[:100]}"
                        )
        except Exception as e:
            issues.append(f"Could not inspect cover.html: {e}")
    else:
        issues.append("cover.html missing in version folder")

    png_folder_id = _find_folder_id(drive, folder_id, "png")
    if not png_folder_id:
        issues.append("PNG folder missing")
    else:
        pngs = [f for f in _list_children(drive, png_folder_id) if f.get("name", "").lower().endswith(".png")]
        if len(pngs) == 0 or len(pngs) % 5 != 0:
            issues.append(f"PNG count = {len(pngs)}, expected multiple of 5 (5/10/15)")
        _tset = os.environ.get("MANUAL_TEMPLATE_SET", "").strip().lower()
        if _tset == "single":
            _prefixes = sorted({p["name"].split("_")[0] for p in pngs if "_" in p.get("name", "")})
            if len(_prefixes) > 1:
                issues.append(f"multiple variant families in single mode: {_prefixes}")
        tiny = [p["name"] for p in pngs if int(p.get("size") or 0) < 10_000]
        if tiny:
            issues.append(f"Suspiciously small PNGs (blank slide?): {', '.join(tiny)}")
        # Same rendered PNG QA used by local flow.
        for p in pngs:
            try:
                raw = _download_drive_bytes(drive, p["id"])
                issues.extend(_check_drive_png_bytes(p["name"], raw))
            except Exception as e:
                issues.append(f"{p.get('name','?')}: PNG download/QA failed ({e})")

    if os.environ.get("MOTION_ENABLED", "0") != "0":
        motion_folder_id = _find_folder_id(drive, folder_id, "motion")
        if not motion_folder_id:
            issues.append("Motion folder missing entirely")
        else:
            mp4s = [f for f in _list_children(drive, motion_folder_id) if f.get("name", "").lower().endswith(".mp4")]
            if not mp4s:
                issues.append("No MP4 files in motion folder — motion render failed")

    # Realism/provenance check (if manifest exists): avoid AI-only look.
    resources_folder_id = _find_folder_id(drive, folder_id, "resources")
    if resources_folder_id:
        res_files = _list_children(drive, resources_folder_id)
        prov_file = next((f for f in res_files if f.get("name") == "media_provenance.json"), None)
        if prov_file:
            try:
                prov = json.loads(_download_drive_text(drive, prov_file["id"]))
                # Phase 10 — pass topic for wrong-image category gate. Drive
                # path doesn't have direct topic so derive from folder name.
                _drive_topic = (input_ref or "").replace("-", " ").replace("_", " ")
                issues.extend(_check_provenance(prov, topic=_drive_topic))
                try:
                    # Library opportunity signal: if a slide is AI, suggest reuse candidate.
                    for sk, sv in (prov.get("slides", {}) or {}).items():
                        if not isinstance(sv, dict):
                            continue
                        q = (sv.get("query") or "").strip()
                        if not q:
                            continue
                        hit = search_library(q, _infer_niche_from_folder(folder_name, input_ref or ""))
                        if hit and sv.get("source_type") == "ai":
                            issues.append(
                                f"[library-candidate] Slide {sk}: matching library image available "
                                f"({hit.get('drive_url','')})"
                            )
                except Exception:
                    pass
                # Vision check: verify each image actually matches its slide topic
                images_fid = _find_folder_id(drive, resources_folder_id, "images")
                if images_fid:
                    issues.extend(_check_image_relevance_drive(prov, images_fid, drive))
            except Exception as e:
                issues.append(f"media_provenance.json parse failed: {e}")

    # ── Auto-fix loop (Goals 1A + 1B) ───────────────────────────────────────
    # FIX_MODE=analyze_and_fix → auto_fix_drive_folder handles BOTH:
    #   Goal 1A: image re-fetch cascade (photo_matcher → library → AI → stock)
    #   Goal 1B: text review (Claude flags weak hooks/overflows → apply_edits_to_html)
    # Triggers on image issues ([fix_type=regenerate|wrong-image]) OR text issues.
    _TEXT_ISSUE_TOKENS = (
        "hook weak", "hook miss", "overflow risk", "too long", "too short",
        "OPC hook", "Cover hook", "readability miss",
        "copy incoherent",  # Goal 1B — narrative coherence check
    )
    autofix_summary = None
    has_regen = any(("[fix_type=regenerate]" in i or "[fix_type=wrong-image]" in i or "[fix_type=corrupt-image]" in i) for i in issues)
    has_text_issues = any(any(t in i for t in _TEXT_ISSUE_TOKENS) for i in issues)
    if FIX_MODE == "analyze_and_fix" and (has_regen or has_text_issues) and not DRY_RUN:
        try:
            from auto_fixer import auto_fix_drive_folder  # lazy import
            niche_guess = _infer_niche_from_folder(folder_name, input_ref or "")
            autofix_summary = auto_fix_drive_folder(
                drive,
                {"id": folder_id, "name": folder_name},
                niche=niche_guess,
                dry_run=False,
            )
            fixed_n = autofix_summary.get("fixed", 0)
            if fixed_n:
                issues.append(
                    f"[auto-fix] regenerated {fixed_n} image(s) "
                    f"(niche={niche_guess}, png backup: "
                    f"{autofix_summary.get('png_backup_folder_id', 'none')})"
                )
        except Exception as e:
            issues.append(f"[auto-fix] FAILED — {type(e).__name__}: {e}")

    return {
        "post_id": folder_id,
        "topic": folder_name[:60],
        "niche": "manual",
        "issues": issues,
        "passed": len(issues) == 0,
        "drive_link": folder_meta.get("webViewLink", ""),
        "input_ref": input_ref or original_id,
        "resolved_id": folder_id,
        "original_id": original_id,
        "autofix_summary": autofix_summary,
    }


# ─── Goal 1B — Text quality checks (Sonnet) ─────────────────────────────────

def _sonnet_score(prompt: str) -> tuple[int, str]:
    """Call Sonnet, parse a 1-3 score. Returns (score, reason).
    Phase 11.2 — falls back to OpenAI gpt-4o on Anthropic credits/capacity/5xx."""
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not ANTHROPIC_KEY and not openai_key:
        return 0, "no API key (neither ANTHROPIC nor OPENAI)"

    text = ""
    if ANTHROPIC_KEY:
        try:
            payload = json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": 80,
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            text = resp["content"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 402, 429, 500, 502, 503, 504, 529) and openai_key:
                text = _openai_score(prompt)
                if not text:
                    return 0, f"Anthropic HTTP {e.code} + OpenAI fallback failed"
            else:
                return 0, f"Anthropic HTTP {e.code}"
        except Exception as e:
            if openai_key:
                text = _openai_score(prompt)
                if not text:
                    return 0, f"Anthropic err: {e} + OpenAI fallback failed"
            else:
                return 0, f"error: {e}"
    else:
        text = _openai_score(prompt)
        if not text:
            return 0, "OpenAI fallback failed"

    m = re.search(r"\b([123])\b", text)
    return (int(m.group(1)) if m else 1), text


def _openai_score(prompt: str) -> str:
    """OpenAI gpt-4o fallback for _sonnet_score. Returns response text or ''."""
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return ""
    try:
        payload = json.dumps({
            "model": "gpt-4o",
            "max_tokens": 80,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {openai_key}",
                     "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return resp["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def _score_hook_strength(headline: str, subhead: str, niche: str) -> tuple[int, str]:
    """Check A: score cover hook 1-3 via Sonnet."""
    if not headline:
        return 0, "no headline extracted"
    niche_label = "homeowner tips" if niche == "opc" else "news"
    prompt = (
        f"Evaluate this Instagram carousel hook for a {niche_label} account.\n"
        f"Headline: {headline}\n"
        f"Subhead: {subhead}\n\n"
        "Score 1-3:\n"
        "3 = has a specific number/cost/risk AND creates curiosity\n"
        "2 = has one of those elements\n"
        "1 = vague or generic\n\n"
        "Reply with score (1/2/3) and one sentence why. "
        "Example: '2 — mentions a cost but no curiosity gap.'"
    )
    return _sonnet_score(prompt)


def _score_copy_coherence(
    headlines: list[str], purposes: list | None = None
) -> tuple[int, str]:
    """Check B: score narrative arc of slide headlines via Sonnet.

    P2 (SH-142): when SLIDE_PURPOSE_PILOT=1 and purposes are supplied, switches
    to a purpose-fulfillment prompt — each headline is scored against its declared
    job (hook/cost/teach/apply/sources) instead of generic arc coherence.
    """
    if len(headlines) < 2:
        return 0, "too few headlines to score"
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines) if h)
    if not numbered.strip():
        return 0, "no headline text"

    if SLIDE_PURPOSE_PILOT and purposes and isinstance(purposes, list):
        purpose_map = "\n".join(
            f"  Slide {e.get('slide', i + 1)}: declared purpose = '{e.get('purpose', '?')}'"
            for i, e in enumerate(purposes)
            if isinstance(e, dict)
        )
        prompt = (
            "These Instagram carousel slide headlines were each generated with a declared "
            "narrative purpose. Score whether each headline fulfills its purpose and whether "
            "the sequence builds toward a payoff.\n\n"
            f"Declared purposes:\n{purpose_map}\n\n"
            f"Headlines:\n{numbered}\n\n"
            "Score 1-3:\n"
            "3 = each headline clearly fulfills its declared purpose AND the sequence "
            "builds hook → cost → teach → apply → sources (or equivalent)\n"
            "2 = most headlines fulfill their purpose — 1-2 slides feel off or generic\n"
            "1 = headlines do not fulfill declared purposes, or could be in any order\n\n"
            "Reply with score (1/2/3) and one sentence naming which slide(s) "
            "fulfilled or missed their purpose."
        )
    else:
        prompt = (
            "Do these Instagram carousel slide headlines tell a complete story "
            "or feel like AI filler?\n\n"
            f"{numbered}\n\n"
            "Score 1-3:\n"
            "3 = clear narrative arc — each headline builds on the last\n"
            "2 = mostly coherent — minor gaps or repetition\n"
            "1 = disconnected or filler — could be in any order\n\n"
            "Reply with score (1/2/3) and one sentence why."
        )
    return _sonnet_score(prompt)


def check_text_quality(
    html_path: str, niche: str, purposes: list | None = None
) -> list[str]:
    """Goal 1B: run hook strength + copy coherence checks via Claude Sonnet.
    Returns issue strings; tokens match _TEXT_ISSUE_TOKENS auto-fix gate.

    P2 (SH-142): accepts optional purposes list from slide_purpose pilot so the
    Coherence scorer can check purpose fulfillment instead of generic arc.
    """
    issues = []
    if not ANTHROPIC_KEY:
        return issues
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues

    slide_blocks = _slide_blocks_for_review(html, limit=5)
    first_slide = slide_blocks[0] if slide_blocks else ""

    # Extract cover headline + subhead from the first slide only. Smart OPC
    # cover templates use .cover-hl/.cover-hook instead of the legacy
    # .headline/.subhead classes.
    headline = (
        _extract_class_text(first_slide, ("cover-hl", "headline"))
        or _plain_text(first_slide)[:90]
    )
    subhead = _extract_class_text(first_slide, ("cover-hook", "subhead", "body-text"))

    # Extract one narrative headline per slide from the first variant only.
    # The HTML contains v2 and v3 variants; reviewing both makes the story look
    # duplicated even when the visible carousel is fine.
    all_headlines = []
    for block in slide_blocks:
        h = (
            _extract_class_text(block, ("cover-hl", "profile-headline", "headline", "title", "pro-title"))
            or _plain_text(block)[:90]
        )
        if h:
            all_headlines.append(h)

    # Check A — hook strength
    hook_score, hook_reason = _score_hook_strength(headline, subhead, niche)
    print(f"  [1B] Hook {hook_score}/3 — {hook_reason[:80]}")
    if 0 < hook_score < 2:
        issues.append(f"[hook weak] Cover hook scored {hook_score}/3 — {hook_reason[:120]}")

    # Check B — copy coherence (P2/SH-142: purpose-aware when pilot active)
    # Smart-picker carousels mix heterogeneous templates (base + material_profile +
    # four_card_grid + progress_media + sources) by design — there is no cross-slide
    # narrative arc to score. OpenAI fallback (Phase 11.2) is also stricter than Sonnet
    # was, returning 1/3 even for acceptable legacy tips. Treat coherence as advisory:
    # only block on hard error/no headlines (score 0 is already excluded above).
    coh_score, coh_reason = _score_copy_coherence(all_headlines, purposes=purposes)
    _coh_mode = "purpose-aware" if (SLIDE_PURPOSE_PILOT and purposes) else "arc"
    print(f"  [1B] Coherence {coh_score}/3 ({_coh_mode}) — {coh_reason[:80]}")
    if 0 < coh_score < 2:
        # Print only — do not append to issues. Hook strength remains the gate.
        print(f"       [advisory] Coherence {coh_score}/3 ({_coh_mode}) — {coh_reason[:120]}")

    return issues


def score_storytelling(html_path: str, niche: str) -> dict:
    """SH-028: Score storytelling quality 0-100 per slide via Haiku.

    Returns {slide_scores: [{slide, score, reason}], overall: int, summary: str}.
    Empty dict if API key missing or HTML unreadable.
    """
    if not ANTHROPIC_KEY:
        return {}
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}

    # Extract per-slide visible text from one variant only. The builder stores
    # both v2 and v3 variants in cover.html; scoring both creates a fake
    # "slides repeat" failure.
    slide_blocks = _slide_blocks_for_review(html, limit=5)
    slide_texts = []
    for i, block in enumerate(slide_blocks, start=1):
        text = _plain_text(block)[:200]
        if text:
            slide_texts.append({"slide": i, "text": text})

    if not slide_texts:
        return {}

    slides_payload = "\n".join(
        f"Slide {s['slide']}: {s['text']}" for s in slide_texts
    )
    niche_label = "homeowner tips (OPC)" if niche == "opc" else "news/political fact-check"

    prompt = (
        f"You are reviewing an Instagram carousel for a {niche_label} account.\n"
        "Score each slide's storytelling quality from 0 to 100:\n"
        "90-100 = gripping, specific, pulls reader forward\n"
        "70-89  = clear and useful, minor gaps\n"
        "50-69  = generic or vague, could be any topic\n"
        "0-49   = filler, confusing, or off-message\n\n"
        f"{slides_payload}\n\n"
        "Return JSON only — no markdown, no explanation outside JSON:\n"
        '{"slide_scores":[{"slide":1,"score":85,"reason":"one sentence"},...], '
        '"overall":78,"summary":"one sentence overall"}'
    )

    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-6",  # Phase 10 — Sonnet for vision/text quality
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        raw = ""
        try:
            raw_resp = urllib.request.urlopen(req, timeout=30).read()
            resp = json.loads(raw_resp)
            raw = resp["content"][0]["text"].strip()
        except urllib.error.HTTPError as http_err:
            status = http_err.code
            body = ""
            try:
                body = http_err.read().decode(errors="ignore")
            except Exception:
                pass
            # Phase 11.2 — credits/capacity/5xx fallback to OpenAI gpt-4o so the
            # storytelling score keeps working when Anthropic balance is dry.
            openai_key = os.environ.get("OPENAI_API_KEY", "")
            if status in (400, 401, 402, 429, 500, 502, 503, 504, 529) and openai_key:
                try:
                    oai_payload = json.dumps({
                        "model": "gpt-4o",
                        "max_tokens": 400,
                        "messages": [{"role": "user", "content": prompt}],
                    }).encode()
                    oai_req = urllib.request.Request(
                        "https://api.openai.com/v1/chat/completions",
                        data=oai_payload,
                        headers={"Authorization": f"Bearer {openai_key}",
                                 "Content-Type": "application/json"},
                    )
                    oai_resp = json.loads(urllib.request.urlopen(oai_req, timeout=30).read())
                    raw = oai_resp["choices"][0]["message"]["content"].strip()
                    print(f"  [SH-028] Storytelling: Anthropic HTTP {status} → OpenAI fallback")
                except Exception as oai_err:
                    print(f"  [SH-028] Anthropic HTTP {status} + OpenAI fallback failed: {oai_err}")
                    return {}
            else:
                if status == 529 or "overloaded" in body.lower() or "credit" in body.lower():
                    print(f"  [SH-028] ⚠ WARN: Anthropic credits/capacity issue (HTTP {status}) — storytelling score skipped")
                else:
                    print(f"  [SH-028] Storytelling score HTTP error {status} (non-fatal): {body[:120]}")
                return {}
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        data = json.loads(raw)
        print(
            f"  [SH-028] Storytelling overall={data.get('overall','?')}/100 — {data.get('summary','')[:80]}"
        )
        return data
    except Exception as e:
        print(f"  [SH-028] Storytelling score error (non-fatal): {e}")
        return {}


# =============================================================================
# Phase 5 — Smart slide-plan reviewer gates (added 2026-05-06)
# =============================================================================
# Catches errors specific to the SH-OPC-SMART-SLIDE-PICKER planner output:
# - banned legacy keys (cutout/illustrated) sneaking back in via plan
# - unmapped template_ids (typo / hallucinated by the planner)
# - mismatch between slide-number and expected role
# - slide count != 5
# Vision-based image-text match remains gated on ANTHROPIC_KEY (existing behavior).

OPC_BANNED_TEMPLATE_KEYS = {"cutout", "illustrated", "opc_cutout", "opc_illustrated"}

KNOWN_OPC_TEMPLATE_IDS = {
    "opc_tip_cover", "opc_tip_stat", "opc_tip_list", "opc_tip_explainer", "opc_tip_sources",
    "opc_duotone", "opc_base", "opc_statement", "opc_material_profile",
    "opc_item_spotlight", "opc_four_card_grid", "opc_progress_media",
}

# Phase 6 — these standalones now have production Python builders. If the
# planner picks any of these AND the renderer falls back to a tip equivalent,
# something is wrong — the reviewer flags it. Single source of truth for the
# "is this template wired?" question.
OPC_PORTED_STANDALONE_IDS = {
    "opc_material_profile",
    "opc_four_card_grid",
    "opc_item_spotlight",
    "opc_statement",
    "opc_base",
    "opc_progress_media",
    "opc_duotone",
}

# Image-need expectations per template. Used to flag missing images for
# templates that *require* a photo (opc_progress_media, opc_duotone, opc_base).
OPC_TEMPLATES_REQUIRING_IMAGE = {
    "opc_progress_media",  # Real jobsite proof — required
    "opc_base",            # Hero bg photo — required for cover treatment
    "opc_duotone",         # Hero photo — duotone filter target
}

# Phase 8G — required content keys per standalone template_id. Reviewer flags
# any slide whose nested content is missing/empty/default for these fields.
# Sourced from carousel_builder.OPC_STANDALONE_SCHEMAS — keep in sync.
# Default tokens that count as MISSING (true placeholder text — never real
# brand content). Static signatures like "MIKE · OPC FOUNDER" or
# "Mike McFolling · GC" are ALLOWED — they are the real attribution lines.
OPC_STANDALONE_DEFAULT_TOKENS = {
    # Empty / em-dash
    "—",
    # Generic numbered placeholders
    "Option 1", "Option 2", "Option 3", "Option 4",
    "OPTION 1", "OPTION 2", "OPTION 3", "OPTION 4", "OPTION",
    "Detail one.", "Detail two.", "Detail three.", "Detail four.",
    "Fact one", "Fact two", "Fact three", "Fact four",
    # Generic single-word slot fillers
    "Topic", "today.", "Detail", "Decide",
    # Template stub headline-italic halves (the constant "What is" / "Spotlight
    # on" / "Compare" first-halves are LEGAL brand patterns; only the italic
    # second-halves like "this material?" / "this item." / "this detail." are
    # placeholders that mean nobody filled in the topic).
    "this material?", "this item.", "this detail.",
    "What's", "hiding",
}

OPC_STANDALONE_REQUIRED_CONTENT_KEYS = {
    "opc_material_profile": ["label", "headline_main", "headline_italic", "best_for", "not_ideal",
                              "durability", "install_notes", "cost_range", "style_fit", "decision_factors"],
    "opc_four_card_grid":   ["eyebrow", "headline_main", "headline_italic", "subhead",
                              "badges", "card_titles", "card_copies"],
    "opc_item_spotlight":   ["tag", "category", "headline_main", "headline_italic", "subhead",
                              "fact_1_title", "fact_1_desc", "fact_2_title", "fact_2_desc",
                              "fact_3_title", "fact_3_desc", "fact_4_title", "fact_4_desc"],
    "opc_statement":        ["tag", "quote_opener", "quote_body", "attribution"],
    "opc_base":             ["tag", "headline_main", "headline_italic", "cover_hook", "byline", "stamp_text"],
    "opc_progress_media":   ["tag", "eyebrow", "title_main", "title_italic",
                              "description_bold", "description_rest", "caption_pills"],
    "opc_duotone":          ["claim_main", "claim_strong", "quote_text", "attribution", "variant"],
}


def _is_standalone_field_default(value):
    """True when value is missing/empty OR matches a known placeholder/default token."""
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return True
        if v in OPC_STANDALONE_DEFAULT_TOKENS:
            return True
        return False
    if isinstance(value, list):
        if not value:
            return True
        # All items are default → list is default
        return all(_is_standalone_field_default(x) for x in value)
    return False  # numbers, dicts etc — treat as present


def check_standalone_content(content: dict, slide_num: int, template_id: str) -> list[str]:
    """Phase 8G — validate that content[template_id] has all required fields
    populated with real (non-default) values. Returns list of issue strings.
    """
    issues = []
    if template_id not in OPC_STANDALONE_REQUIRED_CONTENT_KEYS:
        return issues
    nested = (content or {}).get(template_id)
    if not isinstance(nested, dict):
        issues.append(
            f"[content] slide {slide_num}: template '{template_id}' requires "
            f"content['{template_id}'] dict but it is missing — render will use "
            "all default placeholders."
        )
        return issues
    for key in OPC_STANDALONE_REQUIRED_CONTENT_KEYS[template_id]:
        if _is_standalone_field_default(nested.get(key)):
            issues.append(
                f"[content] slide {slide_num}: '{template_id}.{key}' is missing/default — "
                f"slide will render with placeholder text."
            )
    # Cardinality checks for list fields (already enforced in builder, but cheap re-check).
    if template_id == "opc_material_profile":
        df = nested.get("decision_factors") or []
        if not isinstance(df, list) or len(df) != 4:
            issues.append(f"[content] slide {slide_num}: opc_material_profile.decision_factors must be a list of 4")
    if template_id == "opc_four_card_grid":
        for k in ("badges", "card_titles", "card_copies"):
            v = nested.get(k) or []
            if not isinstance(v, list) or len(v) != 4:
                issues.append(f"[content] slide {slide_num}: opc_four_card_grid.{k} must be a list of 4")
        pair = (content or {}).get("_comparison_pair") or {}
        left = str(pair.get("left", "")).strip()
        right = str(pair.get("right", "")).strip()
        if left and right:
            titles = nested.get("card_titles") or []
            copies = nested.get("card_copies") or []
            combined = " ".join(str(x) for x in titles + copies)
            if not _entity_in_text(left, combined) or not _entity_in_text(right, combined):
                issues.append(
                    f"[content] slide {slide_num}: comparison pair '{left}' vs '{right}' "
                    "is not represented across opc_four_card_grid text."
                )
            for idx, copy in enumerate(copies[:4], start=1):
                title = str(titles[idx - 1]) if idx - 1 < len(titles) else ""
                text = f"{title} {copy}"
                title_declares_winner = bool(re.search(r"\b(wins?|winner)\b", title, flags=re.IGNORECASE))
                has_left = _entity_in_text(left, text)
                has_right = _entity_in_text(right, text)
                if not title_declares_winner and (has_left != has_right):
                    issues.append(
                        f"[content] slide {slide_num}: card {idx} is one-sided for "
                        f"'{left}' vs '{right}' — compare both sides or declare a winner."
                    )
            queries = [str(x) for x in (nested.get("card_image_queries") or [])]
            if queries:
                left_q = sum(1 for q in queries if _entity_in_text(left, q))
                right_q = sum(1 for q in queries if _entity_in_text(right, q))
                if left_q < 1 or right_q < 1:
                    issues.append(
                        f"[content] slide {slide_num}: card_image_queries are not balanced "
                        f"for '{left}' vs '{right}' (left={left_q}, right={right_q})."
                    )
    if template_id == "opc_progress_media":
        cp = nested.get("caption_pills") or []
        if not isinstance(cp, list) or len(cp) < 1:
            issues.append(f"[content] slide {slide_num}: opc_progress_media.caption_pills must be a list of 3")
    if template_id == "opc_duotone":
        v = str(nested.get("variant", "")).strip().lower()
        if v not in ("v1", "v2", "v3"):
            issues.append(f"[content] slide {slide_num}: opc_duotone.variant must be 'v1' | 'v2' | 'v3' (got {v!r})")
    return issues

EXPECTED_ROLE_FOR_SLIDE = {
    1: "cover",
    2: "definition",
    3: "comparison",
    4: "statement",
    5: "sources",
}


def check_slide_plan(content: dict) -> list[str]:
    """Phase 5 — validate content['_slide_plan'] before render/upload.
    Returns a list of issue strings (empty list = passed).
    No-op when no plan is attached (legacy tip path)."""
    plan = (content or {}).get("_slide_plan") or {}
    if not plan:
        return []  # legacy path — nothing to check

    issues = []
    if plan.get("status") != "passed":
        issues.append(f"[slide-plan] status={plan.get('status')!r}; expected 'passed'")
        return issues  # no point checking individual slides if plan blocked

    slides = plan.get("slides") or []
    if len(slides) != 5:
        issues.append(f"[slide-plan] {len(slides)} slides; expected 5")

    seen_template_ids = []
    for s in slides:
        n = s.get("slide")
        tid = s.get("template_id", "")
        role = s.get("role", "")
        seen_template_ids.append(tid)

        if tid in OPC_BANNED_TEMPLATE_KEYS:
            issues.append(
                f"[slide-plan] slide {n}: banned legacy key '{tid}' "
                "(cutout/illustrated were disabled — see commit 72ff06c)"
            )
        if tid not in KNOWN_OPC_TEMPLATE_IDS:
            issues.append(
                f"[slide-plan] slide {n}: unknown template_id '{tid}' "
                f"(not in KNOWN_OPC_TEMPLATE_IDS)"
            )

        expected_role = EXPECTED_ROLE_FOR_SLIDE.get(n)
        if expected_role and role != expected_role:
            issues.append(
                f"[slide-plan] slide {n}: role '{role}' does not match "
                f"expected role '{expected_role}'"
            )

        if not s.get("production_safe", False):
            fb = s.get("fallback_template_id")
            if not fb or fb not in KNOWN_OPC_TEMPLATE_IDS:
                issues.append(
                    f"[slide-plan] slide {n}: template_id '{tid}' is not "
                    f"production_safe and has no valid fallback_template_id"
                )

    # Sources-slide special rule: there's only one renderer for it today.
    if seen_template_ids and seen_template_ids[-1] != "opc_tip_sources":
        issues.append(
            f"[slide-plan] slide 5: must be opc_tip_sources today "
            f"(got '{seen_template_ids[-1]}'). Only sources renderer wired."
        )

    # Phase 6 — flag cases where the renderer fell back to a tip equivalent for
    # a template that DOES have a production Python builder. This means the
    # standalone failed to render for some reason and the user is seeing a tip
    # instead of the approved design — reviewer must surface this loudly.
    resolved = plan.get("_resolved_slides") or []
    fallbacks = plan.get("_fallbacks_used") or []
    for slide_num, requested, used in fallbacks:
        if requested in OPC_PORTED_STANDALONE_IDS and used != requested:
            issues.append(
                f"[slide-plan] slide {slide_num}: planner picked '{requested}' "
                f"but renderer fell back to '{used}'. Standalone IS ported "
                f"(see OPC_PORTED_STANDALONE_IDS) — this is a bug, not a "
                f"missing-builder case."
            )

    # Phase 6 — flag image-required templates that have no image.
    if resolved:
        media_paths = (content or {}).get("_media_paths") or {}
        slide_imgs  = (media_paths.get("slides") or {}) if isinstance(media_paths, dict) else {}
        cover_img   = (media_paths.get("cover") if isinstance(media_paths, dict) else "") or ""
        cards_imgs  = (media_paths.get("cards") or {}) if isinstance(media_paths, dict) else {}
        for rs in resolved:
            eid = rs.get("effective_id", "")
            slide_n = rs.get("slide")
            if eid in OPC_TEMPLATES_REQUIRING_IMAGE:
                has_img = bool(slide_imgs.get(slide_n) or slide_imgs.get(str(slide_n)) or cover_img)
                if not has_img:
                    issues.append(
                        f"[slide-plan] slide {slide_n}: template '{eid}' requires "
                        f"an image but none provided in media_paths "
                        f"(slides[{slide_n}] or cover)."
                    )
            # Phase 8C — opc_four_card_grid needs 4 distinct images.
            if eid == "opc_four_card_grid":
                present = sum(1 for i in range(1, 5) if cards_imgs.get(i) or cards_imgs.get(str(i)))
                if present < 4:
                    issues.append(
                        f"[slide-plan] slide {slide_n}: opc_four_card_grid needs 4 card images "
                        f"in media_paths['cards'][1..4]; only {present} present."
                    )

    # Phase 8G — for each slide that uses a ported standalone, validate the
    # per-template content dict is populated with real (non-default) values.
    # This is the gate that catches the "renderer works but content is all '—'"
    # failure mode we got bit by in Phase 7.
    for s in slides:
        n = s.get("slide")
        tid = s.get("template_id", "")
        # Use the resolved effective_id when available — that's what actually
        # rendered. If a standalone fell back to a tip, the tip-content gates
        # don't apply (legacy path will catch its own issues).
        eff_id = next((r.get("effective_id") for r in resolved if r.get("slide") == n), tid)
        if eff_id in OPC_STANDALONE_REQUIRED_CONTENT_KEYS:
            issues.extend(check_standalone_content(content, n, eff_id))

    return issues


def check_built_post(result: dict) -> dict:
    """Run all checks on a single built post result dict.
    Returns {post_id, topic, niche, issues: [str], passed: bool}."""
    post_id = result.get("post_id", "unknown")
    topic   = result.get("topic", "")
    niche   = result.get("niche", "")

    all_issues = []

    # 0. Phase 5 — smart slide-plan gates (Phase 4 picker output validation).
    # Runs FIRST so a hallucinated/banned plan blocks the post before any
    # render/PNG/Drive cost. No-op for posts that don't use the planner.
    if niche == "opc":
        all_issues.extend(check_slide_plan(result.get("content", {}) or {}))
        # Phase 10 — sources-vs-claims gate. If $/%/years appear on any
        # slide, the sources list must cite a credible external authority.
        all_issues.extend(check_sources_match_claims(result.get("content", {}) or {}))

    # 1. HTML placeholder check — look for cover.html in version folder (local path)
    # The content_creator already cleaned up work_dir, so we check Drive link heuristically.
    # In local GitHub Actions run, WORK_DIR still exists during this script's execution.
    work_dir_env = os.environ.get("WORK_DIR", "/tmp/content_creator_run")
    html_local = Path(work_dir_env) / post_id / "cover.html"
    if html_local.exists():
        all_issues.extend(check_html_placeholders(str(html_local)))
    else:
        # Try common temp pattern
        for candidate in Path(work_dir_env).glob(f"**/{post_id}/cover.html"):
            all_issues.extend(check_html_placeholders(str(candidate)))
            break
        else:
            # Work dir cleaned up — can't check HTML placeholders locally
            pass  # Drive folder link check would require downloading — skip for now

    # 2. PNG check
    png_dir_local = Path(work_dir_env) / post_id / "png"
    if png_dir_local.exists():
        min_slides = 5 if niche == "opc" else 4
        all_issues.extend(check_png_folder(str(png_dir_local), min_slides))

    # 3. Motion check — skip when pipeline is running with motion disabled
    motion_dir_local = Path(work_dir_env) / post_id / "motion"
    if os.environ.get("MOTION_ENABLED", "0") != "0":
        all_issues.extend(check_motion_folder(str(motion_dir_local)))

    # 4. Provenance check — flag AI-sourced images (real-photo tiers missed)
    prov_path = Path(work_dir_env) / post_id / "resources" / "media_provenance.json"
    _prov_data: dict = {}
    if prov_path.exists():
        try:
            _prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
            all_issues.extend(_check_provenance(_prov_data, topic=topic))
        except Exception as e:
            all_issues.append(f"media_provenance.json read/parse failed: {e}")

    # 4.5. Resource image integrity + vision relevance (local — runs while /tmp exists)
    # Primary path: resources/images/. Fallback: resources/ directly for legacy builds.
    _resources_base = Path(work_dir_env) / post_id / "resources"
    images_dir_local = _resources_base / "images"
    if not images_dir_local.exists() and _resources_base.exists():
        images_dir_local = _resources_base  # legacy layout — images may sit at resources/ root
    if images_dir_local.exists():
        all_issues.extend(check_resource_images_local(str(images_dir_local), _prov_data))

    # 5. Caption check — caption.txt must exist before post can go to Buffer
    caption_path = Path(work_dir_env) / post_id / "caption.txt"
    if not caption_path.exists():
        for candidate in Path(work_dir_env).glob(f"**/{post_id}/caption.txt"):
            caption_path = candidate
            break
    if not caption_path.exists():
        all_issues.append(
            "caption.txt missing — no Instagram caption was generated; "
            "post cannot be scheduled to Buffer (check generate_caption() call in main.py)"
        )

    # 5.5. Placeholder auto-fix (Path A — local WORK_DIR)
    # IMG/STICKER placeholders → Wikimedia fetch → HTML patch → re-render → Drive upload.
    # Runs unconditionally (not gated on FIX_MODE) — placeholders are always broken.
    if not DRY_RUN:
        _ph_html = html_local if html_local.exists() else None
        if not _ph_html:
            for _c in Path(work_dir_env).glob(f"**/{post_id}/cover.html"):
                _ph_html = _c
                break
        if _ph_html and Path(_ph_html).exists():
            _ph_work = Path(_ph_html).parent
            _ph_patched, _ph_fixes, _ph_remaining = _patch_html_placeholders(
                str(_ph_html), _ph_work
            )
            if _ph_fixes:
                all_issues.append(
                    f"[auto-fixed] {len(_ph_fixes)} placeholder(s) resolved via Wikimedia: "
                    + "; ".join(_ph_fixes[:3])
                )
            all_issues.extend(_ph_remaining)
            if _ph_patched:
                _re_ok = _rerender_and_upload(
                    str(_ph_html),
                    str(_ph_work / "png"),
                    result.get("version_folder_id") or result.get("static_folder_id", ""),
                    result.get("motion_folder_id", ""),
                )
                if not _re_ok:
                    all_issues.append(
                        "Placeholder fix: HTML patched but re-render failed — "
                        "PNGs in Drive may still show placeholders"
                    )

    # SH-139/P2 — extract slide_purpose declarations BEFORE coherence scoring so
    # check_text_quality() can pass them to the purpose-aware Coherence scorer (SH-142).
    _purposes = result.get("slide_purposes") or result.get("content", {}).get("slide_purposes")
    # Fallback: when LLM fallback (OpenAI/Gemini) omits slide_purposes from JSON,
    # derive them from the deterministic OPC 5-slide mapping — purposes don't change
    # per-topic, they're structural (hook→cost→teach→apply→sources for every OPC tip).
    if not _purposes and SLIDE_PURPOSE_PILOT and niche == "opc":
        _purposes = [
            {"slide": 1, "purpose": "hook"},
            {"slide": 2, "purpose": "cost"},
            {"slide": 3, "purpose": "teach"},
            {"slide": 4, "purpose": "apply"},
            {"slide": 5, "purpose": "sources"},
        ]
        print(f"  [SH-139] slide_purpose pilot: LLM omitted slide_purposes — using OPC deterministic mapping")
    if _purposes and isinstance(_purposes, list):
        print(f"  [SH-139] slide_purpose pilot active — declared purposes:")
        for entry in _purposes:
            if isinstance(entry, dict):
                idx = entry.get("slide", "?")
                pur = entry.get("purpose", "?")
                print(f"           Slide {idx}: purpose='{pur}'")
        print(f"           (P2: Coherence scorer and Structure agent are now purpose-aware)")

    # 6. Goal 1B — hook strength + copy coherence (Sonnet)
    # P2/SH-142: passes _purposes so Coherence scorer checks purpose fulfillment
    storytelling_scores: dict = {}
    if ANTHROPIC_KEY:
        _html_for_text = html_local if html_local.exists() else None
        if not _html_for_text:
            for _c in Path(work_dir_env).glob(f"**/{post_id}/cover.html"):
                _html_for_text = _c
                break
        if _html_for_text and Path(_html_for_text).exists():
            all_issues.extend(check_text_quality(str(_html_for_text), niche, purposes=_purposes))
            # SH-028: storytelling quality score per slide
            storytelling_scores = score_storytelling(str(_html_for_text), niche)
            if storytelling_scores:
                overall = storytelling_scores.get("overall", 0)
                if overall < 60:
                    all_issues.append(
                        f"[storytelling] Overall quality score {overall}/100 — "
                        f"{storytelling_scores.get('summary', '')[:100]}"
                    )

    # 7. Auto-fix — runs in analyze_and_fix mode when Drive folder ID is available.
    # check_built_post() previously only detected issues; this closes the gap where
    # corrupt/wrong images were reported but never repaired in the normal build path.
    autofix_summary = None
    _drive_link = result.get("version_link") or result.get("static_link", "")
    _folder_id = _extract_drive_id(_drive_link) if _drive_link else ""
    _has_image_issues = any(
        ("[fix_type=regenerate]" in i or "[fix_type=wrong-image]" in i or "[fix_type=corrupt-image]" in i)
        for i in all_issues
    )
    if FIX_MODE == "analyze_and_fix" and _folder_id and _has_image_issues and not DRY_RUN:
        try:
            _drive_svc = _build_drive_service()
            if _drive_svc:
                from auto_fixer import auto_fix_drive_folder
                autofix_summary = auto_fix_drive_folder(
                    _drive_svc,
                    {"id": _folder_id, "name": post_id},
                    niche=niche,
                    dry_run=False,
                )
                fixed_n = autofix_summary.get("fixed", 0)
                if fixed_n:
                    all_issues.append(
                        f"[auto-fix] regenerated {fixed_n} image(s) "
                        f"(niche={niche}, png backup: {autofix_summary.get('png_backup_folder_id', 'none')})"
                    )
        except Exception as e:
            all_issues.append(f"[auto-fix] FAILED — {type(e).__name__}: {e}")

    passed = len(all_issues) == 0
    return {
        "post_id": post_id,
        "topic": topic[:60],
        "niche": niche,
        "issues": all_issues,
        "passed": passed,
        "drive_link": _drive_link,
        "autofix_summary": autofix_summary,
        "storytelling_scores": storytelling_scores,
    }


# ─── Email ────────────────────────────────────────────────────────────────────

def send_review_email(failed_posts: list[dict], all_posts: list[dict]):
    """Send review report via send_email.yml workflow."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total = len(all_posts)
    n_fail = len(failed_posts)
    n_pass = total - n_fail

    n_autofixed = sum(
        (p.get("autofix_summary") or {}).get("fixed", 0) for p in all_posts
    )
    mode_tag = f" [{FIX_MODE}]" if FIX_MODE != "analyze_only" else ""
    autofix_tag = f" — auto-fixed {n_autofixed}" if n_autofixed else ""
    subject = (
        f"[carousel-reviewer]{mode_tag} {n_pass}/{total} passed — "
        f"{n_fail} issue(s){autofix_tag} — {now}"
    )

    lines = [
        f"CAROUSEL REVIEW REPORT — {now}",
        f"Mode: {FIX_MODE} | Total: {total} | Passed: {n_pass} | "
        f"Issues: {n_fail} | Auto-fixed: {n_autofixed}",
        "",
    ]

    for p in all_posts:
        status = "✅ PASS" if p["passed"] else "❌ ISSUES"
        lines.append(f"{status}  [{p['niche'].upper()}] {p['topic']}")
        lines.append(f"       Drive: {p['drive_link']}")
        for issue in p["issues"]:
            lines.append(f"       ⚠  {issue}")

        # Append text edit log if Goal 1B ran.
        afs = p.get("autofix_summary")
        if afs and afs.get("text_edits_applied"):
            n_txt = afs["text_edits_applied"]
            lines.append(f"       ── text edits applied: {n_txt} ──")
            for ed in (afs.get("text_edit_log") or []):
                if ed.get("applied"):
                    lines.append(
                        f"         · [{ed.get('severity','?')}] {ed.get('slide','?')}: "
                        f"'{str(ed.get('original',''))[:40]}' → '{str(ed.get('suggested',''))[:40]}'"
                    )
        # Storytelling scores (SH-028)
        ss = p.get("storytelling_scores") or {}
        if ss:
            overall = ss.get("overall", "?")
            lines.append(f"       ── storytelling {overall}/100: {ss.get('summary','')[:80]} ──")
            for s in (ss.get("slide_scores") or [])[:8]:
                lines.append(
                    f"         · slide {s.get('slide','?')}: {s.get('score','?')}/100 — {s.get('reason','')[:60]}"
                )

        # Append before/after image change log if Goal 1A ran.
        if afs and afs.get("details"):
            lines.append("       ── auto-fix change log ──")
            for d in afs["details"]:
                slot = d.get("slot", "?")
                act = d.get("action", "")
                if act == "fixed":
                    lines.append(
                        f"         · {slot}: {d.get('old_provider','—')} "
                        f"→ {d.get('new_provider','')} "
                        f"({d.get('new_source_type','')}) — {d.get('filename','')}"
                    )
                elif act == "would_fix":
                    lines.append(f"         · {slot}: dry-run, would refetch")
                else:
                    lines.append(f"         · {slot}: {act}")
            if afs.get("png_backup_folder_id"):
                lines.append(
                    f"       PNG backup: png_pre_fix_*/ id={afs['png_backup_folder_id']}"
                )
        lines.append("")

    lines += [
        "─" * 60,
        f"FIX_MODE was '{FIX_MODE}'. Set FIX_MODE=analyze_and_fix to auto-repair "
        f"[fix_type=regenerate] issues. Auto-fix backs up png/ before any change.",
        "Reply to this email with feedback and the next run will re-process with "
        "your notes (Goal 3B — pending).",
        "Workflow: https://github.com/priihigashi/oak-park-ai-hub/actions/workflows/content_creator.yml",
    ]

    body = "\n".join(lines)
    html_rows = []
    for p in all_posts:
        status = "PASS" if p["passed"] else "ISSUES"
        issues_html = "<br/>".join([f"• {i}" for i in p["issues"]]) or "None"
        html_rows.append(
            "<tr>"
            f"<td style='padding:8px;color:#ddd'>{status}</td>"
            f"<td style='padding:8px;color:#ddd'>{p['topic']}</td>"
            f"<td style='padding:8px'><a style='color:#CBCC10' href='{p['drive_link']}'>open</a></td>"
            f"<td style='padding:8px;color:#aaa;font-size:12px;line-height:1.4'>{issues_html}</td>"
            "</tr>"
        )
    html_body = (
        "<html><body style='background:#0a0a0a;padding:20px;font-family:Arial,sans-serif;'>"
        f"<h2 style='color:#CBCC10'>Carousel Reviewer ({FIX_MODE})</h2>"
        f"<p style='color:#ccc'>Total {total} | Passed {n_pass} | Issues {n_fail} | Auto-fixed {n_autofixed}</p>"
        "<table style='border-collapse:collapse;width:100%;max-width:1200px;'>"
        "<tr><th style='padding:8px;color:#eee;text-align:left'>Status</th>"
        "<th style='padding:8px;color:#eee;text-align:left'>Carousel</th>"
        "<th style='padding:8px;color:#eee;text-align:left'>Folder</th>"
        "<th style='padding:8px;color:#eee;text-align:left'>Issues</th></tr>"
        + "".join(html_rows) +
        "</table></body></html>"
    )

    if DRY_RUN:
        print("\n[DRY RUN] Would send email:")
        print(f"Subject: {subject}")
        print(body)
        return

    try:
        subprocess.run(
            [
                "gh", "workflow", "run", "send_email.yml",
                "--repo", "priihigashi/oak-park-ai-hub",
                "-f", f"to={ALERT_EMAIL}",
                "-f", f"subject={subject}",
                "-f", f"body={body}",
                "-f", f"html_body={html_body}",
            ],
            check=False, timeout=30,
        )
        print(f"  Review report emailed to {ALERT_EMAIL}")
    except Exception as e:
        print(f"  Review email failed (non-fatal): {e}")
        print(body)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n[carousel-reviewer] Starting post-build review...")

    # Parse results from env var or stdin
    results_raw = RUN_RESULTS_JSON
    try:
        results = json.loads(results_raw) if results_raw.strip() else []
    except json.JSONDecodeError:
        results = []

    reviewed = []
    if results:
        print(f"  Reviewing {len(results)} post(s) from CONTENT_CREATOR_RUN...")
        reviewed.extend(check_built_post(r) for r in results)

    manual_targets = [x.strip() for x in REVIEW_DRIVE_FOLDERS.split(",") if x.strip()]
    manual_inputs = []
    for raw in manual_targets:
        fid = _extract_drive_id(raw)
        if fid:
            manual_inputs.append((raw, fid))
    if manual_inputs:
        print(f"  Reviewing {len(manual_inputs)} existing Drive folder(s) on demand...")
        drive = _build_drive_service()
        if not drive:
            print("  SHEETS_TOKEN missing — cannot review Drive folders")
        else:
            seen_resolved = set()
            for raw_ref, fid in manual_inputs:
                try:
                    result = check_drive_folder(fid, drive, input_ref=raw_ref)
                    rid = result.get("resolved_id", "")
                    if rid and rid in seen_resolved:
                        print(f"  ↪ Skipping duplicate target (same resolved folder): {raw_ref} -> {rid}")
                        continue
                    if rid:
                        seen_resolved.add(rid)
                    reviewed.append(result)
                except Exception as e:
                    reviewed.append({
                        "post_id": fid,
                        "topic": fid,
                        "niche": "manual",
                        "issues": [f"Drive review failed: {e}"],
                        "passed": False,
                        "drive_link": f"https://drive.google.com/drive/folders/{fid}",
                        "input_ref": raw_ref,
                        "resolved_id": fid,
                        "original_id": fid,
                    })

    if not reviewed:
        print("  No results to review (CONTENT_CREATOR_RUN and REVIEW_DRIVE_FOLDERS empty) — exiting")
        return

    passed = [r for r in reviewed if r["passed"]]
    failed = [r for r in reviewed if not r["passed"]]

    for r in reviewed:
        icon = "✅" if r["passed"] else "❌"
        print(f"  {icon} [{r['niche']}] {r['topic']}")
        if r.get("niche") == "manual":
            print(
                f"       input: {r.get('input_ref','')} | resolved: {r.get('resolved_id', r.get('post_id',''))}"
            )
        for issue in r["issues"]:
            print(f"       ⚠  {issue}")

    print(f"\n  Summary: {len(passed)}/{len(reviewed)} passed")

    # Always send report (even if all pass — confirms reviewer ran)
    send_review_email(failed, reviewed)

    if failed and REVIEW_STRICT:
        print("[carousel-reviewer] Strict mode: failing workflow because review issues were found.")
        sys.exit(1)

    print("[carousel-reviewer] Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"[carousel-reviewer] Uncaught exception: {e}")
        print(traceback.format_exc())
        sys.exit(1 if REVIEW_STRICT else 0)
