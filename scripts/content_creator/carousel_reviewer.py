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

# FIX_MODE: "analyze_only" (default) = detect + email
#          "analyze_and_fix"        = detect, auto-fix [fix_type=regenerate] issues,
#                                     then email before/after change log
FIX_MODE = os.environ.get("FIX_MODE", "analyze_only").strip().lower()

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


# ─── Checks ──────────────────────────────────────────────────────────────────

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

    # Label-leak: structural labels that Haiku sometimes emits verbatim into slide copy
    _LABEL_PATTERNS = [
        r'\bSlide\s+\d+\s*[:\-]',          # "Slide 1:", "Slide 2 -"
        r'\b(?:Hook|CTA|Body|Title|Intro|Outro|Headline|Subhead|Caption)\s*:',  # field names
        r'\[INSERT\b', r'\[ADD\b', r'\[REPLACE\b', r'\[PUT\b',  # imperative placeholders
        r'\[YOUR\s+\w', r'\[WRITE\b',       # authoring reminders
        r'\bNUM_\w+\b', r'\bDATE_\w+\b',   # token stubs
    ]
    label_hits = []
    for pat in _LABEL_PATTERNS:
        matches = re.findall(pat, html, re.IGNORECASE)
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
    for idx, stat_txt in enumerate(re.findall(r'<[^>]+class="[^"]*stat-big[^"]*"[^>]*>([\s\S]*?)</[^>]+>', html), start=1):
        clean = re.sub(r"<[^>]+>", "", stat_txt).strip()
        if len(clean) > 12:
            issues.append(f"Stat clipping risk: stat-big {idx} is {len(clean)} chars ('{clean[:24]}').")
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

    # OPC-specific quality checks (prevent text-only middle slides)
    if "Tip of the Week · Oak Park Construction" in html:
        slot_count = len(re.findall(r'class="context-img-slot"', html))
        if slot_count < 3:
            issues.append(
                f"OPC layout issue: expected >=3 context image slots on slides 2-4, found {slot_count}"
            )

        img_count = len(re.findall(r'<div class="context-img-slot"[^>]*>\s*<img ', html))
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
        if ".v2.slide-cover .headline" in html:
            m_v2 = re.search(r"\.v2\.slide-cover\s+\.headline[^{]*\{([\s\S]*?)\}", html)
            if m_v2 and "#0A0A0A" in m_v2.group(1):
                issues.append("OPC readability miss: v2 cover headline uses near-black color over dark overlay.")

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


def _check_provenance(prov: dict) -> list[str]:
    """Read media_provenance.json dict and flag AI-sourced images.

    Per IMAGE_QUALITY_RULES.md:
      - Any slide with source_type=="ai" means all real-photo tiers (Wikimedia/Pexels/Pixabay) missed.
        These are flagged with [fix_type=regenerate] — the fix is always to improve the query and re-fetch.
      - Cover with source_type=="ai" AND subject_type=="person" is a CRITICAL violation (editorial rule).
    """
    issues = []

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
            "model": "claude-haiku-4-5-20251001",
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
    if html_file:
        try:
            html_text = _download_drive_text(drive, html_file["id"])
            tmp = Path("/tmp") / f"review_{folder_id}.html"
            tmp.write_text(html_text, encoding="utf-8")
            issues.extend(check_html_placeholders(str(tmp)))
        except Exception as e:
            issues.append(f"Could not inspect cover.html: {e}")
    else:
        issues.append("cover.html missing in version folder")

    png_folder_id = _find_folder_id(drive, folder_id, "png")
    if not png_folder_id:
        issues.append("PNG folder missing")
    else:
        pngs = [f for f in _list_children(drive, png_folder_id) if f.get("name", "").lower().endswith(".png")]
        if len(pngs) < 5:
            issues.append(f"Too few PNGs: {len(pngs)} found, expected ≥ 5")
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
                issues.extend(_check_provenance(prov))
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
    """Call claude-sonnet-4-6, parse a 1-3 score from the reply.
    Returns (score, reason_text). score=0 means API error/skip."""
    if not ANTHROPIC_KEY:
        return 0, "no API key"
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
        m = re.search(r"\b([123])\b", text)
        return (int(m.group(1)) if m else 1), text
    except Exception as e:
        return 0, f"error: {e}"


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


def _score_copy_coherence(headlines: list[str]) -> tuple[int, str]:
    """Check B: score narrative arc of slide headlines 1-3 via Sonnet."""
    if len(headlines) < 2:
        return 0, "too few headlines to score"
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines) if h)
    if not numbered.strip():
        return 0, "no headline text"
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


def check_text_quality(html_path: str, niche: str) -> list[str]:
    """Goal 1B: run hook strength + copy coherence checks via Claude Sonnet.
    Returns issue strings; tokens match _TEXT_ISSUE_TOKENS auto-fix gate."""
    issues = []
    if not ANTHROPIC_KEY:
        return issues
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues

    # Extract cover headline + subhead (first match in document)
    m_hl = re.search(r'class="[^"]*headline[^"]*"[^>]*>([\s\S]*?)</(?:div|p|h[1-6])', html)
    m_sh = re.search(r'class="[^"]*subhead[^"]*"[^>]*>([\s\S]*?)</(?:div|p|span|h[1-6])', html)
    headline = re.sub(r"<[^>]+>", "", m_hl.group(1)).strip() if m_hl else ""
    subhead  = re.sub(r"<[^>]+>", "", m_sh.group(1)).strip() if m_sh else ""

    # Extract all headlines in document order for coherence check
    all_headlines = [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(r'class="[^"]*headline[^"]*"[^>]*>([\s\S]*?)</(?:div|p|h[1-6])', html)
    ]
    all_headlines = [h for h in all_headlines if h]

    # Check A — hook strength
    hook_score, hook_reason = _score_hook_strength(headline, subhead, niche)
    print(f"  [1B] Hook {hook_score}/3 — {hook_reason[:80]}")
    if 0 < hook_score < 2:
        issues.append(f"[hook weak] Cover hook scored {hook_score}/3 — {hook_reason[:120]}")

    # Check B — copy coherence
    coh_score, coh_reason = _score_copy_coherence(all_headlines)
    print(f"  [1B] Coherence {coh_score}/3 — {coh_reason[:80]}")
    if 0 < coh_score < 2:
        issues.append(f"[copy incoherent] Narrative scored {coh_score}/3 — {coh_reason[:120]}")

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

    # Extract per-slide headline text
    slide_blocks = re.findall(
        r'<div class="slide[^"]*"[^>]*>([\s\S]*?)(?=<div class="slide|\Z)', html
    )
    slide_texts = []
    for i, block in enumerate(slide_blocks, start=1):
        text = re.sub(r"<[^>]+>", " ", block)
        text = re.sub(r"\s+", " ", text).strip()[:200]
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
            "model": "claude-haiku-4-5-20251001",
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
        try:
            raw_resp = urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as http_err:
            status = http_err.code
            body = ""
            try:
                body = http_err.read().decode(errors="ignore")
            except Exception:
                pass
            if status in (529, 529) or "overloaded" in body.lower() or "credit" in body.lower():
                print(f"  [SH-028] ⚠ WARN: Anthropic credits/capacity issue (HTTP {status}) — storytelling score skipped")
            else:
                print(f"  [SH-028] Storytelling score HTTP error {status} (non-fatal): {body[:120]}")
            return {}
        resp = json.loads(raw_resp)
        raw = resp["content"][0]["text"].strip()
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


def check_built_post(result: dict) -> dict:
    """Run all checks on a single built post result dict.
    Returns {post_id, topic, niche, issues: [str], passed: bool}."""
    post_id = result.get("post_id", "unknown")
    topic   = result.get("topic", "")
    niche   = result.get("niche", "")

    all_issues = []

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

    # 3. Motion check
    motion_dir_local = Path(work_dir_env) / post_id / "motion"
    all_issues.extend(check_motion_folder(str(motion_dir_local)))

    # 4. Provenance check — flag AI-sourced images (real-photo tiers missed)
    prov_path = Path(work_dir_env) / post_id / "resources" / "media_provenance.json"
    _prov_data: dict = {}
    if prov_path.exists():
        try:
            _prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
            all_issues.extend(_check_provenance(_prov_data))
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

    # 6. Goal 1B — hook strength + copy coherence (Sonnet)
    storytelling_scores: dict = {}
    if ANTHROPIC_KEY:
        _html_for_text = html_local if html_local.exists() else None
        if not _html_for_text:
            for _c in Path(work_dir_env).glob(f"**/{post_id}/cover.html"):
                _html_for_text = _c
                break
        if _html_for_text and Path(_html_for_text).exists():
            all_issues.extend(check_text_quality(str(_html_for_text), niche))
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

    # Always exit 0 — reviewer is informational, not blocking
    print("[carousel-reviewer] Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"[carousel-reviewer] Uncaught exception: {e}")
        print(traceback.format_exc())
        sys.exit(0)  # Always exit 0 — reviewer is informational, not blocking
