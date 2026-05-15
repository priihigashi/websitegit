#!/usr/bin/env python3
"""
carousel_builder.py — Generates carousel HTML from template + topic, renders PNGs.
Uses Claude Haiku for content generation, Playwright for rendering.
Also generates Instagram caption following Priscila's copy rules.
"""
import base64, datetime, gzip, json, os, re, subprocess, sys, time, urllib.request, urllib.parse
from pathlib import Path
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "capture"))
try:
    from _llm_fallback import llm_text as _llm_text_cascade
except Exception:
    _llm_text_cascade = None

ANTHROPIC_KEY  = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
OPENAI_KEY     = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "")
PEXELS_KEY     = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY    = os.environ.get("PIXABAY_API_KEY", "")
REPLICATE_KEY  = os.environ.get("PRI_OP_REPLICATE_API_KEY", "")
INFSH_KEY      = os.environ.get("PRI_OP_INFSH_API_KEY", "")

# SH-138 — slide_purpose pilot (doc 1BDg9ORggVsWH-WQPBx4iQnYNu2UA5vHWcuNRkXCY5v8 v3 FINAL).
# Default OFF on cron. When SLIDE_PURPOSE_PILOT=1, generation prompts emit slide_purpose
# tags so reviewer/auditor can audit each slide against its declared narrative job.
# Pilot keeps slide count fixed — no dynamic counts during pilot.
# OPC purposes:  hook | cost | teach | apply | sources
# News purposes: claim | number | evidence | opposition | implication | sources
# 5-slide News compression (pilot-safe per v3-final doc): implication folded into slide 4.
SLIDE_PURPOSE_PILOT = os.environ.get("SLIDE_PURPOSE_PILOT", "0") == "1"

# SH-147: generation trace — populated by _claude_with_fallback() on each success.
# main.py reads this after generate_carousel_content() to attach to the result dict.
_gen_trace: dict = {"provider": "unknown", "model": "unknown", "fallback_used": False}
SLIDE_PURPOSE_OPC_BY_INDEX = {
    1: "hook", 2: "cost", 3: "teach", 4: "apply", 5: "sources",
}
SLIDE_PURPOSE_NEWS_5SLIDE_BY_INDEX = {
    1: "claim", 2: "number", 3: "evidence",
    4: "opposition+implication",  # 5-slide compression — opposition slide ends with implication line
    5: "sources",
}
SLIDE_PURPOSE_NEWS_6SLIDE_BY_INDEX = {
    1: "claim", 2: "number", 3: "evidence", 4: "opposition", 5: "implication", 6: "sources",
}


def _local_font_face_css() -> str:
    """Return @font-face declarations with base64-encoded WOFF2 fonts embedded inline.
    Inline data URIs bypass Chromium's file:// cross-directory restriction entirely —
    no network needed, no path-matching required, works in any headless context."""
    import base64
    fonts_dir = Path(__file__).parent / "fonts"
    anton = fonts_dir / "Anton-Regular.woff2"
    rc    = fonts_dir / "RobotoCondensed-Regular.woff2"
    rc_b  = fonts_dir / "RobotoCondensed-Bold.woff2"
    if not anton.exists() or not rc.exists():
        print("  [font] WARNING: bundled WOFF2 not found — fonts will fall back to system")
        return ""

    def _b64(p: Path) -> str:
        return "data:font/woff2;base64," + base64.b64encode(p.read_bytes()).decode()

    _rc_bold_uri = _b64(rc_b) if rc_b.exists() else _b64(rc)
    print(f"  [font] Embedding Anton + Roboto Condensed (Regular/Bold) as inline base64 — Google Fonts bypassed")
    return f"""@font-face {{
  font-family: 'Anton'; font-weight: 400; font-style: normal;
  src: url('{_b64(anton)}') format('woff2');
}}
@font-face {{
  font-family: 'Roboto Condensed'; font-weight: 300; font-style: normal;
  src: url('{_b64(rc)}') format('woff2');
}}
@font-face {{
  font-family: 'Roboto Condensed'; font-weight: 400; font-style: normal;
  src: url('{_b64(rc)}') format('woff2');
}}
@font-face {{
  font-family: 'Roboto Condensed'; font-weight: 700; font-style: normal;
  src: url('{_rc_bold_uri}') format('woff2');
}}
"""


def _slide_purpose_block(niche: str, n_slides: int) -> str:
    """SH-138: build a prompt fragment that asks the model to emit slide_purpose
    per slide. Returns "" when pilot disabled so cron behavior is unchanged.
    """
    if not SLIDE_PURPOSE_PILOT:
        return ""
    if niche == "opc":
        mapping = SLIDE_PURPOSE_OPC_BY_INDEX
        purpose_doc = (
            "OPC narrative spine (purposes IN ORDER): hook | cost | teach | apply | sources.\n"
            "  - hook  = stop the scroll. Surprising fact / costly mistake. Noun phrase, no question.\n"
            "  - cost  = the dollar/time/regret number that grounds the hook. Concrete, sourced.\n"
            "  - teach = explain ONE concept simply. The 'explain one thing' rule.\n"
            "  - apply = how a homeowner applies it (NOT what OPC does — what THEY do).\n"
            "  - sources = citations + soft 'save this for your reno' CTA."
        )
    else:
        # Brazil/USA News
        if n_slides <= 5:
            mapping = SLIDE_PURPOSE_NEWS_5SLIDE_BY_INDEX
            purpose_doc = (
                "News narrative spine (purposes IN ORDER, 5-slide compression): claim | number | evidence | opposition+implication | sources.\n"
                "  - claim    = cover_claim verbatim. The provocative statement.\n"
                "  - number   = the size of the claim. Visual receipts.\n"
                "  - evidence = primary sources (gov website, official doc).\n"
                "  - opposition+implication = cross-partisan agreement on the same fact, ENDING with one neutral implication line ('what this means for you').\n"
                "  - sources  = full citations."
            )
        else:
            mapping = SLIDE_PURPOSE_NEWS_6SLIDE_BY_INDEX
            purpose_doc = (
                "News narrative spine (purposes IN ORDER): claim | number | evidence | opposition | implication | sources.\n"
                "  - claim       = cover_claim verbatim. The provocative statement.\n"
                "  - number      = the size of the claim. Visual receipts.\n"
                "  - evidence    = primary sources (gov website, official doc).\n"
                "  - opposition  = cross-partisan agreement on the same fact.\n"
                "  - implication = neutral 'what this means for you' line. Never editorial.\n"
                "  - sources     = full citations."
            )
    by_index = "\n".join(
        f"  Slide {i}: {mapping.get(i, 'middle')}" for i in range(1, n_slides + 1)
    )
    return (
        "\n=== SH-138 SLIDE PURPOSE PILOT (advisory, non-blocking) ===\n"
        f"{purpose_doc}\n"
        f"For this {n_slides}-slide carousel, each slide MUST fulfill its declared purpose AND build on the previous slide.\n"
        f"Required slide_purpose by slide index:\n{by_index}\n"
        "Return an additional top-level field `slide_purposes` in your JSON output:\n"
        '  "slide_purposes": [{"slide": 1, "purpose": "hook"}, {"slide": 2, "purpose": "cost"}, ...]\n'
        "Each slide's content MUST visibly serve its purpose. Reviewer will audit per-slide.\n"
        "=== END SLIDE PURPOSE PILOT ===\n"
    )


# SH-041: DALL-E opt-in guard — must be explicitly set to activate (OPC always off regardless)
_USE_DALLE     = os.environ.get("USE_DALLE", "").lower() in ("1", "true", "yes")
APIFY_KEY      = os.environ.get("APIFY_API_KEY", "")

# SH-055: configurable slide safety margin — override via SLIDE_INSET_PX env var
# Default 108px matches existing OPC templates; lower values reduce safe-zone trimming
SLIDE_INSET_PX: int = int(os.environ.get("SLIDE_INSET_PX", "108"))

# Shared image generation modules (image_providers + prompt_builder)
try:
    import sys as _sys
    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from image_providers import generate_ai_image as _gen_ai_image, make_filename as _make_img_filename, log_failure as _log_failure
    from prompt_builder import build_image_prompt as _build_img_prompt
    _IMAGE_PROVIDERS_AVAILABLE = True
except Exception as _ip_err:
    _IMAGE_PROVIDERS_AVAILABLE = False
    def _log_failure(stage, error):
        print(f"  ❌ [{stage}]: {error}")
    print(f"  Warning: image_providers/prompt_builder not loaded ({_ip_err}) — using legacy cascade")

try:
    from image_library import (
        search_library as _search_library,
        enhance_library_image as _enhance_library_image,
        mark_used as _mark_library_used,
    )
    _IMAGE_LIBRARY_AVAILABLE = True
except Exception:
    _IMAGE_LIBRARY_AVAILABLE = False

try:
    from vision_validator import validate_image as _vision_validate
    _VISION_AVAILABLE = True
except Exception as _vv_err:
    _VISION_AVAILABLE = False
    def _vision_validate(_p, _q):
        return True, "skipped (vision_validator not loaded)"
    print(f"  Warning: vision_validator not loaded ({_vv_err})")


def _resolve_local_image_path(path, work_dir=None):
    """Resolve carousel-relative image paths before validation.

    Stock/image-provider helpers return paths like resources/images/foo.jpg
    while the files live under the per-run carousel work_dir. Catalog downloads
    already return absolute paths. Keep both shapes working.
    """
    from pathlib import Path as _P

    p = _P(str(path))
    if p.exists():
        return p
    if work_dir and not p.is_absolute():
        candidate = _P(str(work_dir)) / p
        if candidate.exists():
            return candidate
    return p


def _is_valid_image_file(path, work_dir=None) -> bool:
    """Phase 3: verify a generated/downloaded image is real before trusting it.

    Returns True only if file exists, has non-trivial size, and Pillow can
    open + verify it. Used to gate Vision acceptance so corrupt/missing AI
    output (Seedream/NB2 'success' that wrote nothing) doesn't silently
    become an empty slot.
    """
    if not path:
        return False
    try:
        p = _resolve_local_image_path(path, work_dir=work_dir)
        if not p.exists() or p.stat().st_size < 15_000:
            return False
    except Exception:
        return False
    try:
        from PIL import Image  # type: ignore
        with Image.open(str(p)) as im:
            im.verify()
        return True
    except Exception:
        return False


def _vision_accept(local_path, query, label, *, source_url="", work_dir=None, opc_strict=False):
    """Return True if Vision says image matches query. Logs the verdict.
    Empty path or empty query short-circuits to True so we never block on
    missing inputs.
    SH-056: rejects images from known AI-art domains (checks source_url when available).
    SH-040: also applies URL heuristic from photo_matcher (watermark domains, tiny imgs).
    Phase 3: also rejects when the file does not exist or is unreadable —
    prevents 'Vision OK skipped (file not found)' from silently passing."""
    if not local_path or not query:
        return True
    resolved_path = _resolve_local_image_path(local_path, work_dir=work_dir)
    # Phase 3: hard pre-check — file must be real on disk.
    if not _is_valid_image_file(resolved_path):
        print(f"  Vision REJECT ({label}): file missing/corrupt/too small — {str(local_path)[-80:]}")
        return False
    # SH-056: check original source URL for AI-art domains first; fall back to local path
    url_to_check = (
        source_url
        or _fetch_url_cache.get(str(resolved_path))
        or _fetch_url_cache.get(str(local_path))
        or str(resolved_path)
    )
    if _is_ai_art_url(url_to_check):
        print(f"  Vision REJECT ({label}): AI-art domain URL — {url_to_check[:80]}")
        try:
            __import__("os").unlink(resolved_path)
        except Exception:
            pass
        return False
    # SH-040: apply photo_matcher URL heuristic (watermark/tiny pattern check)
    try:
        from photo_matcher import _url_looks_watermarked_or_tiny as _pm_url_check  # type: ignore
        if _pm_url_check(url_to_check):
            print(f"  Vision REJECT ({label}): photo_matcher URL heuristic reject — {url_to_check[:80]}")
            try:
                __import__("os").unlink(resolved_path)
            except Exception:
                pass
            return False
    except Exception:
        pass
    try:
        ok, reason = _vision_validate(str(resolved_path), query, opc_strict=opc_strict)
        if ok:
            print(f"  Vision OK ({label}): {reason[:120]}")
        else:
            print(f"  Vision REJECT ({label}): {reason[:120]}")
            try:
                __import__("os").unlink(resolved_path)
            except Exception:
                pass
        return ok
    except Exception as e:
        print(f"  Vision check error ({label}, non-fatal): {e}")
        return True


def _extract_comparison_pair_safe(topic: str, brief: str = ""):
    """Return {'left','right'} for explicit X-vs-Y OPC topics, else None."""
    try:
        from opc_template_chooser import extract_comparison_pair  # type: ignore
        return extract_comparison_pair(topic, brief)
    except Exception:
        text = re.sub(r"\s+", " ", f"{topic or ''} {brief or ''}")
        m = re.search(
            r"(?P<left>[A-Za-z0-9][A-Za-z0-9 &/\-]{1,60}?)\s+(?:vs\.?|versus)\s+"
            r"(?P<right>[A-Za-z0-9][A-Za-z0-9 &/\-]{1,60}?)(?:[:?!.—-]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return None
        left = re.sub(r"\s+", " ", m.group("left")).strip(" -—:;,.?!\"'")
        right = re.sub(r"\b(which|wins?|winner|better|best)\b.*$", "", m.group("right"), flags=re.IGNORECASE)
        right = re.sub(r"\s+", " ", right).strip(" -—:;,.?!\"'")
        if left and right and left.lower() != right.lower():
            return {"left": left, "right": right}
    return None


def _comparison_context_word(topic: str) -> str:
    low = (topic or "").lower()
    for word in ("driveway", "patio", "walkway", "pool deck", "foundation", "slab", "wall", "floor", "roof"):
        if word in low:
            return word
    return "residential project"


def _comparison_media_queries(pair: dict, topic: str) -> list[str]:
    left = pair.get("left", "").strip()
    right = pair.get("right", "").strip()
    ctx = _comparison_context_word(topic)
    return [
        f"{left} {ctx} residential south florida",
        f"{right} {ctx} residential south florida",
        f"{left} installation detail residential construction",
        f"{right} installation detail residential construction",
    ]


def _entity_in_text(entity: str, text: str) -> bool:
    tokens = [t for t in re.findall(r"[a-z0-9]{3,}", (entity or "").lower()) if t not in {"the", "and", "with"}]
    hay = (text or "").lower()
    return any(re.search(rf"\b{re.escape(t)}s?\b", hay) for t in tokens)


def _comparison_prompt_block(topic: str, brief: str = "") -> str:
    pair = _extract_comparison_pair_safe(topic, brief)
    if not pair:
        return ""
    left = pair["left"]
    right = pair["right"]
    return f"""

TOPIC-LEVEL COMPARISON CONTRACT (NON-NEGOTIABLE):
- This is a comparison between "{left}" and "{right}". Treat them as a PAIR for the whole carousel.
- Do NOT let one side become the whole story. Every explanatory slide must name or clearly refer to BOTH sides.
- Cover/search visuals must include both sides or a true side-by-side comparison.
- Slide 2 must frame the tradeoff between {left} and {right}, not profile only one material.
- Slide 3 comparison cards must use one consistent frame: either every card compares both sides, or every title declares a winner by criterion.
- Media queries must be balanced: at least one query for {left}, at least one query for {right}, and no repeated one-sided visual lane.
- Bad output example: headline/subhead/cards/images all about "{right}" with "{left}" only appearing in the title. That fails the assignment.
"""


def enforce_opc_comparison_parity(content: dict, topic: str, brief: str = "") -> dict:
    """Attach and enforce a lightweight comparison contract before media fetch."""
    if not isinstance(content, dict):
        return content
    pair = content.get("_comparison_pair") or _extract_comparison_pair_safe(topic, brief)
    if not pair:
        return content
    left = str(pair.get("left", "")).strip()
    right = str(pair.get("right", "")).strip()
    if not left or not right:
        return content

    content["_comparison_pair"] = {"left": left, "right": right}
    cover_query = f"{left} and {right} {_comparison_context_word(topic)} residential comparison south florida"

    cover_visual = content.setdefault("cover_visual", {})
    if isinstance(cover_visual, dict):
        option_a = cover_visual.setdefault("option_a", {})
        if isinstance(option_a, dict):
            existing = str(option_a.get("search_query", ""))
            if not (_entity_in_text(left, existing) and _entity_in_text(right, existing)):
                option_a["search_query"] = cover_query
        option_b = cover_visual.setdefault("option_b", {})
        if isinstance(option_b, dict):
            existing_prompt = str(option_b.get("prompt", ""))
            if not (_entity_in_text(left, existing_prompt) and _entity_in_text(right, existing_prompt)):
                option_b["prompt"] = (
                    f"Photorealistic editorial split-view of {left} and {right} "
                    f"on a {_comparison_context_word(topic)}, South Florida residential construction, no text."
                )

    queries = _comparison_media_queries({"left": left, "right": right}, topic)
    slides = content.get("slides") or []
    if isinstance(slides, list):
        for idx, slide in enumerate(slides[:3]):
            if not isinstance(slide, dict):
                continue
            q = queries[idx if idx < len(queries) else 0]
            alt = q.replace("south florida", "").replace("residential construction", "residential")
            existing_q = str(slide.get("context_image_query", ""))
            if idx == 0 or not (_entity_in_text(left, existing_q) or _entity_in_text(right, existing_q)):
                slide["context_image_query"] = q if idx else cover_query
            existing_alt = str(slide.get("context_image_query_alt", ""))
            if idx == 0 or not (_entity_in_text(left, existing_alt) or _entity_in_text(right, existing_alt)):
                slide["context_image_query_alt"] = f"{left} {right} {_comparison_context_word(topic)}" if idx == 0 else alt

    fcg = content.get("opc_four_card_grid")
    if isinstance(fcg, dict):
        # The LLM can technically mention both sides while still producing
        # vague stock searches like "concrete slab" that return unrelated
        # industrial photos. For comparisons, make the visual contract
        # deterministic: two left-side queries and two right-side queries.
        fcg["card_image_queries"] = queries

    for tid in ("opc_base", "opc_duotone", "opc_progress_media"):
        nested = content.get(tid)
        if isinstance(nested, dict):
            q = str(nested.get("image_query", ""))
            if not (_entity_in_text(left, q) and _entity_in_text(right, q)):
                nested["image_query"] = cover_query
    return content


# ── Pre-render contract gate (SH-146) ─────────────────────────────────────────
# Validates that every selected OPC template has its required fields populated.
# If fields are missing, triggers a targeted LLM repair pass (fills only blanks).
# This prevents the 'headline' KeyError class of crashes and ensures weak/empty
# slides are caught before Playwright tries to render them.

_OPC_TIP_REQUIRED: dict[str, list[str]] = {
    "opc_tip_cover":    ["headline", "subhead"],
    "opc_tip_stat":     ["slide2_stat", "slide2_label"],
    "opc_tip_list":     ["slide3_items"],
    "opc_tip_explainer":["slide4_headline", "slide4_body"],
    "opc_tip_sources":  ["sources"],
}
_OPC_STANDALONE_REQUIRED: dict[str, list[str]] = {
    "opc_material_profile": ["headline_main", "headline_italic", "decision_factors"],
    "opc_four_card_grid":   ["card_titles", "card_copies"],
    "opc_item_spotlight":   ["headline_main", "fact_1_title", "fact_1_desc"],
    "opc_statement":        ["quote_body", "attribution"],
    "opc_progress_media":   ["title_main", "caption_pills"],
    "opc_duotone":          ["claim_strong", "quote_text"],
    "opc_base":             ["headline_main", "headline_italic"],
}


def _get_field_value(content: dict, template_id: str, field: str):
    """Return the field value from the nested template block (standalone) or
    the top-level content dict (tip components)."""
    if template_id in _OPC_STANDALONE_REQUIRED:
        block = content.get(template_id) or {}
        return block.get(field)
    return content.get(field)


def validate_opc_template_contract(content: dict, plan: dict) -> list[str]:
    """Return list of '<template_id>.<field>' strings that are missing or empty.
    Empty list = contract passes, safe to render."""
    issues = []
    if not isinstance(content, dict) or not isinstance(plan, dict):
        return issues
    for slide in plan.get("slides") or []:
        tid = slide.get("template_id", "")
        required = _OPC_TIP_REQUIRED.get(tid) or _OPC_STANDALONE_REQUIRED.get(tid) or []
        for field in required:
            val = _get_field_value(content, tid, field)
            if val is None or val == "" or val == [] or val == {}:
                issues.append(f"{tid}.{field}")
    return issues


def repair_opc_content(content: dict, issues: list[str], topic: str, brief: str = "") -> dict:
    """Targeted repair pass — fills only the missing fields listed in `issues`.
    Uses Sonnet (not Haiku) because partial-fill prompts need reasoning.
    Returns the (possibly repaired) content dict. Never replaces existing fields."""
    if not issues:
        return content

    grouped: dict[str, list[str]] = {}
    for issue in issues:
        tid, _, field = issue.partition(".")
        grouped.setdefault(tid, []).append(field)

    repair_blocks = []
    for tid, fields in grouped.items():
        is_standalone = tid in _OPC_STANDALONE_REQUIRED
        current_block = content.get(tid) if is_standalone else content
        repair_blocks.append(
            f"Template: {tid}\n"
            f"Missing fields: {', '.join(fields)}\n"
            f"Current block (partial): {json.dumps(current_block or {}, indent=2)[:400]}"
        )

    tip_ctx = {k: content.get(k) for k in
               ("headline", "subhead", "accent_word", "slide2_stat", "slide2_label",
                "slide3_items", "slide4_headline", "slide4_body", "sources") if content.get(k)}

    prompt = f"""You are repairing incomplete OPC carousel content for Oak Park Construction (South Florida contractor, CBC1263425).

Topic: "{topic}"
Brief: {brief[:300] if brief else '(none)'}

EXISTING tip-shape context (do NOT contradict):
{json.dumps(tip_ctx, indent=2)}

The following template blocks have missing required fields. Fill ONLY the missing fields. Keep values concise and on-topic.

{chr(10).join(repair_blocks)}

OUTPUT FORMAT — return ONE JSON object keyed by template_id, like this:
{{
  "opc_tip_cover": {{ "headline": "...", "subhead": "..." }},
  "opc_tip_stat": {{ "slide2_stat": "...", "slide2_label": "..." }},
  "opc_four_card_grid": {{ "card_titles": [...], "card_copies": [...] }}
}}
Only include the templates listed above. Only include the missing fields. Do not return prose. Do not nest deeper than this.

RULES:
- slide2_stat: big number WITH qualifier (e.g. "UP TO $12K"). Max 40 chars.
- slide2_label: 1 line explaining the stat with source. Max 60 chars.
- slide3_items: exactly 3 items as [{{"title": ..., "sub": ...}}]. title max 34 chars. sub max 80 chars.
- slide4_body: 2-3 sentences. No promises. No superlatives.
- card_titles: 4 items, ALL CAPS, max 18 chars each.
- card_copies: 4 items, max 100 chars each.
- quote_body: Mike quote without quotation marks. Max 200 chars.
- caption_pills: 3 items, ALL CAPS, max 8 chars each.
- sources: list of 3-4 source strings.

Return ONLY JSON. No preamble."""

    print(f"  [repair] requesting fill for {len(issues)} field(s) across {len(grouped)} template(s)")
    try:
        text = _claude_with_fallback(
            prompt, max_tokens=1500, timeout=30,
            context="repair_opc_content",
        )
        if not text:
            print(f"  [repair] LLM returned empty text — keeping original content")
            return content
        print(f"  [repair] raw response ({len(text)} chars): {text[:300].replace(chr(10), ' ')}")
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            print(f"  [repair] no JSON in response — keeping original content")
            return content
        try:
            patch = json.loads(json_match.group())
        except json.JSONDecodeError as je:
            print(f"  [repair] JSON parse failed: {je} — keeping original content")
            return content
        print(f"  [repair] parsed patch top-level keys: {list(patch.keys())[:10]}")
    except Exception as exc:
        print(f"  [repair] LLM failed: {exc!r} — keeping original content")
        return content

    filled_count = 0
    for tid, fields in grouped.items():
        is_standalone = tid in _OPC_STANDALONE_REQUIRED
        # Tip templates may be returned as nested {tid: {...}} OR flat at top level.
        # Standalone templates may also be returned flat OR nested.
        nested_block = patch.get(tid) if isinstance(patch.get(tid), dict) else None
        for field in fields:
            new_val = None
            # Try nested first (LLM follows the prompt's "keyed by template_id" instruction)
            if nested_block is not None and field in nested_block:
                new_val = nested_block[field]
            # Fall back to flat top-level (LLM returns just the missing fields)
            elif field in patch:
                new_val = patch[field]
            if new_val is None or new_val == "" or new_val == [] or new_val == {}:
                print(f"  [repair] no value for {tid}.{field} in patch (nested={nested_block is not None}, top-keys={list(patch.keys())[:5]})")
                continue
            if is_standalone:
                content.setdefault(tid, {})[field] = new_val
            else:
                content[field] = new_val
            filled_count += 1
            preview = (str(new_val)[:60] + '...') if len(str(new_val)) > 60 else str(new_val)
            print(f"  [repair] filled {tid}.{field} = {preview}")

    print(f"  [repair] summary: {filled_count}/{len(issues)} fields filled")
    return content


# SH-155: visible placeholder strings that MUST NOT reach rendered HTML.
# If any of these appear in the body of cover.html the build is blocked.
_HTML_BLOCKED_PLACEHOLDER_STRINGS = (
    "STAT CONTEXT IMAGE",
    "TIP IN ACTION IMAGE",
    "PROCESS IMAGE",
    "INSTALL placeholder",
    "IMAGE PLACEHOLDER",
    "CONTEXT IMAGE",
)

# Required text-bearing div selectors per OPC tip template. Empty = block.
_HTML_REQUIRED_NONEMPTY = (
    # (css_class, human_label, max_check_per_variant_block)
    ("body-text", "cover subhead"),
    ("stat-big", "stat number"),
    ("stat-label", "stat label/source"),
    ("tip-explain", "pro tip body"),
    ("src-list", "sources list"),
)


def verify_html_completeness(html_path: str) -> list[str]:
    """SH-155: pre-email HTML gate. Reads the rendered cover.html and returns a list
    of issues — empty list = HTML is shippable, non-empty = block upload + email.

    Checks:
      1. No visible internal placeholder strings (STAT CONTEXT IMAGE, etc.)
      2. Required text-bearing divs are non-empty:
         body-text, stat-big (cannot be '—'), stat-label, tip-explain, src-list
      3. src-list must contain at least one .src-row child
    """
    issues: list[str] = []
    try:
        html = Path(html_path).read_text()
    except Exception as exc:
        issues.append(f"could not read {html_path}: {exc!r}")
        return issues

    # Strip HTML comments before scanning so '<!-- omitted ... -->' tags from
    # SH-156 don't false-trigger placeholder string match.
    body_only = re.sub(r"<!--[\s\S]*?-->", "", html)
    # Drop the entire <style> block — CSS class/selector text would match too.
    body_only = re.sub(r"<style[\s\S]*?</style>", "", body_only)

    # 1. Placeholder string scan
    for needle in _HTML_BLOCKED_PLACEHOLDER_STRINGS:
        if needle in body_only:
            issues.append(f"placeholder string '{needle}' visible in rendered HTML")

    # 2. Required non-empty divs (per occurrence — every variant block must be filled)
    for cls, label in _HTML_REQUIRED_NONEMPTY:
        # Match opening tag + capture content until matching close. Greedy-safe by
        # using non-greedy match against [^<] then </div>; nested divs allowed via
        # broader pattern.
        for m in re.finditer(rf'<div class="{cls}"[^>]*>([\s\S]*?)</div>', body_only):
            inner = m.group(1).strip()
            # Strip nested HTML tags to count visible text
            visible = re.sub(r"<[^>]+>", "", inner).strip()
            if not visible or visible == "—":
                issues.append(f"empty {cls} div ({label}) — slide cannot ship")
                break  # one issue per class is enough; don't spam

    # 3. src-list must contain at least one src-row
    for m in re.finditer(r'<div class="src-list"[^>]*>([\s\S]*?)</div>\s*<div class="cta-bar"', body_only):
        if 'class="src-row"' not in m.group(1):
            issues.append("src-list contains no src-row entries — sources missing")
            break

    return issues


def check_comparison_text_parity(content: dict, left: str, right: str) -> tuple[bool, str]:
    """Check that both sides of a comparison appear at least twice in the
    generated text (slide copy, not just the topic title). Returns (pass, msg)."""
    if not left or not right:
        return True, ""
    text = json.dumps(content).lower()
    left_count = text.count(left.lower())
    right_count = text.count(right.lower())
    min_count = 2
    if left_count < min_count or right_count < min_count:
        return False, (
            f"Comparison parity low: {left}={left_count} mentions, "
            f"{right}={right_count} mentions (need ≥{min_count} each)"
        )
    return True, ""


# ── SH-056: OPC photorealistic guardrails ─────────────────────────────────────

# Known AI-art/illustration hosting domains — images from these URLs are rejected
# for OPC (real-photo rule). They are silently allowed for news/brazil/usa.
_AI_ART_DOMAINS = frozenset([
    "lexica.art",
    "midjourney.com",
    "artstation.com",
    "deviantart.com",
    "civitai.com",
    "generated.photos",
    "thispersondoesnotexist.com",
    "stability.ai",
    "nightcafe.studio",
])

# Suffix appended to OPC image search queries so stock providers return
# photorealistic editorial photos rather than illustrations or 3D renders.
_OPC_PHOTO_SUFFIX = " photorealistic real photo no illustration no cartoon no render"

# SH-056: maps local_path → original_url so _vision_accept can check the source domain.
# Populated by _fetch_pexels_image, _fetch_pixabay_image, and other fetch helpers.
_fetch_url_cache: dict = {}


def _is_ai_art_url(url: str) -> bool:
    """Return True if the URL is from a known AI-art domain (reject for OPC)."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in _AI_ART_DOMAINS)
    except Exception:
        return False


def _opc_photo_query(query: str, niche: str) -> str:
    """Append photorealistic suffix to stock-search queries for OPC builds.
    SH-025: also enriches query with a specific material reference term when the
    topic mentions a known material category (paint, flooring, tile, etc.).
    Non-OPC niches are returned unchanged — their AI cascade is allowed."""
    if niche != "opc":
        return query
    if _OPC_PHOTO_SUFFIX.strip() in query:
        return query
    # SH-025: enrich with OPC_MATERIAL_REFERENCE when topic matches a material keyword
    try:
        from photo_matcher import OPC_MATERIAL_REFERENCE as _opc_mat  # type: ignore
        q_lower = query.lower()
        for _cat, _terms in _opc_mat.items():
            cat_word = _cat.replace("_", " ")
            if cat_word in q_lower or _cat in q_lower:
                # Prepend the first specific reference term to narrow the stock search
                query = _terms[0] + " " + query
                break
    except Exception:
        pass
    return (query + _OPC_PHOTO_SUFFIX)[:200]


def _claude_with_fallback(prompt, *, max_tokens, timeout=60, context="", model="claude-sonnet-4-6"):
    """Try the Claude→OpenAI→Gemini cascade; if the shared module is unavailable,
    fall back to the raw HTTP call this script originally used."""
    import sys as _sys
    if _llm_text_cascade and model == "claude-sonnet-4-6":
        try:
            result = _llm_text_cascade(prompt, model_tier="sonnet",
                                       max_tokens=max_tokens, context=context)
            # SH-147: read which tier actually succeeded from _llm_fallback module state
            _fb = _sys.modules.get("_llm_fallback")
            _prov = (_fb._last_provider.copy() if _fb and hasattr(_fb, "_last_provider") else {})
            _gen_trace.update({
                "provider": _prov.get("provider", "cascade"),
                "model": _prov.get("model", "unknown"),
                "fallback_used": _prov.get("tier", 1) > 1,
                "context": context,
            })
            print(f"  [gen:{context or 'content'}] generated_by={_gen_trace['provider']} "
                  f"model={_gen_trace['model']} fallback={_gen_trace['fallback_used']}")
            return result
        except Exception as e:
            print(f"  [carousel_builder] cascade failed ({e}) — trying raw Claude HTTP")
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    _gen_trace.update({"provider": "claude-direct", "model": model, "fallback_used": True, "context": context})
    print(f"  [gen:{context or 'content'}] generated_by=claude-direct model={model}")
    return resp["content"][0]["text"]

OPC_TEMPLATE = "tip"
BRAZIL_TEMPLATE = "quem-decidiu"
USA_TEMPLATE = "fact-checked"

# SERIES REGISTRY — every series must have a template entry here.
# Verification / Fake News series: Verificamos (Brazil) + Fact-Checked (USA)
# These are FORMAT-001 reel series (split screen + sources). Carousel variant uses same structure.
TEMPLATES = {
    "opc": {
        "tip": {
            "series": "Tip of the Week",
            "tag": "Tip of the Week · Oak Park Construction",
            "slides": 5,
            "structure": "cover → stat → list → tip → sources",
        },
        "illustrated": {
            "series": "Tip of the Week",
            "tag": "Tip of the Week · Oak Park Construction",
            "slides": 5,
            "structure": "cover → stat → list → tip → sources (illustrated editorial)",
        },
        "cutout": {
            "series": "Tip of the Week",
            "tag": "Tip of the Week · Oak Park Construction",
            "slides": 5,
            "structure": "cover → stat → list → tip → sources (cutout sticker editorial)",
        },
        "progress": {
            "series": "Progress",
            "tag": "Progress · Oak Park Construction",
            "slides": 5,
            "structure": "cover → stage → what's done → what's next → credits",
        },
    },
    "brazil": {
        "quem-decidiu": {
            "series": "Quem decidiu isso?",
            "tag": "Quem decidiu isso?",
            "slides": 4,
            "structure": "cover → context → breakdown → sources",
        },
        "verificamos": {
            # FORMAT-013 Route B — expert debunk / institutional source carousel
            "series": "Verificamos",
            "tag": "Verificamos",
            "slides": 5,
            "structure": "cover (claim) → what people believe → the source → what it actually says → sources/CTA",
        },
        "verificamos_clip": {
            # FORMAT-013 Route A — original claim clip with real-time source overlay (FORMAT-001 style)
            "series": "Verificamos",
            "tag": "Verificamos",
            "slides": 4,
            "structure": "cover (VERIFICAMOS stamp) → clip context → source overlay → sources/CTA",
        },
        "dados-ou-agenda": {
            # FORMAT-019 — Influencer/public figure bias check carousel
            # 3 verdicts: BASEADO EM DADOS / VIÉS IDEOLÓGICO / VIÉS DE INTERESSE
            # series_override: "DADOS OU AGENDA" (triggers this template)
            # Requires approval before scheduling (same gate as Verificamos)
            "series": "Dados ou Agenda?",
            "tag": "Dados ou Agenda?",
            "slides": 9,
            "structure": "cover (hook+credibility badge) → claim → true fact 1 → true fact 2 → what was missing → exaggeration check → verdict (THE KEY SLIDE) → our verdict → CTA",
        },
        "verdade-pela-metade": {
            # FORMAT-024 — Weekly fake-news debunk carousel (Tuesdays)
            # Source account never named in content (internal only)
            # Two modes: mode_a = wrong attribution, mode_b = distorted numbers
            # series_override: "VERDADE PELA METADE" (triggers this template)
            # Requires approval before scheduling (same gate as Verificamos)
            "series": "Verdade Pela Metade",
            "tag": "Verdade Pela Metade",
            "slides": 7,
            "structure": "cover → o-que-diz → mode-branch (quem-decidiu|numero-real) → contexto → fontes → conclusao → sources",
        },
    },
    "usa": {
        "fact-checked": {
            # Fake News / Verification series — USA account (English)
            # FORMAT-001 twin: same structure as Verificamos but English + US sources
            "series": "Fact-Checked",
            "tag": "Fact-Checked",
            "slides": 5,
            "structure": "cover (claim) → what people believe → the source → what it actually says → sources/CTA",
        },
        "the-chain": {
            "series": "The Chain",
            "tag": "The Chain",
            "slides": 4,
            "structure": "cover → context → breakdown → sources",
        },
    },
}

# === COPY RULES — encoded from Priscila's preferences ===
OPC_COPY_RULES = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OPC HOOK + STORYTELLING GUIDE — write from this first
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are a social media educator for homeowners. Your only job: stop the scroll, teach one thing, earn the follow. Every carousel is a complete story. If someone reads only slide 1 and then slide 5, they must understand the full arc.

BEFORE YOU WRITE ANYTHING — pick ONE hook type:

1. FEAR OF MISTAKE
   The reader thinks they are safe but they are not. Name the exact mistake. Name the cost.
   Example: "The $8K mistake most homeowners make before choosing their patio material."
   Why it works: they scroll because they need to find out IF they're the one making this mistake.

2. CURIOSITY GAP
   They sense there is something they don't know. They MUST scroll to close the gap.
   Example: "Nobody tells you this about poured concrete — until it's year 3."
   Why it works: the gap between "I don't know" and "I need to know" is what drives the swipe.

3. CONTRARIAN (say the opposite, then flip it inside)
   Lead with the common belief. Slides 2-4 prove it wrong. The flip IS the story.
   Example: Cover says "Pavers are always cheaper." Slide 3 proves it wrong with real numbers.
   Why it works: disagreement = curiosity. They swipe to find out how you dare say that.

4. TEACH ONE THING
   Frame it as free knowledge they are leaving on the table.
   Example: "One question every homeowner should ask before signing any patio quote."
   Why it works: they feel like idiots for NOT knowing it. They scroll to get smarter.

5. PAIN POINT (name a problem they already have)
   They already feel this pain. You name it and promise the fix inside.
   Example: "Your concrete driveway IS going to crack. Here's what you can do about it."
   Why it works: the problem is already theirs. The fix is the reason to swipe.

SLIDE-BY-SLIDE STORY STRUCTURE:
- Slide 1 (HOOK): ONE promise. Name the mistake/risk/gap/flip/pain. Main claim = under 10 words.
  The viewer must think: "Wait — am I making this mistake?" or "I need to know this."
- Slide 2 (STAKES): Raise the consequence. Why does this matter THIS week, not someday?
  Open the loop wider. Give the number that raises the stakes.
- Slide 3 (ANSWER — the most important slide): THIS is where you pay off the hook.
  Name the exact mistake. Prove the contrarian claim. Give the insight. NEVER stay generic.
  If the hook said "mistake," name the mistake HERE. Not implied. Named explicitly.
- Slide 4 (ACTION): ONE thing the homeowner does differently because of this carousel.
  Mike's voice. First person. Specific. "First thing I ask every client: [specific question]."
- Slide 5 (CLOSE): Close the loop opened on slide 1. The viewer must think: "Now I know."

FULL-CIRCLE STORY RULE:
Before writing a single slide, complete this sentence: "The question slide 1 creates is: ___."
Slides 2-4 must answer that question completely and explicitly.
If a homeowner finishes slide 4 and still cannot explain the slide 1 claim in one sentence — the carousel failed. Rewrite it.

THE SWIPE TEST — ask before you write:
"Why would a busy homeowner stop scrolling mid-feed for THIS specific slide?"
If the honest answer is "they probably wouldn't" — rewrite the hook.
The hook must do ONE of: reveal a mistake they might be making, close a gap they feel, flip something they believe, give them something free, name a pain they already have.

EVIDENCE RULE — apply before writing the hook:
Do not put a dollar amount, percentage, lifespan, "most homeowners," or count in the hook UNLESS the body can support it with a named source, qualified range, or visible calculation. Ask yourself: "Can I actually prove this inside slides 2-4?" If the answer is no, remove the number from the hook or use softer wording ("can cost more than you think" instead of "$8K more"). A strong hook with a number you cannot prove is worse than a softer hook with evidence behind it.

FORMAT RULE — narrow the angle before you write:
Write to the template's slide count. If the topic is too broad for the number of slides, pick ONE decision the viewer faces today and write about that. Do not try to cover the whole subject. A carousel about "concrete vs pavers" is too broad for 5 slides — "the one cost most people miss when choosing concrete" is the right angle. If you genuinely cannot narrow the topic to fit, set needs_longer_format=true in the strategy block.

VISUAL RULE — decide what each image proves before writing the query:
Every middle-slide image must earn its place by proving, clarifying, or showing the thing that slide teaches. Before writing any image search query, answer: "What does the viewer need to SEE to believe this slide?" A slide about installation cost needs a photo of the work or result — not a generic "construction" photo. Write the query to source that specific proof, not to decorate the slide.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY SAFETY RULES — apply AFTER writing great content
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. NEVER make promises about what Oak Park Construction does for clients.
   - BANNED: "This is what we tell every client", "We always include...",
     "After hundreds of jobs...", "Our guarantee is..."
   - WHY: Public content creates expectations. If a customer reads "we always do X"
     and OPC doesn't do X on their project, it's a liability.

2. NEVER state conditional statistics as universal facts.
   - BANNED: "$12K average overrun" (depends on property size, scope, region)
   - ALLOWED: "Overruns can range from $5K to $20K depending on scope"
   - ALLOWED: "In South Florida mid-range renos, overruns of $8K-$15K are common"
   - RULE: If a number depends on variables, qualify it with context or use a range.

3. Keep captions SIMPLE — describe the topic, let the slides carry the detail.
   - The caption hooks them in. The slides teach. Don't repeat slide content in caption.
   - Max 3-4 sentences for the main caption body (before hashtags).

4. Content should be EDUCATIONAL, not sales-y.
   - You're teaching homeowners, not selling OPC's services.
   - The authority comes from the knowledge, not from claiming experience.

5. Every cost range or statistic needs a qualified source.
   - Industry data must cite the org (Houzz, NAHB, Remodeling Magazine,
     ICC/IBC, ACI, ASCE, Florida Building Code/FBC, IRC).
   - Use "according to [source]" or put the source on the slide itself.
   - DO NOT cite "Oak Park Construction — South Florida contractor data
     2023-2025" UNLESS that exact data point came from a real OPC job log.
     If you don't have it, downgrade the claim to non-numeric wording or
     cite a real industry source (FBC, ACI, NAHB).
   - SOURCES SLIDE = the receipts. Every $, %, "years" claim that appears
     on any slide MUST have a matching source on the sources slide. If a
     claim doesn't have a real source, REMOVE THE NUMBER, don't invent
     a source.
   - BANNED: vague unsourced "studies show", "industry average", "experts
     say". If you can't name the source, drop the claim.

6. Tone: Direct, matter-of-fact, no jargon, no hype.
   - Write like a contractor explaining something to a homeowner over coffee.
   - No exclamation marks in slide text. One max in caption.

7. READABILITY: Write at 8th-grade level or simpler.
   - Max sentence length: 15 words. Break longer sentences in two.
   - Use common words. BANNED: "utilize" (say "use"), "leverage" (say "use"),
     "implement" (say "do"), "facilitate" (say "help"), "subsequently" (say "then"),
     "aforementioned" (say "this"), "commence" (say "start"), "endeavor" (say "try").
   - If a homeowner needs to Google the word, pick a simpler one.

8. HEADLINE FRAMING (NON-NEGOTIABLE):
   - NEVER use question framings: "What is X?" / "Why is X?" / "How is X?" /
     "Is X worth it?" / "What's X?" — anything that ends in a question mark
     or opens with What/Why/How/Is.
   - headline_main = the subject NAME in ALL CAPS (e.g. "CONCRETE",
     "POURED CONCRETE", "PAVERS", "SHIPLAP", "REBAR").
   - headline_italic = a sharp 2-5 word descriptor that gives the angle
     (e.g. "the bonded backbone", "20-year guaranteed", "the cost king",
     "Florida-built since '78").
   - Together they form a noun phrase, NOT a question. Example output:
     "CONCRETE" + italic "the bonded backbone".
   - This applies to opc_material_profile, opc_item_spotlight, opc_base,
     opc_statement. NEVER include a question mark in headline_main or
     headline_italic for these templates.

9. CREDIBILITY LAYER — contractor-safe caveats (#122 addendum):
   When a slide mentions price, timeline, or ROI — add a qualifier:
   - Price: "costs vary by scope, materials, and local market"
   - Timeline: "add 20-30% buffer for permit approval and material lead time"
   - ROI: "ROI is market-specific and not guaranteed — research local comps"
   These caveats build trust. Homeowners who see you hedge on ROI believe the rest.

10. COVER HOOK ANCHOR (OPC storytelling rule — #122):
    The cover hook must be grounded in ONE of these 5 tension types:
   - RISK: a consequence the homeowner didn't know they were facing
   - COST: a dollar/time range that surprises people — not just "expensive"
   - MISTAKE: what most homeowners do wrong and why it costs them
   - HIDDEN CONSEQUENCE: a downstream problem caused by a common choice
   - DECISION TENSION: a genuine trade-off (A vs B) homeowners face right now
   If the cover hook doesn't fit one of these 5 types, rewrite it until it does.
   NEVER open with "Here's what you need to know" or similar generic openers.
"""

BRAZIL_COPY_RULES = """
MANDATORY RULES — follow these exactly:

1. Language: Brazilian Portuguese (informal but not slangy).
2. Political content must be FACTUAL — no opinion, no editorial, no accusation.
3. Always include party affiliation when naming a politician: "Fulano (PT-RJ)"
4. Every factual claim needs 2+ sources from different outlets.
5. Series-premise rule: if the title asks a question, the carousel MUST answer it.
6. Never use "todos", "maioria", "everyone" without qualifying with actual data.
7. Tone: Fact-check energy. "Here are the facts. Now you know."
0. COVER CLAIM RULE (non-negotiable, all series): The CLAIM or OPINION being discussed MUST appear on slide 1 (the cover). Not on slide 2 — on slide 1. The cover is the hook. The claim IS the hook. Format it as cover_claim: a provocative 1-line statement that stops the scroll. Rage-bait energy, Jubilee debate style. NEVER leave the cover as just a topic title with no claim.

CAROUSEL STRUCTURE RULES (Brazil/News fact-check):
8. Hook slide = THE BIG CLAIM/NUMBER only. Do NOT hint that you'll question it.
   - Lead with the size of the claim to make people stop scrolling.
   - "R$1.4 bilhão" as the headline — not "será que gastou mesmo?"
   - The skepticism lives in the middle slides, never in the hook.
9. Receipts slide = screenshots or citations from primary sources (gov websites,
   official docs, opposing-side confirmation). "Segue o documento."
10. Opposition confirmation = find 1 source from the political opposition that
    ALSO confirms the same fact. Cross-partisan agreement = strongest credibility.
    This kills the "this is partisan" rebuttal before it starts.

11. READABILITY: Escreva em linguagem simples (nível ensino médio).
    - Frases curtas — máximo 15 palavras por frase. Divida frases longas em duas.
    - Palavras do dia a dia. PROIBIDO: termos técnicos sem explicação, jargão político
      não explicado, siglas sem legenda. Se precisar de sigla, explique na primeira vez.
    - Meta: qualquer pessoa de 14 anos deve entender sem consultar dicionário.
11. Caption is written AFTER the carousel slides are finalized — never before.
    Caption complements the slides, it does not summarize them.
12. CAPTION HASHTAG RULES (shadow-ban prevention):
    - NEVER use party abbreviations as hashtags: no #PT, #PL, #PSDB, #MDB, #Bolsonaro, #Lula.
    - NEVER @-tag or hashtag politicians by name.
    - Use ONLY topic hashtags: #politicabrasileira, #senadofederal, #fiscalizacao, #direitoshumanos.
    - Reason: party hashtags trigger shadow-ban on Instagram. Attribution-without-traffic rule.

13. COVER HOOK ANCHOR TYPE (News storytelling rule — #122):
    Every cover hook must be anchored on ONE of these patterns:
    - EXACT CLAIM: the specific claim being verified — include the number when available
    - CONTRADICTION: two official positions that oppose each other
    - VOTE / RESULT: who voted for what, who approved or blocked it
    - NUMBER THAT SHOCKS: a budget figure, a percentage, a vote count
    - LEGAL / INSTITUTIONAL TENSION: what law was broken or two institutions with opposing stances
    NEVER use "Você sabia que..." / "Não vai acreditar..." or any generic opener.
    The number or the contradiction IS the hook — lead with it directly.

14. OBJECTION LAYER (#122 Addendum — News objection/context):
    Every carousel must anticipate ONE honest objection the reader will raise.
    Classify the objection as one of:
    - PARCIALIDADE: "isso é parcial" — address cross-party confirmation (see rule 10)
    - CONTEXTO: "isso é fora de contexto" — add timeline or historical comparison
    - OUTRO LADO: "e o lado oposto?" — cite opposition statement or vote record
    - EXCEÇÃO: "mas nem todos..." — acknowledge scope limits with exact numbers
    The objection + response lives on the PENULTIMATE slide (second-to-last),
    before the sources slide. Format: one sentence raising the objection,
    one sentence with the fact-based response. Keep it under 20 words each.
    NEVER ignore the objection — readers who see it pre-empted trust the rest.
"""


def _web_research(topic, lang="en"):
    """Fetch background on topic from free public APIs — no key needed.
    Returns a plain-text summary to prepend to Haiku prompts when content comes back thin."""
    summaries = []

    # DuckDuckGo Instant Answer API
    try:
        q = urllib.parse.quote_plus(topic)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "content-creator/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        abstract = data.get("AbstractText", "").strip()
        if abstract:
            summaries.append(abstract)
        for item in data.get("RelatedTopics", [])[:3]:
            text = item.get("Text", "").strip()
            if text and len(text) > 40:
                summaries.append(text)
    except Exception as e:
        print(f"  DuckDuckGo research skipped: {e}")

    # Wikipedia summary — supplement / fallback
    if len(summaries) < 2:
        wiki_lang = "pt" if lang == "pt" else "en"
        try:
            q = urllib.parse.quote_plus(topic)
            url = f"https://{wiki_lang}.wikipedia.org/api/rest_v1/page/summary/{q}"
            req = urllib.request.Request(url, headers={"User-Agent": "content-creator/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            extract = data.get("extract", "").strip()
            if extract:
                summaries.append(extract[:600])
        except Exception:
            try:
                url = f"https://{wiki_lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&srlimit=3&format=json"
                req = urllib.request.Request(url, headers={"User-Agent": "content-creator/1.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read())
                for result in data.get("query", {}).get("search", [])[:2]:
                    snippet = re.sub(r'<[^>]+>', '', result.get("snippet", "")).strip()
                    if snippet:
                        summaries.append(snippet)
            except Exception as e:
                print(f"  Wikipedia research skipped: {e}")

    # Stack Exchange (Stack Overflow + network) — free, no key, great for tech/spreadsheet topics
    if len(summaries) < 3:
        try:
            q = urllib.parse.quote_plus(topic)
            url = f"https://api.stackexchange.com/2.3/search/excerpts?q={q}&order=desc&sort=relevance&site=stackoverflow&pagesize=3"
            req = urllib.request.Request(url, headers={"User-Agent": "content-creator/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                data = json.loads(gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw)
            for item in data.get("items", [])[:3]:
                excerpt = re.sub(r'<[^>]+>', '', item.get("excerpt", ""))
                excerpt = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), excerpt)
                excerpt = excerpt.replace("&hellip;", "…").replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'").strip()
                title = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), item.get("title", "")).replace("&quot;", '"').replace("&amp;", "&")
                if excerpt and item.get("score", 0) > 0:
                    summaries.append(f"{title}: {excerpt[:300]}")
        except Exception as e:
            print(f"  Stack Exchange research skipped: {e}")

    return "\n".join(summaries[:5]) if summaries else ""


def generate_progress_content(topic, brief=""):
    """Generate OPC Progress post content (project update carousel).
    Fields map to _build_opc_progress_html: project_name, project_address, stage,
    stage_date, whats_done/caption, whats_next/caption, project_id, workers.
    Brief should contain project details when available; Haiku fills gaps creatively."""
    brief_section = f"\n\nPROJECT BRIEF:\n{brief}" if brief and str(brief).strip() else ""
    _today = datetime.datetime.utcnow().strftime("%B %d, %Y")
    prompt = f"""You are writing an Oak Park Construction project-progress Instagram carousel.
Project/topic: "{topic}"{brief_section}

Mike is a licensed South Florida contractor (CBC1263425). Write as him talking directly to homeowners.
RULES:
1. NEVER promise timelines or specific outcomes. NEVER use superlatives.
2. project_name: ALL CAPS, 3-5 words, punchy (e.g. "WALNUT KITCHEN REMODEL", "MASTER BATH REBUILD")
3. project_address: if brief has an address use it; otherwise write "South Florida" — never invent an address
4. stage: current construction phase in ALL CAPS (2-5 words, e.g. "TILE INSTALLATION", "FRAMING COMPLETE")
5. stage_date: short time marker in ALL CAPS (e.g. "WEEK 3 OF 6", "DAY 12", "PHASE 2 OF 3")
6. whats_done_caption: ALL CAPS, 2-4 words (e.g. "CONCRETE POURED", "DEMO DONE", "WALLS UP")
7. whats_done: 2 sentences max. What was completed. Specific, no vague filler.
8. whats_next_caption: ALL CAPS, 2-4 words (e.g. "TILE GOES IN", "CABINET INSTALL NEXT")
9. whats_next: 2 sentences max. What comes next and why it matters to the homeowner.
10. project_id: "PROJECT #OPC-{_today[:4]}-XXX" — replace XXX with a 3-digit random-ish number
11. workers: list of 1-3 crew members with name + role (if brief has names use them; otherwise use generic roles like "Lead Carpenter" with placeholder name "T. Rivera")
12. caption: 2-3 sentence Instagram caption. Hook = first line (must mention the stage or a specific task). End with 6-8 hashtags: #oakparkconstruction + material/trade-specific tags. NO generic #construction alone.
13. READABILITY (SH-013): 8th-grade reading level. Max 16 words per sentence. No jargon without a plain-language explanation within 2 lines. Short sentences. Active voice.

Return ONLY a valid JSON object:
{{
  "project_name": "ALL CAPS project name",
  "project_address": "address or 'South Florida'",
  "stage": "CURRENT STAGE IN CAPS",
  "stage_date": "WEEK X OF Y or similar",
  "whats_done": "What was completed — 2 sentences",
  "whats_done_caption": "ALL CAPS PHOTO CAPTION",
  "whats_next": "What comes next — 2 sentences",
  "whats_next_caption": "ALL CAPS PHOTO CAPTION",
  "project_id": "PROJECT #OPC-YYYY-NNN",
  "workers": [
    {{"name": "First Last", "role": "Trade Role"}},
    {{"name": "First Last", "role": "Trade Role"}}
  ],
  "caption": "Instagram caption with hook + hashtags",
  "cover_visual": {{
    "option_a": {{"search_query": "Wikimedia Commons search for a CC photo of this construction stage"}},
    "option_b": {{"prompt": "DALL-E 3 prompt — editorial photo, South Florida residential, this construction stage, no text"}}
  }},
  "clip_suggestions": [
    {{
      "slide": 1,
      "youtube_query": "YouTube search for this construction stage timelapse or tutorial",
      "instagram_query": "lowercase hashtag-friendly phrase for this stage",
      "pexels_query": "Pexels stock video for this stage — material + action, 4+ words",
      "pixabay_query": "Different wording same subject as pexels_query",
      "archive_query": "Public-domain footage phrasing for this trade/stage",
      "wikimedia_query": "CC-licensed clip for this construction work",
      "motion_prompt": "5s direction: camera move + mood for cover slide",
      "motion_renderer": "remotion",
      "visual_hint": "product-photo"
    }}
  ]
}}"""

    for attempt in range(2):
        try:
            # SH-OPC-SMART-SLIDE-PICKER Phase 10: Sonnet primary for smart-path
            # OPC content. Haiku is too weak for schema-strict template content.
            # Falls back to OpenAI/Gemini via the cascade if Anthropic is
            # rate-limited / out of credits.
            resp = _claude_with_fallback(
                prompt, max_tokens=1500, timeout=45,
                context="generate_progress_content", model="claude-sonnet-4-6",
            )
            raw = resp.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
            result = json.loads(raw)
            result["_template_key"] = "progress"
            return result
        except Exception as e:
            if attempt == 0:
                print(f"  Progress content retry: {e}")
                continue
            print(f"  Progress content FAILED: {e}")
            return None


def generate_carousel_content(topic, niche, template_key=None, brief="", model="claude-sonnet-4-6", slide_plan=None):
    # Special templates checked BEFORE the generic niche short-circuit
    if template_key == "progress":
        return generate_progress_content(topic, brief)
    if template_key == "dados-ou-agenda":
        return generate_dados_content(topic, brief)
    if template_key == "verdade-pela-metade":
        return generate_verdade_content(topic, brief)
    if niche in ("brazil", "usa"):
        return generate_brazil_content(topic, brief, model=model)
    if not template_key:
        template_key = OPC_TEMPLATE if niche == "opc" else BRAZIL_TEMPLATE

    tmpl = TEMPLATES.get(niche, {}).get(template_key)
    if not tmpl:
        print(f"  No template for {niche}/{template_key}")
        return None

    lang = "Portuguese (Brazilian)" if niche == "brazil" else "English"
    copy_rules = OPC_COPY_RULES if niche == "opc" else BRAZIL_COPY_RULES
    purpose_block = _slide_purpose_block(niche, tmpl['slides'])  # SH-138 pilot

    # Build plan-aware block when planner already decided the template per slide
    _plan_block = ""
    if slide_plan and slide_plan.get("slides"):
        _slide_lines = []
        for s in slide_plan["slides"]:
            tid = s.get("template_id", "")
            purpose = s.get("purpose", "")
            _slide_lines.append(f"  Slide {s.get('slide', '?')}: {tid} — {purpose}")
        _plan_block = (
            "\nTEMPLATE PLAN (already decided — write content to match this exact plan):\n"
            + "\n".join(_slide_lines)
            + "\n\nCRITICAL rules based on this plan:\n"
            + "- If any slide uses opc_four_card_grid: slide3_items MUST be 4 comparison DIMENSIONS "
            "(e.g. COST / DURABILITY / MAINTENANCE / APPEARANCE), each with data for BOTH sides "
            "in the 'sub' field. Format: 'Left side: $X–$Y | Right side: $A–$B'. "
            "Do NOT write generic tips as list items.\n"
            "- If any slide uses opc_item_spotlight: slide3_items[0] is the featured item — "
            "make it a named product/material with a specific stat in 'sub'.\n"
            "- If any slide uses opc_material_profile: the headline must name the material "
            "explicitly (e.g. 'CONCRETE BLOCK WALLS') and slide3_items = 3 decision factors "
            "a homeowner weighs (cost, maintenance, durability) with data in 'sub'.\n"
            "- If any slide uses opc_duotone: slide4_body becomes the quote/myth to debunk — "
            "write it as a single strong claim the homeowner believes, then counter it.\n"
        )

    prompt = f"""You are a content writer for an Instagram carousel.
Generate content for a {tmpl['slides']}-slide carousel about: "{topic}"

Series: {tmpl['series']}
Structure: {tmpl['structure']}
Language: {lang}

{copy_rules}
{purpose_block}
{_plan_block}
Return ONLY a JSON object. The FIRST fields must be the strategy block — fill these honestly before writing any slide content. If you cannot complete viewer_question and payoff before writing slides, rewrite the hook until you can.
{{
  "hook_frame": "which hook type you chose — one of: fear_of_mistake | curiosity_gap | contrarian | teach_one_thing | pain_point",
  "viewer_question": "the exact question slide 1 creates in the viewer's mind — e.g. 'What IS the mistake?' or 'Why does it cost more?'",
  "payoff": "the specific useful thing the viewer knows after slide 4 that they did not know before slide 1 — 1 sentence",
  "proof_needed": "what evidence supports the main claim — name the source type: e.g. 'NAHB cost-per-sqft data' or 'ACI lifespan range' or 'rate/range from industry body'. If you cannot name a real source, soften the hook claim.",
  "format_fit": true,
  "needs_longer_format": false,
  "visual_strategy": "one sentence each for slides 2, 3, 4 — what the viewer needs to SEE to believe that slide. E.g. 'Slide 2: concrete pour showing labor cost. Slide 3: cracked concrete vs intact pavers year 3. Slide 4: homeowner reviewing quote with contractor.'",
  "headline": "3-4 word cover headline (ALL CAPS, punchy) — MUST include a number, dollar amount, timeframe, or named loss/risk. GOOD: '3 COSTLY MISTAKES', '$20K TRAP', 'AVOID THIS COST'. BAD: 'CONCRETE OR PAVERS', 'THINGS TO KNOW', 'TIPS AND TRICKS', 'WHAT TO DO'.",
  "accent_word": "1 word from headline to highlight in accent color",
  "subhead": "1 sentence under the headline — MUST contain at least one of: a specific number, a dollar amount, or a named consequence/fear. BANNED: generic phrases like 'what to look for', 'things you should know', 'tips for'. Good: '$20K mistake most homeowners make before signing' | '3 red flags contractors hope you miss'",
  "hook_answer": "1 plain sentence that directly answers the cover promise. If headline says MISTAKE/TRAP/AVOID/COST, name exactly what the mistake/trap/cost driver is. MUST start with 'The mistake:' or 'The trap:' or 'The risk:'. GOOD: 'The mistake: comparing only the install price instead of the total 10-year cost.' BAD: 'Concrete and pavers have different pros and cons.'",
  "slide2_headline": "3-4 word headline for slide 2",
  "slide2_stat": "a big number or stat WITH QUALIFIER (e.g. 'UP TO $15K' not '$12K') — stat_number MUST be 40 characters or fewer including spaces",
  "slide2_label": "1 line connecting the stat to a homeowner's real decision — frame as a consequence, not a citation. GOOD: 'That gap erases your patio budget before you pour the slab (NAHB 2023)' | 'One wrong choice here eats 40% of most remodel budgets (Remodeling Magazine 2024)'. BAD: 'According to NAHB, prices vary by material.' Always end with the source in parentheses.",
  "slide3_items": [
    {{"title": "Item 1 title", "sub": "1 line ending with a decision consequence — for mistake/trap hooks, this MUST state or prove hook_answer. GOOD: '$8–12K upfront, no sealing needed — pays for itself at year 5' | 'Cracks under heavy loads — you replace it, not the contractor'. BAD: 'Costs more than asphalt.'"}},
    {{"title": "Item 2 title", "sub": "1 line with decision consequence — different angle from item 1"}},
    {{"title": "Item 3 title", "sub": "1 line with decision consequence — different angle from items 1 and 2"}}
  ],
  "slide4_headline": "3-4 word action headline — name the specific move, not a generic warning label. GOOD: 'COMPARE TOTAL COST', 'CHECK DRAINAGE FIRST', 'PLAN REPAIRS EARLY'. BAD: 'AVOID THIS', 'WATCH OUT', 'RED FLAG', 'THE PRO MOVE', 'PRO TIP'",
  "slide4_body": "2-3 sentences as Mike speaking directly to the homeowner who is about to make the mistake this carousel warns about. First person. Name the one specific thing they should ask or do first, and why it saves money. Conversational, not instructional. No promises, no superlatives. GOOD: 'First thing I ask every client: what\\'s your 10-year plan for this? Concrete costs more today but I\\'ve seen pavers shift and stain by year 3 — that repair bill surprises people.' BAD: 'Homeowners should consider both options carefully before making a decision.'",
  "mentioned_people": [
    {{"name": "Full Name", "role_en": "role / why they're named", "slide": 4, "image_hint": "Wikipedia or editorial headshot search term"}}
  ],
  "sources": [
    "Source 1 — description",
    "Source 2 — description",
    "Source 3 — description",
    "Oak Park Construction — South Florida contractor data, 2023-2025"
  ],
  "cta": "4-7 word Mike-voice closer that creates a sense of payoff — not just a save prompt, a mini-resolution. GOOD: 'SAVE THIS BEFORE YOUR NEXT BID.' | 'SHOW THIS TO YOUR CONTRACTOR.' | 'SCREENSHOT BEFORE SIGNING ANYTHING.' | 'KEEP THIS BEFORE YOU CALL ANYONE.' BAD: 'SAVE THIS.' | 'LIKE AND SHARE.' | 'FOLLOW FOR MORE TIPS.'",
  "caption": "Instagram caption: 2-3 sentences max. Hook first line (visible in feed). Describe the topic. Let slides do the teaching. End with 8-12 relevant hashtags.",
  "audience_questions": [
    "Question a viewer would ask after seeing slide 1",
    "Question triggered by the stat or claim",
    "Question about what to do / what this means for them"
  ],
  "cover_visual": {{
    "option_a": {{
      "search_query": "Wikimedia Commons search term for a CC-licensed photo (material, process, or tool — e.g. 'rebar concrete construction', 'shiplap wood wall', 'kitchen cabinet frameless')"
    }},
    "option_b": {{
      "prompt": "DALL-E 3 image prompt if no CC photo found — editorial photo style, construction/interior context, no text in image"
    }}
  }},
  "slides": [
    {{
      "slide": 2,
      "visual_hint": "context-image or none — use context-image when slide2_stat references something visual (a material, a process, a specific product)",
      "context_image_query": "Pexels/Wikimedia search for the STAT on SLIDE 2 (slide2_stat field) — must show the material or process the number is about. MINIMUM 4 words. GOOD: 'concrete driveway residential pour south florida', 'bathroom tile frameless shower door installation', 'shiplap wood accent wall interior residential'. BAD (banned): 'construction work', 'house', 'renovation', 'contractor', 'kitchen', 'bathroom', 'home improvement'. Must be specific to the stat subject, not the overall topic.",
      "context_image_query_alt": "Simpler fallback search for the same subject — broader but still topic-related. Used only if the specific query above finds no real photo. 3-4 words, common terms, still tied to the slide subject. GOOD examples for the queries above: 'concrete pour residential site', 'frameless shower door bathroom', 'wood accent wall living room'. BAD: completely generic single words ('construction', 'home') OR off-topic queries (a slide about tile must not fall back to a query about roofing)."
    }},
    {{
      "slide": 3,
      "visual_hint": "context-image or none — use context-image for at least 1 of the 3 list items in slide3_items",
      "context_image_query": "Pexels/Wikimedia search for the LIST items on SLIDE 3 (slide3_items field) — different subject from slide 2 query. Must include material/action + location. GOOD: 'roof shingles GAF installation aerial residential', 'framing wood stud wall addition oak park illinois'. BAD: 'construction', 'building', 'outdoor work'. Query MUST differ from slide 2 query.",
      "context_image_query_alt": "Simpler fallback for the slide 3 subject — same rules as slide 2's _alt. Must stay related to slide 3 content, never overlap with slide 2 or slide 4 alts."
    }},
    {{
      "slide": 4,
      "visual_hint": "context-image or none — use context-image when slide4_body describes a specific tool, material, or technique",
      "context_image_query": "Pexels/Wikimedia search for the TIP on SLIDE 4 (slide4_body field) — show the solution or tool being described. Different subject from slides 2 and 3. GOOD: 'contractor measuring kitchen cabinet installation south florida', 'outdoor kitchen pergola concrete patio residential'. BAD: 'contractor', 'renovation', 'home project'. Query MUST differ from both slide 2 and slide 3 queries.",
      "context_image_query_alt": "Simpler fallback for the slide 4 subject — same rules as slide 2's _alt. Must stay related to slide 4 content, never overlap with slide 2 or slide 3 alts."
    }}
  ],
  "clip_suggestions": [
    {{
      "slide": 1,
      "layout_hint": "A|D — A for framed sticker/tool/product clip; D only when the clip can work as a full-bleed background under dark overlay",
      "subject_type": "person|place|event|product|tool|process|material|concept",
      "text_density": "low|medium|high — use low for layout D so text remains readable over video",
      "youtube_query": "YouTube search for COVER — construction tutorial or timelapse matching the topic. GOOD: 'roof shingles installation timelapse 2024', 'concrete driveway pour residential how to', 'kitchen cabinet install frameless tutorial'. Use 4-6 words. Avoid brand names unless they are the subject.",
      "instagram_query": "Instagram/hashtag phrasing for the same subject — lowercase, no hashtag symbol. e.g. 'residential roofing installation'",
      "giphy_query": "Only for simple tool/material/action accent loops; otherwise empty string. GOOD: 'concrete pour', 'tile installation'. BAD: meme reactions for OPC.",
      "pexels_query": "Pexels stock video — specific material/action for COVER. GOOD: 'roof shingles installation aerial residential', 'concrete driveway pour south florida', 'kitchen remodel frameless cabinet install'. NO proper names. MINIMUM 4 words.",
      "pixabay_query": "Different wording from pexels_query — same subject, different angle. e.g. 'residential roofing contractor work' vs 'asphalt shingle install timelapse'",
      "archive_query": "Public-domain construction footage phrasing. e.g. 'residential construction 1970s archival', 'roofing trade work vintage footage'",
      "wikimedia_query": "CC-licensed construction or materials clip. e.g. 'asphalt shingle installation Commons'",
      "motion_prompt": "5s direction: camera move + mood. e.g. 'slow aerial push-in on residential roof being installed, golden hour, cinematic'",
      "motion_renderer": "remotion",
      "visual_hint": "product-photo"
    }},
    {{
      "slide": 3,
      "layout_hint": "A|B — A for small tool/product/action sticker; B for medium place/process window. Never D on dense text slides.",
      "subject_type": "person|place|event|product|tool|process|material|concept",
      "text_density": "low|medium|high",
      "youtube_query": "YouTube search matching SLIDE 3 subject — different query from cover. e.g. 'how to inspect roof before buying home', 'permit process residential addition explained'",
      "instagram_query": "Instagram phrasing for slide 3 subject — lowercase, no hashtag symbol",
      "giphy_query": "Only for simple tool/material/action accent loops; otherwise empty string.",
      "pexels_query": "Pexels stock video matching SLIDE 3 content — specific material/process. Different from cover query.",
      "pixabay_query": "Different wording same subject as slide 3 pexels_query",
      "archive_query": "Archival phrasing for slide 3 subject",
      "wikimedia_query": "CC clip for slide 3 subject",
      "motion_prompt": "5s direction for slide 3 visual",
      "motion_renderer": "playwright",
      "visual_hint": "context-image"
    }}
  ]
}}

Rules:
- Write as Mike, a South Florida contractor talking directly to a homeowner. First person. Conversational but expert. Florida-licensed (CBC1263425). NEVER promise specific outcomes, results, or timelines. NEVER use superlatives (best, #1, guaranteed). Stats must reference a real source.
- Keep it simple, direct, no jargon
- Stats MUST use ranges (e.g. "$5K-$15K") not exact averages — safer and more honest
- Every stat must name its source in slide2_label or on the sources slide
- Headlines in ALL CAPS
- Slide 1 headline MUST include a number, dollar amount, timeframe, or named loss/risk. Never ship a neutral topic label like "CONCRETE OR PAVERS" as the cover headline.
- Motion metadata is required but manual-only: choose layout_hint from the content, not variety rotation. Use A for a framed sticker/tool/person/product, B for a medium process/place window on middle slides, D only when the cover can be readable as full background video under dark overlay. If unsure, choose A.
- NARRATIVE ARC — NON-NEGOTIABLE: ALL 5 slides must tell ONE connected story. They are chapters, not 5 separate tips.
  SEQUENCE: cover sets the risk/hook → slide2_stat quantifies THAT risk → slide3_items are 3 causes/red-flags OF that same risk → slide4 is the ONE fix for that risk → sources back up the claims.
  HOOK PAYOFF CONTRACT: before writing slide 2, define hook_answer. The carousel must answer the cover promise in plain language by slide 3. If the cover says "AVOID THE $5K MISTAKE", slide 2 or slide 3 must explicitly name the mistake; do not merely compare options.
  THREAD TEST: if you remove the cover slide, can someone read slides 2-4 and still know they're about the SAME topic? If no, rewrite.
  - slide2_headline MUST name the same risk/material/situation as the cover headline (e.g. cover = "3 OUTDOOR KITCHEN RISKS" → slide2_headline = "WHAT THESE COST" or "THE REAL PRICE TAG")
  - slide3_items are the 3 items/causes/red-flags introduced on the cover — NOT 3 different tips on a different subject
  - slide4 addresses specifically what the homeowner does to avoid the risk introduced on slide 1; headline must name the action, not a generic warning label
  BANNED: slides that each cover a different sub-topic (e.g. slide 2 = permits, slide 3 = materials, slide 4 = timeline — these feel like 3 different posts crammed together)
- Caption hook = first line visible in feed — MUST contain at least one of: a specific number, a dollar amount, or a named consequence/risk. BANNED: generic openers like "Here's what you need to know", "Let's talk about", "Important update", "Things homeowners should know". GOOD: "Most homeowners spend $12K-$20K replacing tile they could have saved." | "3 red flags your contractor won't mention until after you sign." | "This one permit mistake delays your project 6-8 weeks."
- NEVER promise what OPC does for clients
- slides[]: emit context-image for at least 2 of the 3 middle slides — never all none
- slide4_body must describe what is happening in the visual (not generic advice)
- context_image_query: BANNED words that make queries too generic and WILL fail stock search — never use alone or as the whole query: "construction", "house", "home", "building", "renovation", "contractor", "kitchen", "bathroom", "outdoor", "indoor", "work", "project". Always combine with material type + location (e.g. "oak park illinois", "south florida", "residential") + action verb (installation, pour, framing, remodel). Minimum 4 words per query. A generic query means the pipeline falls back to AI images — avoid this.
- context_image_query UNIQUENESS: Each slide's context_image_query MUST describe a DIFFERENT visual subject. Slides 2, 3, and 4 must each have a distinct query. NEVER reuse or rephrase a query from another slide. If you find yourself writing the same query twice, stop and change one of them to show a different material, location, or action.
- context_image_query_alt — STOCK-LIBRARY-FRIENDLY (NON-NEGOTIABLE):
  ALWAYS provide a simpler fallback query alongside the main one. The main query is the IDEAL specific match. The _alt is the SAFETY NET that has to actually return real photos from Pexels/Pixabay/Wikimedia.

  GOOD alts (stock libraries have many of these — your test: would a generic search return >20 results?):
    "construction worker on site"
    "concrete pour residential"
    "kitchen cabinet white"
    "asphalt shingle roof installation"
    "bathroom tile shower wall"
    "wood deck backyard"
    "framing wood stud wall"
    "soil ground residential foundation"

  BAD alts (stock libraries don't have these — too narrow, too specific, too jargon-y):
    "geotechnical engineer inspecting residential site"   ← profession + activity — almost no stock photos exist
    "soil testing equipment residential lot"              ← niche equipment — empty results
    "contractor measuring shower door frameless"          ← specific action + product — won't match
    "GAF asphalt shingle warranty inspection"             ← brand + paperwork — never on stock libraries
    "permit inspector approving foundation pour"          ← role + verb — fictional photo

  ON-TOPIC RULE — the _alt MUST stay tied to the slide subject:
    Slide about SOIL/foundation → _alt mentions soil/ground/foundation/dirt. NEVER drift to roofing, kitchens, or unrelated trades.
    Slide about TILE → _alt mentions tile/bathroom/wall/floor. NEVER drift to concrete or roofing.
    Slide about HURRICANE WINDOWS → _alt mentions windows/glass/storm/impact. NEVER drift to siding or insulation.

  EXAMPLE COMPARISON for a soil-testing slide:
    main: "geotechnical engineer inspecting residential building site soil sample" (specific, ~7 words, niche — may return 0 photos)
    _alt: "construction site soil ground residential"  (broad common terms — stock libraries return many — STILL on-topic)

  TEST BEFORE EMITTING: silently ask yourself "would a Pexels search for this _alt return more than 20 results, AND would those results still relate to my slide subject?" If either answer is no, simplify or refocus the _alt.

  Rule of thumb: if the main query has 5-7 words with specific brand/material/location, the _alt has 3-5 words with common substitutes — but those substitutes MUST stay in the same subject lane."""

    for attempt in range(2):
        _prompt = prompt
        if attempt == 1:
            research = _web_research(topic, lang="en")
            if not research:
                break
            print(f"  OPC: retrying with web research for: {topic}")
            _prompt = (
                f"RESEARCH FOUND:\n{research}\n\n"
                "Use this research to fill in missing facts, names, and numbers. "
                "Do not invent. Do not contradict your knowledge.\n\n"
            ) + prompt

        try:
            text = _claude_with_fallback(
                _prompt, max_tokens=2500, timeout=30,
                context=f"carousel_builder.opc(attempt {attempt+1})", model=model,
            )
        except Exception as e:
            print(f"  LLM cascade failed (OPC, attempt {attempt+1}): {e}")
            continue

        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            print(f"  Failed to parse OPC carousel content (attempt {attempt+1})")
            continue

        try:
            parsed = json.loads(json_match.group())
            if not parsed.get("headline"):
                words = re.sub(r"[^a-zA-Z0-9 ]", " ", topic).upper().split()[:4]
                parsed["headline"] = " ".join(words) or "THE GUIDE"
                print(f"  [carousel_builder] headline missing in LLM response — using fallback: {parsed['headline']!r}")
            return _apply_opc_hook_answer_contract(parsed, topic)
        except json.JSONDecodeError as e:
            print(f"  OPC JSON parse error (attempt {attempt+1}): {e}")
            continue

    print(f"  OPC content generation failed after 2 attempts for: {topic}")
    return None


def _apply_opc_hook_answer_contract(content, topic=""):
    """Make strong OPC hooks pay off visibly before the CTA slide."""
    if not isinstance(content, dict):
        return content
    hook_text = " ".join([
        str(content.get("headline") or ""),
        str(content.get("subhead") or ""),
        str(topic or ""),
    ])
    strong_hook = bool(
        re.search(r"\b(mistake|trap|avoid|cost|overpay|risk|loss|lose|surprise)\b", hook_text, re.I)
        and re.search(r"[$%]|\d", hook_text)
    )
    if not strong_hook:
        return content

    answer = str(content.get("hook_answer") or "").strip()
    if not answer:
        answer = "The mistake: comparing only the upfront price instead of the total cost over time."
        content["hook_answer"] = answer
    elif not re.match(r"^\s*the\s+(mistake|trap|risk|answer)\s*:", answer, re.I):
        answer = f"The mistake: {answer[0].lower()}{answer[1:]}" if answer else answer
        content["hook_answer"] = answer

    items = content.get("slide3_items")
    if not isinstance(items, list):
        items = []
    while len(items) < 3:
        items.append({"title": "Decision point", "sub": ""})

    visible = " ".join([
        str(content.get("slide2_label") or ""),
        " ".join(str((i or {}).get("title", "")) + " " + str((i or {}).get("sub", "")) for i in items if isinstance(i, dict)),
        str(content.get("slide4_body") or ""),
    ]).lower()
    answer_core = re.sub(r"^\s*the\s+(mistake|trap|risk|answer)\s*:\s*", "", answer, flags=re.I).strip()
    if answer_core and answer_core.lower() not in visible:
        first = items[0] if isinstance(items[0], dict) else {}
        first["title"] = "THE MISTAKE"
        first["sub"] = answer_core[:120]
        items[0] = first
        content["slide3_items"] = items[:3]

    body = str(content.get("slide4_body") or "").strip()
    if answer_core and answer_core.lower() not in body.lower():
        content["slide4_body"] = (
            f"That's the mistake I want you to avoid: {answer_core} "
            f"{body}"
        ).strip()

    return content


# ── Phase 8A — per-template OPC content generation ─────────────────────────
# When OPC_SLIDE_PLANNER_ENABLED=1, each standalone slide in the plan needs
# its own nested content dict matching SLIDE_REQUIRED_FIELDS in the chooser.
# generate_carousel_content() only emits tip-shape content. This helper runs
# AFTER the planner and AFTER generate_carousel_content(), then merges each
# template's nested fields into content under content["opc_<template>"].
#
# Char-length limits enforced via prompt + post-validation truncation. The
# tip-shape fields stay intact — used by tip slides + as fallback context.

# Image-query field appended to every standalone schema below — Phase 8D.
# A SINGLE field per template (image_query) tells the fetcher what kind of
# photo to search for. Four-card grid uses card_image_queries (list of 4)
# instead of a single query.
OPC_STANDALONE_SCHEMAS = {
    "opc_material_profile": {
        "label":            ("eyebrow above headline, ALL CAPS · OPC suffix",  30),
        "headline_main":    ("subject NAME in ALL CAPS — NEVER a question. e.g. 'CONCRETE', 'REBAR', 'SHIPLAP', 'POURED CONCRETE'. NEVER 'What is', 'Why is', 'How is'.", 12),
        "headline_italic":  ("sharp 2-5 word descriptor giving the angle. NEVER ends in '?'. e.g. 'the bonded backbone', '20-year guaranteed', 'the cost king', 'Florida-built since 78'. Together with headline_main forms a noun phrase, NEVER a question.", 22),
        "best_for":         ("1 short phrase — what this material is best for", 40),
        "not_ideal":        ("1 short phrase — where this material falls short", 40),
        "durability":       ("lifespan in years (e.g. '30+ years', '8-15 years')", 30),
        "install_notes":    ("1 sentence on what install requires (skill, tools, time)", 80),
        "cost_range":       ("$X-$Y range (e.g. '$5K-$30K', '$8/sqft-$22/sqft')", 25),
        "style_fit":        ("which design styles match (e.g. 'Modern, coastal, transitional')", 40),
        "decision_factors": ("LIST of exactly 4 short labels (each ≤12 chars) shown as buttons at bottom — e.g. ['Spacing','Cover','Mix','Drainage']", 0),
        "image_query":      ("OPTIONAL Pexels search for an optional material thumbnail — leave empty string for text-only profile (preferred)", 0),
    },
    "opc_four_card_grid": {
        "eyebrow":     ("ALL CAPS eyebrow · OPC suffix", 30),
        "headline_main":   ("first half of headline (e.g. 'Four')", 10),
        "headline_italic": ("second half italic (e.g. 'checks.', 'options.')", 18),
        "subhead":     ("1 short sentence framing the 4 cards", 80),
        "badges":      ("LIST of exactly 4 short labels for card top-right pill (e.g. ['A','B','C','D'] or ['NEW','POP','PRO','BUDGET'])", 0),
        "card_titles": ("LIST of exactly 4 card titles, ALL CAPS, max 18 chars each (e.g. ['SPACING','COVER','TIES','CHAIRS'])", 0),
        "card_copies": ("LIST of exactly 4 short body lines, max 100 chars each, one per card", 0),
        "card_image_queries": ("LIST of exactly 4 Pexels search queries — ONE per card, each tied to that card's title (e.g. for SPACING: 'rebar grid concrete slab installation residential'). Each MUST be 4+ words and specific to its card.", 0),
    },
    "opc_item_spotlight": {
        "tag":        ("eyebrow tag · OPC suffix", 30),
        "category":   ("ALL CAPS category line (e.g. 'PRODUCT · MATERIAL · TECHNIQUE')", 40),
        "headline_main":   ("ALL CAPS item NAME — the spotlighted product/material in 1-3 words. NEVER a question, NEVER 'Spotlight on'. e.g. 'FRAMELESS GLASS', 'OAK FLOORING', 'GAF SHINGLE'.", 14),
        "headline_italic": ("sharp italic descriptor giving the angle. NEVER ends in '?'. e.g. 'the South Florida pick', 'cabinet-grade only', 'IRC-compliant'.", 22),
        "subhead":    ("1 short framing line", 80),
        "fact_1_title": ("ALL CAPS short title", 18),
        "fact_1_desc":  ("1 line detail (max 80 chars)", 80),
        "fact_2_title": ("ALL CAPS short title", 18),
        "fact_2_desc":  ("1 line detail", 80),
        "fact_3_title": ("ALL CAPS short title", 18),
        "fact_3_desc":  ("1 line detail", 80),
        "fact_4_title": ("ALL CAPS short title", 18),
        "fact_4_desc":  ("1 line detail", 80),
        "image_query":  ("Pexels search for a single close-up of the spotlighted item. 4+ words. e.g. 'frameless cabinet door close-up modern kitchen'", 0),
    },
    "opc_statement": {
        "tag":          ("ALL CAPS tag (e.g. 'FROM THE FIELD')", 30),
        "quote_opener": ("short punchy opening phrase to the quote (max 40 chars)", 40),
        "quote_body":   ("the quoted line — 1-2 sentences (max 200 chars), no surrounding quote marks", 200),
        "attribution":  ("ALL CAPS attribution (e.g. 'MIKE · OPC FOUNDER')", 30),
        "image_query":  ("Pexels search for a portrait/person photo. 4+ words. e.g. 'construction worker portrait helmet south florida residential' — gets B&W treatment via CSS", 0),
    },
    "opc_base": {
        "tag":           ("OPC / TIP eyebrow", 25),
        "headline_main": ("ALL CAPS subject NAME — the topic of the tip in 1-3 words. NEVER 'WHAT'S', NEVER a question opener. e.g. 'HIDDEN COSTS', 'WALL CRACKS', 'PERMIT TRAPS', 'CONCRETE PRO'.", 12),
        "headline_italic": ("sharp italic angle in 2-5 words. NEVER ends in '?'. e.g. 'before they hit you', 'what inspectors flag', 'the 20-year version'.", 22),
        "cover_hook":    ("1 short subhead (max 80 chars)", 80),
        "byline":        ("MIKE · ROLE format", 25),
        "stamp_text":    ("badge text on sticker portrait, e.g. 'TIP', '#001'", 12),
        "image_query":   ("Pexels/OPC catalog search for the cover hero photo (full-bleed bg). 4+ words. e.g. 'kitchen demolition wall behind cabinets residential remodel'", 0),
    },
    "opc_progress_media": {
        "tag":              ("Project Progress · Field Update style tag", 30),
        "eyebrow":          ("Oak Park Construction or short label", 25),
        "title_main":       ("first half of headline (e.g. 'What changed')", 14),
        "title_italic":     ("second half italic (e.g. 'on site?')", 22),
        "description_bold": ("first 1-3 words of description, bold (e.g. 'Real proof.')", 30),
        "description_rest": ("rest of description (max 120 chars)", 120),
        "caption_pills":    ("LIST of 3 short ALL CAPS pills (default ['BEFORE','DURING','AFTER'] or ['DAY 1','DAY 14','DAY 30'])", 0),
        "image_query":      ("OPC catalog search for a real jobsite photo — required, no stock. 4+ words. e.g. 'kitchen remodel pompano beach during demolition cabinets'", 0),
    },
    "opc_duotone": {
        "variant":         ("'v1' | 'v2' | 'v3' — picks the duotone color filter (v1 navy→lime, v2 navy→yellow, v3 teal→lime). Default v1.", 4),
        "claim_main":      ("opening punch (e.g. 'Watch out:')", 20),
        "claim_strong":    ("bold middle (e.g. 'this can cost you')", 25),
        "claim_rest":      ("rest of claim before underline (max 80 chars)", 80),
        "claim_underline": ("phrase to underline (max 30 chars, optional)", 30),
        "claim_final":     ("closing phrase (max 30 chars, optional)", 30),
        "quote_text":      ("quoted body line (max 200 chars), no surrounding quote marks", 200),
        "attribution":     ("Mike McFolling · GC style attribution", 30),
        "image_query":     ("Pexels search for high-contrast hero photo, dramatic, suitable for duotone. 4+ words. e.g. 'concrete formwork wood structure dramatic shadow construction site'", 0),
    },
}


def _truncate_to_limit(value, limit):
    """Hard-truncate to char limit; returns string or list as appropriate."""
    if limit <= 0:
        return value  # 0 = list field, no scalar limit
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    return value


_OPC_AI_FIELD_LABEL_RE = re.compile(
    r"^\s*(?:slide\s*\d+\s*[-:]\s*)?"
    r"(?:hook|cta|body|title|intro|outro|headline|subhead|caption|quote|copy|text)\s*:\s*",
    re.IGNORECASE,
)


def _clean_opc_generated_value(value, scalar: bool = False):
    """Normalize LLM-filled template fields before they hit HTML.

    This strips accidental field labels such as "body: ..." and turns scalar
    fields that came back as one-item lists into plain strings.
    """
    if scalar and isinstance(value, list):
        value = next((x for x in value if str(x).strip()), "")
    if isinstance(value, str):
        cleaned = value.strip()
        for _ in range(2):
            new = _OPC_AI_FIELD_LABEL_RE.sub("", cleaned).strip()
            if new == cleaned:
                break
            cleaned = new
        return cleaned
    if isinstance(value, list):
        return [_clean_opc_generated_value(x, scalar=False) for x in value]
    if isinstance(value, dict):
        return {k: _clean_opc_generated_value(v, scalar=False) for k, v in value.items()}
    return value


def _coerce_list_4(value, fallback):
    """Force a value to a 4-item list of strings, padding with fallback as needed."""
    if isinstance(value, list):
        out = [str(x) for x in value[:4]]
    elif isinstance(value, str):
        out = [value]
    else:
        out = []
    while len(out) < 4:
        out.append(fallback[len(out)] if len(out) < len(fallback) else fallback[-1])
    return out


def _coerce_list_3(value, fallback):
    if isinstance(value, list):
        out = [str(x) for x in value[:3]]
    elif isinstance(value, str):
        out = [value]
    else:
        out = []
    while len(out) < 3:
        out.append(fallback[len(out)] if len(out) < len(fallback) else fallback[-1])
    return out


def _derive_standalone_from_tip(template_id, tip_content):
    """Phase 8F — derive topic-specific standalone content from tip-shape
    fields when Haiku per-template generation isn't available. Returns a
    partial dict that lets the standalone renderers ship populated copy
    without "—" placeholders even if the LLM cascade fully fails.

    Topic-aware labels: instead of "MATERIAL PROFILE · OPC", emit
    "<ACCENT> PROFILE · OPC" (e.g. "REBAR PROFILE · OPC"). This passes
    Phase 8G reviewer gates because it's not in the placeholder set.
    """
    headline = (tip_content or {}).get("headline", "")
    accent   = (tip_content or {}).get("accent_word", "") or ""
    subhead  = (tip_content or {}).get("subhead", "")
    items    = (tip_content or {}).get("slide3_items", []) or []
    s4_hl    = (tip_content or {}).get("slide4_headline", "")
    s4_body  = (tip_content or {}).get("slide4_body", "")

    # Split headline at last word into main + italic for the standalone two-part pattern.
    parts = headline.split() if headline else []
    if len(parts) >= 2:
        hl_main = " ".join(parts[:-1])
        hl_em = parts[-1].rstrip("?.")
    else:
        hl_main = (headline or "On topic").title()
        hl_em   = accent.lower() if accent else "today"

    # Topic-specific eyebrow token (avoids the "MATERIAL PROFILE · OPC" placeholder).
    accent_token = accent.upper().strip("?.,") if accent else (parts[0].upper().strip("?.,") if parts else "OPC")

    if template_id == "opc_material_profile":
        factors = [(i.get("title", "").split() or [accent_token])[0][:12].title() for i in items[:4]]
        while len(factors) < 4:
            extra_pool = ["Spec", "Cost", "Lead", "Style", "Code", "Risk"]
            factors.append(extra_pool[len(factors) % len(extra_pool)])
        return {
            "label":            f"{accent_token} PROFILE · OAK PARK",
            "headline_main":    accent_token or "MATERIAL",
            "headline_italic":  "the South Florida pick",
            "best_for":         (subhead.split(".")[0] if subhead else f"{accent_token} use cases")[:40],
            "not_ideal":        (s4_hl.title() if s4_hl else f"Wrong-spec {accent.lower()}")[:40],
            "durability":       "30+ years",
            "install_notes":    (s4_body or subhead or f"Specify {accent.lower() or 'spec'} early — install is detail-driven.")[:80],
            "cost_range":       "$5K–$30K",
            "style_fit":        "South Florida residential",
            "decision_factors": factors,
        }

    if template_id == "opc_four_card_grid":
        cards = items[:]
        while len(cards) < 4:
            cards.append({"title": (accent or "Step").title()[:18], "sub": (subhead or "")[:100]})
        return {
            "eyebrow":         f"{accent_token} BREAKDOWN · OAK PARK",
            "headline_main":   "Compare",
            "headline_italic": f"{accent.lower() or 'options'}.",
            "subhead":         (subhead or f"Four ways {accent.lower() or 'this'} can change the build.")[:80],
            "badges":          ["A", "B", "C", "D"],
            "card_titles":     [str(c.get("title", accent_token))[:18].upper() for c in cards[:4]],
            "card_copies":     [str(c.get("sub", subhead))[:100] for c in cards[:4]],
        }

    if template_id == "opc_item_spotlight":
        f1 = items[0] if len(items) > 0 else {"title": accent_token, "sub": subhead}
        f2 = items[1] if len(items) > 1 else {"title": "Cost", "sub": (subhead or "")[:80]}
        f3 = items[2] if len(items) > 2 else {"title": "Install", "sub": (subhead or "")[:80]}
        f4 = {"title": s4_hl or "Pro check", "sub": s4_body or subhead or ""}
        return {
            "tag":             f"{accent_token} SPOTLIGHT · OAK PARK",
            "category":        "PRODUCT · MATERIAL · TECHNIQUE",
            "headline_main":   accent_token or "THIS DETAIL",
            "headline_italic": "Florida-built only",
            "subhead":         (subhead or "")[:80],
            "fact_1_title":    str(f1.get("title", accent_token))[:18].upper(),
            "fact_1_desc":     str(f1.get("sub", ""))[:80],
            "fact_2_title":    str(f2.get("title", "Cost"))[:18].upper(),
            "fact_2_desc":     str(f2.get("sub", ""))[:80],
            "fact_3_title":    str(f3.get("title", "Install"))[:18].upper(),
            "fact_3_desc":     str(f3.get("sub", ""))[:80],
            "fact_4_title":    str(f4.get("title", "Pro check"))[:18].upper(),
            "fact_4_desc":     str(f4.get("sub", ""))[:80],
        }

    if template_id == "opc_statement":
        return {
            "tag":          f"FROM THE FIELD · {accent_token}",
            "quote_opener": (s4_hl.title() if s4_hl else f"On {accent.lower() or 'this'},")[:40],
            "quote_body":   (s4_body or subhead or f"Skipping the {accent.lower() or 'detail'} step is the most expensive call we make.")[:200],
            "attribution":  "MIKE · OPC FOUNDER",
        }

    if template_id == "opc_base":
        return {
            "tag":             f"OAK PARK · {accent_token}",
            "headline_main":   (hl_main.upper().rstrip("?") if hl_main else accent_token) or "THE TIP",
            "headline_italic": (hl_em.upper().rstrip("?") if hl_em else "what inspectors flag"),
            "cover_hook":      (subhead or "")[:80],
            "byline":          "MIKE · OPC FOUNDER",
            "stamp_text":      accent_token[:12] or "TIP",
        }

    if template_id == "opc_progress_media":
        # Use the topic-specific accent in the tag so reviewer doesn't see
        # the same "Project Progress · Field Update" boilerplate on every post.
        return {
            "tag":              f"PROGRESS · {accent_token}",
            "eyebrow":          "Oak Park Construction · Pompano Beach",
            "title_main":       "What changed",
            "title_italic":     f"on the {accent.lower() or 'site'}?",
            "description_bold": (s4_hl.title() if s4_hl else "Real proof.")[:30],
            "description_rest": (subhead or s4_body or "")[:120],
            "caption_pills":    ["BEFORE", "DURING", "AFTER"],
        }

    if template_id == "opc_duotone":
        # Pick variant from accent semantics: cost-related → v2, success → v3.
        cost_words = {"cost", "price", "money", "delay", "lose", "lost", "fail", "broken"}
        proof_words = {"proof", "win", "saved", "built", "after", "result"}
        accent_low = accent.lower()
        if any(w in accent_low for w in cost_words):
            variant = "v2"
        elif any(w in accent_low for w in proof_words):
            variant = "v3"
        else:
            variant = "v1"
        return {
            "variant":         variant,
            "claim_main":      f"On {accent.lower() or 'this'}:" if accent else "Watch out —",
            "claim_strong":    (s4_hl.lower() if s4_hl else f"this changes the build")[:25],
            "claim_rest":      (subhead or "")[:80],
            "claim_underline": (accent or "")[:30],
            "claim_final":     "",
            "quote_text":      (s4_body or subhead or "")[:200],
            "attribution":     "Mike McFolling · GC",
        }

    return {}


def generate_opc_per_template_content(topic, plan, tip_content, brief="", model="claude-sonnet-4-6"):
    """Phase 8A — generate per-template nested content for every standalone
    slide in the plan. Adds keys like content['opc_material_profile'] = {...}
    onto the existing tip-shape content dict, leaving tip-shape fields intact.

    Returns a dict mapping template_id → field-dict for the standalones in
    the plan. Caller merges into content. Falls back to derive-from-tip when
    Haiku fails so renderers never show '—' placeholders.
    """
    if not tip_content or not isinstance(tip_content, dict):
        return {}
    slides = (plan or {}).get("slides") or []
    standalone_ids = []
    for s in slides:
        tid = s.get("template_id", "")
        if tid in OPC_STANDALONE_SCHEMAS and tid not in standalone_ids:
            standalone_ids.append(tid)
    if not standalone_ids:
        return {}  # plan only uses tip components — nothing to do

    # Build the per-template schema block for the prompt.
    schema_block = []
    for tid in standalone_ids:
        fields = OPC_STANDALONE_SCHEMAS[tid]
        lines = [f"  {{ // {tid}"]
        for k, (desc, limit) in fields.items():
            cap = f"max {limit} chars" if limit > 0 else "list field"
            lines.append(f'    "{k}": "<{desc}> ({cap})",')
        lines.append("  }")
        schema_block.append("\n".join(lines))

    # Tip-shape context — gives Haiku the existing copy + tone for coherence.
    tip_ctx = {
        "headline":        tip_content.get("headline", ""),
        "accent_word":     tip_content.get("accent_word", ""),
        "subhead":         tip_content.get("subhead", ""),
        "slide2_stat":     tip_content.get("slide2_stat", ""),
        "slide2_label":    tip_content.get("slide2_label", ""),
        "slide3_items":    tip_content.get("slide3_items", []),
        "slide4_headline": tip_content.get("slide4_headline", ""),
        "slide4_body":     tip_content.get("slide4_body", ""),
    }

    # SH-138: build a per-template purpose map from the plan's slide indices.
    # Empty string when pilot disabled so the prompt format is unchanged on cron.
    purpose_block_smart = ""
    if SLIDE_PURPOSE_PILOT:
        n_total = len(slides) or 5
        tmpl_to_purpose = {}
        for s in slides:
            idx = s.get("slide", 0)
            tid = s.get("template_id", "")
            purpose = SLIDE_PURPOSE_OPC_BY_INDEX.get(idx, "middle")
            if tid in standalone_ids and tid not in tmpl_to_purpose:
                tmpl_to_purpose[tid] = (idx, purpose)
        if tmpl_to_purpose:
            lines = [f"  - {tid}: slide {idx} → purpose='{p}'" for tid, (idx, p) in tmpl_to_purpose.items()]
            purpose_block_smart = (
                "\n=== SH-138 SLIDE PURPOSE PILOT (advisory, non-blocking) ===\n"
                "OPC narrative spine: hook | cost | teach | apply | sources.\n"
                f"Each standalone template fills a specific slide whose narrative purpose is fixed:\n"
                + "\n".join(lines) + "\n"
                "Each template's content MUST visibly serve its assigned purpose.\n"
                "Add a top-level field `slide_purpose` to EACH template's nested dict, e.g.\n"
                '  "opc_material_profile": { ..., "slide_purpose": "teach" }\n'
                "=== END SLIDE PURPOSE PILOT ===\n"
            )

    # OPC story spine — MANDATORY, always injected regardless of SLIDE_PURPOSE_PILOT.
    # Each standalone template must visibly serve its assigned narrative act.
    _spine_for_standalones = []
    for _s in slides:
        _idx = _s.get("slide", 0)
        _tid = _s.get("template_id", "")
        if _tid in standalone_ids and _idx in SLIDE_PURPOSE_OPC_BY_INDEX:
            _spine_for_standalones.append(
                f"  Slide {_idx} ({_tid}) → {SLIDE_PURPOSE_OPC_BY_INDEX[_idx].upper()}"
            )

    opc_story_spine_block = ""
    if _spine_for_standalones:
        opc_story_spine_block = (
            "\n=== MANDATORY OPC STORY SPINE — NON-NEGOTIABLE ===\n"
            "Every OPC carousel tells ONE story in 5 acts:\n"
            "  Slide 1 HOOK    — stop the scroll with a number, cost, risk, or timeframe.\n"
            "  Slide 2 COST    — show what goes wrong or what it costs the homeowner.\n"
            "  Slide 3 TEACH   — reveal one root cause or key decision insight.\n"
            "  Slide 4 APPLY   — give the ONE concrete action Mike uses on every job.\n"
            "  Slide 5 SOURCES — result proof + CTA + attribution.\n"
            "These standalone templates are assigned to specific acts:\n"
            + "\n".join(_spine_for_standalones) + "\n\n"
            "Each template's fields MUST serve its act. Examples:\n"
            "  COST slide: surface a dollar amount or risk ('skip this = $4K repair').\n"
            "  TEACH slide: reveal a root cause or framework, not just describe the topic.\n"
            "  APPLY slide: one concrete action Mike recommends. Not a list. One thing.\n"
            "Generic filling ('here is some information about X') is BANNED.\n\n"
            "SLIDE 1 HOOK RULES (if any standalone template lands on slide 1):\n"
            "  cover_hook, subhead, or opening text MUST include at least one of:\n"
            "    • a number or percentage (e.g. '72% of homeowners skip this')\n"
            "    • a dollar amount (e.g. 'costs $3K–$8K on average')\n"
            "    • a timeframe (e.g. 'within 2 years', 'after one rainy season')\n"
            "    • a loss/risk statement (e.g. 'you’re overpaying', 'most skip this and pay twice')\n"
            "  BANNED hook openers: 'Here’s what you need to know', 'A quick tip about',\n"
            "    'Did you know', 'Let’s talk about', 'Today we’re covering'.\n\n"
            "BANNED GENERIC TITLES (applies to ALL template fields: headline_main, card_titles, title_main):\n"
            "  NEVER use: THE LIST · WHAT TO KNOW · TIPS · THINGS TO CONSIDER · KEY POINTS\n"
            "             OVERVIEW · SUMMARY · INTRODUCTION · FACTS · GUIDE · INFO\n"
            "  Instead: name the specific material, cost, risk, or outcome.\n"
            "  GOOD: 'HIDDEN COSTS', 'REBAR SPACING', 'PERMIT TRAPS', 'CONCRETE VS PAVERS'\n"
            "  BAD:  'KEY POINTS', 'WHAT TO KNOW', 'TIPS', 'THE LIST'\n"
            "=== END MANDATORY OPC STORY SPINE ===\n"
        )

    prompt = f"""You are filling in approved Instagram carousel TEMPLATES for Oak Park Construction (South Florida contractor, license CBC1263425, voice = Mike, first-person, conversational expert).

Topic: "{topic}"
{_comparison_prompt_block(topic, brief)}
{opc_story_spine_block}{purpose_block_smart}
You ALREADY produced this tip-shape carousel content (use as the SOURCE of facts/voice — do NOT contradict it):
{json.dumps(tip_ctx, indent=2)}

Your job: produce the per-template nested fields for these {len(standalone_ids)} standalone templates the planner picked:
{', '.join(standalone_ids)}

Return ONLY a JSON object keyed by template_id. Each value is a dict matching EXACTLY this schema:

{{
{','.join('  "' + tid + '": ' + chr(10) + schema_block[i] for i, tid in enumerate(standalone_ids))}
}}

CRITICAL RULES:
- Char-length limits are HARD. If a field says max 40 chars, the value MUST be ≤40 chars. Truncate gracefully — do not exceed.
- LIST fields (badges, card_titles, card_copies, decision_factors, caption_pills) MUST have EXACTLY the count specified.
- Stay coherent with the tip-shape facts above. Do NOT invent new numbers. Re-use slide3_items when filling card_titles/card_copies/decision_factors.
- Florida-residential context. No promises. No superlatives.
- For opc_duotone: "variant" defaults to "v1". Pick "v2" only if topic is about a financial cost/risk; pick "v3" only if topic is about success/proof. Most should stay v1.
- For opc_progress_media: tag should reflect the actual project type. caption_pills follow project timeline (BEFORE/DURING/AFTER is the safe default).
- For opc_material_profile.decision_factors: pull 4 short tokens from slide3_items[].title (first word, ≤12 chars each).
- For opc_four_card_grid.card_titles + card_copies: if 4 items aren't naturally available, derive 4 distinct decision points from the topic.
- For opc_four_card_grid.card_image_queries on comparison topics: return exactly 4 balanced queries. At least one query must name Subject A, at least one must name Subject B, and the set should alternate/split the two sides when possible.

FOUR_CARD_GRID — UNIFIED COMPARISON FRAME (NON-NEGOTIABLE):
- For comparison topics ("X vs Y", "A or B", "Which wins"): ALL 4 cards MUST follow ONE structure. Pick exactly ONE pattern and apply it to every card:
  (A) HEAD-TO-HEAD per card — every card compares BOTH subjects inside its body. Card title = the dimension. Card copy = "Subject A: <data> / Subject B: <data>".
      GOOD example for "Concrete vs Pavers":
        card 1: title "DURABILITY" / copy "Concrete: 20-30y. Pavers: 25-50y when reset."
        card 2: title "INSTALL" / copy "Concrete: 1-2 days pour + cure. Pavers: 3-5 days hand-set."
        card 3: title "REPAIR" / copy "Concrete: replace slab section. Pavers: lift and reset individual stones."
        card 4: title "COST" / copy "Concrete: $6-$12/sqft. Pavers: $10-$30/sqft installed."
  (B) WINNER-PER-DIMENSION — every card declares the winner upfront in the title.
      GOOD example for "Concrete vs Pavers":
        card 1: title "DURABILITY · PAVERS WIN" / copy "25-50y when reset; concrete typically 20-30y."
        card 2: title "INSTALL SPEED · CONCRETE WINS" / copy "1-2 days pour vs 3-5 days hand-set."
        card 3: title "REPAIR · PAVERS WIN" / copy "Lift and reset individual stones; concrete needs slab work."
        card 4: title "BUDGET · CONCRETE WINS" / copy "$6-$12/sqft vs $10-$30/sqft installed."

  BAD example (NEVER ship this — drifting subjects):
    card 1: title "DURABILITY" / copy "Concrete lasts 20-30 years."  ← only about Subject A
    card 2: title "AESTHETICS" / copy "Pavers offer diverse design choices." ← only about Subject B
    card 3: title "WARRANTY" / copy "Concrete typically comes with a 5-year warranty." ← back to A
    card 4: title "DRAINAGE" / copy "Pavers allow water to permeate." ← back to B
  This drift is BANNED. The reader cannot tell what the slide is recommending.

- For NON-comparison topics (single subject, e.g. "Hurricane Window Installation"): all 4 cards must focus on the SAME subject from 4 distinct angles — the standard quartet is COST, INSTALL, MAINTENANCE, LONGEVITY. Other valid quartets: SPEC, CODE, RISK, STYLE. Never mix unrelated angles (e.g. don't pair COST with HISTORY).
- For opc_statement.quote_body: quote Mike, no quotation marks in the value (the template adds them).
- ALL CAPS where the schema says ALL CAPS.
- No emojis. No markdown.

Return JSON ONLY, no preamble."""

    try:
        text = _claude_with_fallback(
            prompt, max_tokens=2500, timeout=40,
            context=f"opc_per_template({','.join(standalone_ids)})", model=model,
        )
    except Exception as e:
        print(f"  [phase8a] LLM failed: {e!r} — using tip-derived fallback for all standalones")
        return {tid: _derive_standalone_from_tip(tid, tip_content) for tid in standalone_ids}

    json_match = re.search(r'\{[\s\S]*\}', text or "")
    if not json_match:
        print("  [phase8a] no JSON in response — using tip-derived fallback")
        return {tid: _derive_standalone_from_tip(tid, tip_content) for tid in standalone_ids}

    try:
        raw = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"  [phase8a] JSON parse error: {e} — using tip-derived fallback")
        return {tid: _derive_standalone_from_tip(tid, tip_content) for tid in standalone_ids}

    # Validate + truncate per schema. Fill any missing template/field from tip-derived fallback.
    out = {}
    for tid in standalone_ids:
        haiku_block = raw.get(tid) if isinstance(raw, dict) else None
        fallback = _derive_standalone_from_tip(tid, tip_content)
        merged = dict(fallback)
        if isinstance(haiku_block, dict):
            for k, (_, limit) in OPC_STANDALONE_SCHEMAS[tid].items():
                if k in haiku_block and haiku_block[k] not in (None, ""):
                    cleaned = _clean_opc_generated_value(haiku_block[k], scalar=limit > 0)
                    merged[k] = _truncate_to_limit(cleaned, limit)
        for k, (_, limit) in OPC_STANDALONE_SCHEMAS[tid].items():
            if k in merged:
                merged[k] = _clean_opc_generated_value(merged[k], scalar=limit > 0)
        # Coerce list fields to required cardinality.
        if tid == "opc_material_profile":
            merged["decision_factors"] = _coerce_list_4(
                merged.get("decision_factors"),
                ["Quality", "Cost", "Speed", "Style"],
            )[:4]
            merged["decision_factors"] = [str(x)[:12] for x in merged["decision_factors"]]
        elif tid == "opc_four_card_grid":
            merged["badges"]      = _coerce_list_4(merged.get("badges"), ["A", "B", "C", "D"])[:4]
            merged["card_titles"] = [str(x)[:18].upper() for x in _coerce_list_4(
                merged.get("card_titles"), ["OPTION 1", "OPTION 2", "OPTION 3", "OPTION 4"])][:4]
            merged["card_copies"] = [str(x)[:100] for x in _coerce_list_4(
                merged.get("card_copies"), ["Detail one.", "Detail two.", "Detail three.", "Detail four."])][:4]
            # Phase 8D: 4 distinct image queries, one per card.
            ciq_fallback = [f"{merged['card_titles'][i].lower()} construction detail residential" for i in range(4)]
            merged["card_image_queries"] = [str(x)[:120] for x in _coerce_list_4(
                merged.get("card_image_queries"), ciq_fallback)][:4]
        elif tid == "opc_progress_media":
            merged["caption_pills"] = _coerce_list_3(
                merged.get("caption_pills"), ["BEFORE", "DURING", "AFTER"],
            )[:3]
            merged["caption_pills"] = [str(x)[:8].upper() for x in merged["caption_pills"]]
        elif tid == "opc_duotone":
            v = str(merged.get("variant", "v1")).strip().lower()
            if v not in ("v1", "v2", "v3"):
                v = "v1"
            merged["variant"] = v
        out[tid] = merged
    print(f"  [phase8a] generated per-template content for: {', '.join(out.keys())}")
    return out


def generate_dados_content(topic, brief="", capture_brief=None):
    """Generate FORMAT-019 Dados ou Agenda? bias-check carousel (9 slides, PT-BR).
    Uses the brief from analyze_bias() as the primary source — never invents facts.
    Structure: cover → post-context → data 1 → data 2 → what was missing →
               exaggeration check → VERDICT (3-way score) → conclusion → CTA/sources.

    capture_brief: required context from /capture. If None or empty, raises ValueError
    so the pipeline skips this post rather than letting Haiku invent content.
    """
    # FIX 5: brief gate — do not generate FORMAT-019 without a capture brief
    effective_brief = capture_brief if capture_brief else brief
    if not effective_brief or not str(effective_brief).strip():
        raise ValueError(
            "capture_brief required for FORMAT-019 (Dados ou Agenda?) — run /capture first. "
            "Haiku must NOT invent bias analysis without a real capture brief."
        )
    brief_section = f"\n\nBRIEF / BIAS ANALYSIS (use this — it is the primary source):\n{effective_brief}"

    # Strip any internal labels (EP001, EP002 etc.) from topic so they never appear in slides
    clean_topic = re.sub(r'\bEP\d{3,4}\b', '', topic).strip(' —-').strip()

    # Build today's date string in PT-BR for cover_date — never let Haiku invent a date
    _today = datetime.datetime.utcnow()
    _months_pt = ["janeiro","fevereiro","março","abril","maio","junho",
                  "julho","agosto","setembro","outubro","novembro","dezembro"]
    _today_pt = f"{_today.day:02d} de {_months_pt[_today.month-1]} de {_today.year} · Brasil"

    prompt = f"""You are writing a FORMAT-019 "Dados ou Agenda?" Instagram carousel in Brazilian Portuguese.
This format checks whether a public figure or influencer is presenting data honestly or pushing an agenda.

Subject: "{clean_topic}"{brief_section}

MANDATORY RULES:
1. Use ONLY facts from the brief. Never invent numbers, claims, or sources.
2. Language: Brazilian Portuguese throughout body copy. Headings have an English subtitle (small, grey).
3. NEVER put internal identifiers like "EP001", "EP002", "FORMAT-019" in any slide text.
4. cover_claim is NON-NEGOTIABLE — the exact claim/opinion being fact-checked, in 1 punchy sentence. This is the scroll-stopper on slide 1. Write it as rage-bait or mystery: "O Brasil gasta 3x mais que o mundo com Justiça." If the brief has a direct quote, use it. If not, distill the core claim to one provocative line. Max 12 words. NEVER leave cover_claim empty.
5. cover_date MUST be exactly: "{_today_pt}" — do not invent or change this date.
6. Slide 2 is about the SPECIFIC POST/CLAIM they made — quote or paraphrase what they said. This expands what cover_claim stated on slide 1.
7. The VERDICT slide is the most important — show the 3-way score as concrete percentages.
8. Every factual slide needs a source name (Harvard, IMF, IBGE, etc.) visible in the text.
9. Tone: journalistic, calm, not accusatory. "Vamos ver o que os dados dizem."
10. READABILITY (SH-013): Nível de leitura 8ª série. Máximo 16 palavras por frase. Sem jargão sem explicação em 2 linhas. Voz ativa. Frases curtas.

Return ONLY a valid JSON object with this exact structure:

{{
  "cover_pt": "DADOS VS OPINIÃO — 4-6 words MAX, ALL CAPS (topic of this episode)",
  "cover_en": "Data vs Opinion — same topic in English",
  "cover_accent": "1 word from cover to highlight in accent color (e.g. 'OPINIÃO' or 'DADOS')",
  "cover_claim": "The exact claim being fact-checked — 1 short punchy sentence, as the person said it. This is the HOOK on slide 1. Write it like rage-bait: provocative, mysterious, stops the scroll. E.g.: 'O Brasil gasta 3x mais que o mundo com Justiça.' or 'O Judiciário brasileiro é o mais caro do planeta.' Max 12 words. PORTUGUESE ONLY.",
  "cover_date": "DD de mês de YYYY · Brasil",
  "cover_credibility_badge": "ALTA CREDIBILIDADE|MÉDIA CREDIBILIDADE|BAIXA CREDIBILIDADE — pick one from brief",
  "cover_visual": {{
    "subject_type": "person",
    "option_a": {{
      "type": "ai-composition",
      "prompt": "photorealistic portrait of [influencer name], Brazilian financial educator, professional look, dramatic side lighting, dark background — for Seedream 4.5",
      "concept": "Close portrait of the influencer, serious look, editorial style",
      "tool_hint": "seedream"
    }},
    "option_b": {{
      "type": "graphic-design",
      "concept": "Bold DADOS VS OPINIÃO text over dark background, influencer handle in smaller type, accent yellow line"
    }},
    "recommended": "a",
    "reason": "Influencer face stops scroll; viewer knows exactly who this is about"
  }},
  "slides": [
    {{
      "type": "quote",
      "heading_pt": "O que ele disse",
      "heading_en": "What they claimed",
      "quote": "Direct paraphrase or quote of the specific claim they made in the post — in PT-BR",
      "source": "@handle · [platform] · data",
      "context_pt": "Why this claim matters: how many followers, what they were promoting or explaining",
      "mentioned_people": [
        {{"name": "Influencer Full Name", "role_pt": "Educador financeiro — X milhões de seguidores", "role_en": "Financial educator", "image_hint": "influencer name Instagram"}}
      ],
      "visual_hint": "bio-card",
      "context_image_query": ""
    }},
    {{
      "type": "data",
      "heading_pt": "O que os dados dizem",
      "heading_en": "What the data says",
      "numbers": [
        {{"value": "XX%", "label_pt": "dado verificado 1 com contexto", "label_en": "verified fact 1"}},
        {{"value": "XX", "label_pt": "dado verificado 2 com contexto", "label_en": "verified fact 2"}}
      ],
      "mentioned_people": [],
      "visual_hint": "context-image",
      "context_image_query": "specific chart, graph, or institution related to this data — e.g. 'banco central brasil taxa juros dados' or 'IMF World Economic Outlook chart'"
    }},
    {{
      "type": "list",
      "heading_pt": "O que ele deixou de fora",
      "heading_en": "What was missing",
      "items_pt": [
        "Contexto omitido 1 — o que os dados reais mostram",
        "Contexto omitido 2 — informação que muda a conclusão",
        "Contexto omitido 3 — fonte que contradiz ou qualifica"
      ],
      "mentioned_people": [],
      "visual_hint": "context-image",
      "context_image_query": "financial data research institution or document — e.g. 'relatorio banco mundial economia emergente' or 'FGV IBRE dados pesquisa'"
    }},
    {{
      "type": "comparison",
      "heading_pt": "O que ele disse vs. a realidade",
      "heading_en": "Claimed vs. reality",
      "left_label": "Ele disse",
      "right_label": "Os dados mostram",
      "items": [
        {{"aspect": "Ponto 1 analisado", "left": "afirmação dele resumida", "right": "dado real com fonte"}},
        {{"aspect": "Ponto 2 analisado", "left": "afirmação dele resumida", "right": "dado real com fonte"}}
      ],
      "mentioned_people": [],
      "visual_hint": "context-image",
      "context_image_query": "specific economic data visual — graph, report cover, or institution facade"
    }},
    {{
      "type": "verdict",
      "heading_pt": "VEREDICTO — Dados ou Agenda?",
      "heading_en": "Data or Agenda?",
      "verdicts": [
        {{"label": "Baseado em Dados", "result": "XX%", "detail_pt": "O que está correto e embasado em fontes sólidas"}},
        {{"label": "Viés Ideológico", "result": "XX%", "detail_pt": "Onde a visão de mundo influencia a apresentação dos fatos"}},
        {{"label": "Viés de Interesse", "result": "XX%", "detail_pt": "Onde interesses comerciais, audiência ou marca pessoal distorcem o conteúdo"}}
      ],
      "mentioned_people": [],
      "visual_hint": "none",
      "context_image_query": ""
    }},
    {{
      "type": "list",
      "heading_pt": "Nossa conclusão",
      "heading_en": "Our take",
      "items_pt": [
        "O que é seguro usar do conteúdo dele",
        "O que deve ser verificado antes de aplicar",
        "Como checar você mesmo: [fonte específica]"
      ],
      "mentioned_people": [],
      "visual_hint": "context-image",
      "context_image_query": "person reading financial documents research data — thoughtful analytical"
    }}
  ],
  "clip_suggestions": [
    {{
      "person_or_topic": "influencer name + claim topic",
      "slide": 1,
      "duration_hint": "5-7 seconds",
      "reason": "Cover: influencer speaking — creates personal connection",
      "photo_query": "influencer full name",
      "photo_bg_position": "center top",
      "youtube_query": "influencer name educacao financeira video recente",
      "instagram_query": "influencer handle financas pessoais dicas",
      "pexels_query": "financial advisor presenting data chart screen",
      "pixabay_query": "business person finance presentation data",
      "archive_query": "financial education lecture economics",
      "wikimedia_query": "economics finance education",
      "motion_prompt": "slow push-in on financial educator speaking, documentary style, warm lighting, 5s",
      "motion_renderer": "kenburns",
      "visual_hint": "bio-card"
    }}
  ],
  "sources": ["Source 1 — institution + specific report/year", "Source 2", "Source 3", "Source 4"],
  "source_handle": "real Instagram username without @ symbol — e.g. 'thiagodespaiva' or 'primo_rico'. If unknown, use the person's full name in lowercase with underscores e.g. 'thiago_de_paiva'. NEVER write HANDLE_PLACEHOLDER or any placeholder text.",
  "cta_pt": "Salva e manda pra quem precisa ver.",
  "cta_en": "Save this.",
  "caption_pt": "Instagram caption PT — 3-4 sentences. Hook: mention the influencer and the tension. Body: what you found. End: follow for Dados ou Agenda? series. Hashtags: max 8, no party names, no @-tags.",
  "caption_en": "Instagram caption EN — same structure"
}}

IMPORTANT:
- The percentages in verdicts[].result must add up to 100%.
- If brief says "45% dados / 10% ideológico / 45% interesse" → use those exact numbers.
- Cover credibility badge must match brief's credibility field (ALTA/MÉDIA/BAIXA).
- quotes slide: the "quote" field must be the actual claim, not a meta description.
- items_pt: write in simple PT-BR, max 15 words per bullet. Factual only.
- comparison items: max 2 rows. Keep values short (under 10 words each side).
- The comparison "left" column is what the influencer claimed; "right" is what data shows — always paired.
- motion_renderer must always be "kenburns" for Brazil native template.
- Never use party hashtags or @-tags in caption_pt.
- source_handle MUST be the real Instagram username without @. If you don't know it, use the person's full name in lowercase with underscores. NEVER write HANDLE_PLACEHOLDER."""

    for attempt in range(2):
        if attempt == 1:
            research = _web_research(clean_topic, lang="pt")
            if research:
                print(f"  Dados: retrying with web research for: {clean_topic}")
                prompt = (
                    f"RESEARCH FOUND:\n{research}\n\n"
                    "Use this research to supplement the brief. Do not invent. Brief takes priority.\n\n"
                ) + prompt
            else:
                print("  Dados: no research — retrying with fresh call")

        try:
            text = _claude_with_fallback(
                prompt, max_tokens=4000, timeout=60,
                context=f"carousel_builder.dados(attempt {attempt+1})",
            )
        except Exception as e:
            print(f"  LLM cascade failed (Dados, attempt {attempt+1}): {e}")
            continue
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            print(f"  Dados content generation failed — no JSON in response (attempt {attempt+1})")
            continue
        try:
            result = json.loads(m.group())
            # Inject template key so HTML builder knows which path to use
            result["_template_key"] = "dados-ou-agenda"
            # Belt-and-suspenders: override cover_date with today (Haiku sometimes drifts to past years)
            result["cover_date"] = _today_pt
            # FIX 4: validate source_handle — retry if PLACEHOLDER crept in
            _sh = str(result.get("source_handle", ""))
            if "PLACEHOLDER" in _sh.upper() or "HANDLE" in _sh.upper():
                if attempt < 1:
                    print(f"  Dados: source_handle contains placeholder ('{_sh}') — retrying")
                    continue
                else:
                    raise ValueError(
                        f"source_handle still contains placeholder after 2 attempts: '{_sh}'. "
                        "Run /capture first to identify the influencer's real Instagram handle."
                    )
            return result
        except json.JSONDecodeError as e:
            print(f"  Dados JSON parse error (attempt {attempt+1}): {e}")
            continue
    print("  Dados content generation failed after 2 attempts")
    return None


def generate_verdade_content(topic, brief=""):
    """Generate FORMAT-024 Verdade Pela Metade debunk carousel (7 slides, PT-BR).
    brief should contain 'MODE: mode_a|mode_b' and optionally 'RESEARCH: {json}'.
    Source account is NEVER named in content — content is always original."""
    import re as _re

    # Parse mode and research from brief
    mode = "mode_b"
    research_text = ""
    if brief:
        m = _re.search(r"MODE:\s*(mode_[ab])", brief, _re.IGNORECASE)
        if m:
            mode = m.group(1).lower()
        r = _re.search(r"RESEARCH:\s*(.+)", brief, _re.DOTALL | _re.IGNORECASE)
        if r:
            research_text = r.group(1).strip()

    _today = datetime.datetime.utcnow()
    _months_pt = ["janeiro","fevereiro","março","abril","maio","junho",
                  "julho","agosto","setembro","outubro","novembro","dezembro"]
    today_pt = f"{_today.day:02d} de {_months_pt[_today.month-1]} de {_today.year}"

    mode_branch_instructions = ""
    if mode == "mode_a":
        mode_branch_instructions = """slide_mode_heading_pt: "Quem Realmente Decidiu" — who is actually responsible
slide_mode_heading_en: "Who Actually Decided"
slide_mode_content: object with keys: responsible_party (name), decision_name (law/vote name), year (string), source_url (URL)"""
        if research_text:
            mode_branch_instructions += f"\n\nResearch data (use this — do not invent):\n{research_text[:600]}"
    else:
        mode_branch_instructions = """slide_mode_heading_pt: "Número Real vs O Que Disseram" — actual stat vs inflated version
slide_mode_heading_en: "Real Number vs What They Said"
slide_mode_content: object with keys: original_stat (what went viral), real_stat (verified number + source), context (1-2 sentences explaining the difference)"""

    prompt = f"""Você é um jornalista brasileiro escrevendo um carrossel Instagram de 7 slides FORMAT-024 "Verdade Pela Metade".
Este formato desmonta boatos virais em PT-BR com fontes verificadas. Nunca mencione a origem do conteúdo — seja sempre original.

Tópico: "{topic}"
Data: {today_pt}
Modo: {mode} — {"Atribuição errada (fato real, pessoa/governo errado)" if mode == "mode_a" else "Números distorcidos (dado real, contexto manipulado)"}

{mode_branch_instructions}

REGRAS OBRIGATÓRIAS:
1. cover_claim é o gancho — a afirmação viral em 1 frase curta, provocadora. Max 12 palavras.
2. slide_o_que_diz é o que o boato diz literalmente — cite sem comentar.
3. slide_mode_* é onde você derruba o boato com dados reais.
4. contexto explica o que o boato deixa de fora.
5. conclusao é o veredicto em 1 linha: "O fato é real, mas a responsabilidade é de X."
6. Fontes: mínimo 2 outlets verificáveis (G1, Agência Brasil, Câmara.gov, Senado.leg, etc.).
7. Tom: jornalístico, calmo, não acusatório. "Os dados mostram que..."
8. NUNCA invente números. Se não souber, escreva "dado não disponível".
9. LEGIBILIDADE (SH-013): Nível 8ª série. Máximo 16 palavras por frase. Voz ativa. Frases curtas. Sem jargão sem explicação.

Retorne SOMENTE um JSON válido com esta estrutura exata:
{{
  "cover_pt": "VERDADE PELA METADE — 4-6 palavras (tópico do episódio, ALL CAPS)",
  "cover_en": "Half-Truth — same topic in English",
  "cover_accent": "1 palavra do cover_pt para destacar em amarelo",
  "cover_claim": "A afirmação viral — 1 frase curta e provocadora (max 12 palavras, PT)",
  "cover_date": "{today_pt}",
  "person": {{
    "name": "Nome completo da pessoa que está sendo mal atribuída OU o político/instituição citado no boato",
    "role": "cargo ou descrição em PT",
    "image_hint": "nome para busca de foto jornalística"
  }},
  "slide_o_que_diz": "O que o boato diz literalmente — 2-3 frases, como se descrevesse o viral",
  "slide_mode_heading_pt": "título do slide 3 (conforme modo)",
  "slide_mode_heading_en": "English subtitle for slide 3",
  "slide_mode_content": {{ "depends_on_mode": "see above" }},
  "contexto": "O que o boato deixa de fora — 2-3 frases. Este é o contexto crucial que muda tudo.",
  "fontes": [
    "Outlet 1 — descrição curta do que confirma",
    "Outlet 2 — descrição curta"
  ],
  "conclusao": "Veredicto em 1 linha. Ex: 'O dado é real — mas a culpa é do governo anterior, não do atual.'",
  "sources": [
    "Fonte 1 — url ou nome completo",
    "Fonte 2 — url ou nome completo"
  ],
  "caption": "Legenda Instagram: 2-3 frases. Primeira linha = gancho (visível no feed). Descreve o tópico. Termina com 8-10 hashtags relevantes em PT.",
  "clip_suggestions": [
    {{
      "slide": 1,
      "person_or_topic": "nome da pessoa ou tema para busca de clipe",
      "youtube_query": "nome da pessoa + discurso ou pronunciamento",
      "pexels_query": "parlamento brasileiro ou congresso nacional",
      "visual_hint": "bio-card"
    }}
  ]
}}"""

    for attempt in range(2):
        try:
            response = anthropic_client().messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            result = json.loads(raw)
            result["_template_key"] = "verdade-pela-metade"
            result["_mode"] = mode
            return result
        except json.JSONDecodeError as e:
            print(f"  Verdade JSON parse error (attempt {attempt+1}): {e}")
            continue
    print("  Verdade content generation failed after 2 attempts")
    return None


def generate_brazil_content(topic, brief="", model="claude-sonnet-4-6"):
    """Generate structured Brazil news carousel content via Claude Haiku."""
    brief_section = f"\n\nBRIEF / RESEARCH PROVIDED (use this — do not invent facts):\n{brief}" if brief else ""
    prompt = f"""You are writing a Brazil news carousel for Instagram.
Topic: "{topic}"{brief_section}

{BRAZIL_COPY_RULES}

Return ONLY a valid JSON object with this exact structure:
{{
  "cover_pt": "HEADLINE IN CAPS — 5-8 words, punchy",
  "cover_en": "SAME HEADLINE IN ENGLISH",
  "cover_accent": "1 NUMBER OR WORD to highlight in yellow (e.g. '16', 'ACABOU')",
  "cover_date": "DD de mês de YYYY · Country",
  "cover_visual": {{
    "subject_type": "person|place|event|concept — pick one",
    "option_a": {{
      "type": "cc-photo",
      "search_query": "specific search term for Agência Brasil or Wikimedia Commons (e.g. 'Viktor Orbán 2026 election Hungary' or 'Havana Cuba 1950s street')",
      "description": "what this photo shows"
    }},
    "option_b": {{
      "type": "ai-composition",
      "prompt": "detailed AI image prompt — for place: '[PLACE NAME] in massive bold serif letters centered, historical leader portraits fading into/behind the letterforms, high contrast documentary black-and-white + sepia overlay, powerful editorial style'. For person: 'photorealistic portrait of [Name], dramatic side lighting, dark background'. For event: 'archival document texture, [EVENT] stamped in bold capital letters, official seal visible'",
      "concept": "brief visual concept — e.g. 'CUBA in huge letters, Fidel + Che fading into the C and U, sepia tone'",
      "tool_hint": "openai|seedream|nb2"
    }},
    "option_c": {{
      "type": "graphic-design",
      "concept": "typographic composition — e.g. 'bold topic name in Anton font, accent-yellow underline, minimal documentary photo cropped behind text at low opacity'"
    }},
    "recommended": "a|b|c",
    "reason": "one line why this option fits this specific topic"
  }},
  "slides": [
    {{
      "type": "profile",
      "heading_pt": "Quem é [Name]?",
      "heading_en": "Who is [Name]?",
      "party_tag": "PARTY NAME — leaning label",
      "facts_pt": ["fact 1", "fact 2", "fact 3"],
      "sticker_name": "LASTNAME",
      "mentioned_people": [],
      "visual_hint": "bio-card",
      "context_image_query": ""
    }},
    {{
      "type": "data",
      "heading_pt": "O Resultado",
      "heading_en": "The Results",
      "numbers": [
        {{"value": "XX%", "label_pt": "label", "label_en": "label"}},
        {{"value": "XX%", "label_pt": "label", "label_en": "label"}},
        {{"value": "XX%", "label_pt": "label", "label_en": "label"}},
        {{"value": "XM", "label_pt": "label", "label_en": "label"}}
      ],
      "mentioned_people": [],
      "visual_hint": "context-image",
      "context_image_query": "specific institution/place/event — e.g. 'Câmara dos Deputados Brasília fachada'"
    }},
    {{
      "type": "list",
      "heading_pt": "Heading PT",
      "heading_en": "Heading EN",
      "items_pt": ["item 1", "item 2", "item 3", "item 4"],
      "mentioned_people": [],
      "visual_hint": "context-image|bio-card|none",
      "context_image_query": "search term if context-image, else empty string"
    }},
    {{
      "type": "list",
      "heading_pt": "Heading PT",
      "heading_en": "Heading EN",
      "items_pt": ["item 1", "item 2", "item 3"],
      "mentioned_people": [],
      "visual_hint": "context-image|bio-card|none",
      "context_image_query": "search term if context-image, else empty string"
    }},
    {{
      "type": "quote",
      "heading_pt": "Mas [question]?",
      "heading_en": "But [question]?",
      "quote": "Memorable quote from a credible source",
      "source": "Source Name",
      "context_pt": "1-2 lines of context",
      "mentioned_people": [],
      "visual_hint": "bio-card|context-image|none",
      "context_image_query": "search term if context-image (e.g. speaker's institution building), else empty string"
    }},
    {{
      "type": "comparison",
      "heading_pt": "Na prática — lado a lado",
      "heading_en": "Side by side",
      "left_label": "Brasil",
      "right_label": "Outros países",
      "items": [
        {{"aspect": "Aspecto analisado", "left": "dado Brasil PT", "right": "dado outros PT"}}
      ],
      "mentioned_people": [],
      "visual_hint": "context-image|none",
      "context_image_query": "specific institution, document, or building related to the comparison"
    }},
    {{
      "type": "verdict",
      "heading_pt": "O que é real?",
      "heading_en": "What is real?",
      "verdicts": [
        {{"label": "A afirmação", "result": "ENGANOSO", "detail_pt": "Por que essa afirmação engana em 1-2 frases simples"}},
        {{"label": "O número real", "result": "PARCIALMENTE CORRETO", "detail_pt": "O que está correto e o que falta de contexto"}}
      ],
      "mentioned_people": [],
      "visual_hint": "none",
      "context_image_query": ""
    }}
  ],
  "clip_suggestions": [
    {{"person_or_topic": "name or topic", "slide": 3, "duration_hint": "5-8 seconds", "reason": "why this clip fits this slide",
      "photo_query": "Wikipedia/Wikimedia search term for a CC-licensed still photo of this person or place — used as slide background in v1 motion treatment. For people: 'Firstname Lastname' in English. For places: English or PT landmark name.",
      "photo_bg_position": "CSS background-position for the photo crop (e.g. 'center 20%' for face, '50% 40%' for building, 'center top' for portrait). Default: 'center 20%'.",
      "youtube_query": "specific YouTube search — proper names OK, best for speeches/press",
      "instagram_query": "IG-style phrasing — lowercase hashtag-friendly, creator reels",
      "pexels_query": "stock-style phrasing — place/event/institution, NO proper names",
      "pixabay_query": "alt stock phrasing — different wording than pexels_query",
      "archive_query": "archival phrasing for public-domain footage (Archive.org)",
      "wikimedia_query": "CC-licensed historical/institutional footage query",
      "motion_prompt": "5s visual direction for the animated cover (Remotion/Kling): camera, mood, framing",
      "motion_renderer": "kenburns",
      "visual_hint": "bio-card|context-image|place|event|product-photo|ugc-reaction|none"
    }}
  ],
  "sources": ["Source 1", "Source 2", "Source 3", "Source 4"],
  "cta_pt": "Salva pra não esquecer.",
  "cta_en": "Save this.",
  "caption_pt": "Instagram caption PT — 3-4 sentences + hashtags",
  "caption_en": "Instagram caption EN — 3-4 sentences + hashtags"
}}

Rules:
- Factual only. No opinion. No accusation.
- Party affiliation in every politician mention
- Simple Portuguese — not academic
- Numbers must match the brief exactly if provided
- Body text and bullet items (items_pt, facts_pt, context_pt, items[*].left/right, verdicts[*].detail_pt) must be in PORTUGUESE ONLY. Never mix English words into PT sentences. heading_en is a small subtitle only — it is NOT body copy.
- Generate 5-9 slides based on the brief's complexity. More data and named people = more slides.
- Use `comparison` type when the brief contains side-by-side data (Brazil vs others, before vs after a law, two methodologies). One comparison row per key dimension. Max 4 rows per slide.
- Use `verdict` type at the end of a fact-check carousel to rate each specific claim (VERDADEIRO / ENGANOSO / PARCIALMENTE CORRETO / FALSO). One verdict per bullet point claim. Only use when the carousel is explicitly debunking claims.

COVER VISUAL RULES (apply before filling cover_visual):
subject_type guide:
  "person" → named individual is the main subject. option_a=CC real photo (Wikimedia/Agência Brasil). option_b=AI portrait (Seedream 4.5). recommended=a unless no CC photo exists → then b.
  "place" → country/city is the character (not a specific person). option_a=archival/news CC photo. option_b=AI composition with place name in massive letters + historical leaders fading into letterforms. recommended=b (more graphic, stops scroll on IG).
  "event" → specific law/decision/moment. option_a=document screenshot or news headline crop. option_b=archival texture + event name in bold stamp. recommended=a (receipt journalism visual).
  "concept" → abstract policy/ideology/system. option_a=contextual CC photo. option_b=bold typographic AI composition. recommended=b.

VISUAL-EVERY-OTHER-SLIDE RULE (non-negotiable):
Between cover and sources, never output "visual_hint": "none" on more than 1 consecutive slide.
Also target at least 3 middle slides with visual_hint="context-image" plus specific queries.
visual_hint values:
  "bio-card" → slide has named person in mentioned_people (face cards render automatically from that field)
  "context-image" → slide references a specific institution, building, place, event, or document; fill context_image_query with a specific search term (e.g. "Câmara dos Deputados Brasília", "Viktor Orbán 2026", "Supremo Tribunal Federal fachada", "Congresso Nacional aerial")
  "ugc-reaction" → UGC/reaction-style motion only: meme reaction, expressive emoji/sticker, quick emotional beat. Use only when the slide needs a reaction GIF/sticker instead of factual b-roll.
  "none" → text-only, max 1 consecutive allowed
First choice for Brazilian institutions: Agência Brasil CC BY 3.0 search terms. International subjects: English search terms.
BANNED context_image_query patterns: NEVER copy words from heading_pt or heading_en into the query. The query is a stock photo search term — it must be a place/institution/person name, NOT a phrase from the slide copy. BAD: "Se comparar igual com igual" or "O ponto que importa". GOOD: "Conselho Nacional de Justiça STF fachada" or "STF Brasília Supremo Tribunal Federal". For comparison slides about judicial spending: always reference a specific court or government body (STF, CNJ, Câmara, TCU).

CLIP SUGGESTIONS + MOTION PROMPTS RULE (non-negotiable):
The motion pipeline runs the Motion System v2 source cascade per clip: real short clips (clip collections / YouTube / Instagram / Archive.org / Wikimedia) → GIPHY → static PNG/no motion. You must write DIFFERENT phrasing per tier so each tier can succeed even if the others fail.

QUERY QUALITY RULE: Every query must be specific enough that a researcher could find the RIGHT clip — not just any clip. A good youtube_query for a slide about "Flávio Bolsonaro CPI 2021" is "Flávio Bolsonaro CPI senado 2021 depoimento" not "Bolsonaro corruption". For a Congress scene: "Câmara dos Deputados votação sessão 2023" not just "congress". Include: full name (if person) + year + context keyword (hearing/speech/vote/signing). Generic queries produce unrelated clips that don't match the story.

For every slide that would benefit from motion (cover + any slide naming a speech, law, institution, event, leader, or iconic moment):
  - youtube_query   → SPECIFIC: full name + year + event type. Best for speeches, press conferences, hearings. e.g. "Viktor Orbán concede derrota eleição Hungria 2026"
  - instagram_query → lowercase, hashtag-friendly, creator-reel phrasing. e.g. "hungria eleicao 2026 orbán perdeu"
  - pexels_query    → legacy/static-photo hint only: place/institution/event, NO proper names, NO party names. e.g. "parliament building Budapest exterior"
  - pixabay_query   → legacy/static-photo hint only, different wording than pexels_query. e.g. "European parliament vote session"
  - archive_query   → public-domain / archival phrasing (vintage footage, historical film). e.g. "Hungary Budapest 1990 democratic transition archival"
  - wikimedia_query → CC-licensed historical or institutional footage. e.g. "Hungarian National Assembly Budapest"
  - motion_prompt   → 5-second directorial note: camera move + mood + framing (e.g. "slow push-in on Brasília facade, dusk, cinematic, 24mm", "archival grain, slight zoom on signing ceremony"). This drives Remotion animation + serves as AI-video prompt if we escalate to Runway/Kling.
  - photo_query     → Wikipedia/Wikimedia search term for a CC still photo used as slide background. For people: English full name. For places: landmark name. This is the PRIMARY source — always populate.
  - photo_bg_position → CSS background-position for the crop (default "center 20%"). Use "center top" for portraits, "50% 40%" for buildings.
  - motion_renderer → "playwright" for Motion v2 Phase 1. No Ken Burns, no zoompan, no text movement.
  - visual_hint     → same values as slides.visual_hint, plus "ugc-reaction" for UGC/reaction GIF-style motion. Determines whether stock tiers are allowed (stock skips for bio-card).

If NO tier could plausibly succeed (hyper-local story, no public footage, no place to film) return an empty clip_suggestions array — do not invent false queries. The pipeline must deliver static PNG/no motion instead of fake motion.

NAMED-PERSON → FACE RULE (non-negotiable):
For every slide, populate `mentioned_people` with EVERY named person referenced in that
slide's text whose face should appear as a 3x4 bio-card next to the text. Include the
main subject ONLY on the slide where they get the hero sticker (cover/profile). Do NOT
repeat them on later slides unless they reappear with a new quote/fact. For each entry:
  {{"name": "First Last", "role_pt": "cargo / por quê é citado", "role_en": "role", "image_hint": "Wikipedia search term or 'Agência Brasil' descriptor"}}
If no secondary person is named on a slide, return an empty array. Never omit the field.
These map to .bio-card / .bio-photo / .bio-initials in the HTML template — one card per
entry, 2-column grid, face crop first, name second, role tag third."""

    for attempt in range(2):
        if attempt == 1:
            research = _web_research(topic, lang="pt")
            if research:
                print(f"  Brazil: retrying with web research for: {topic}")
                prompt = (
                    f"RESEARCH FOUND:\n{research}\n\n"
                    "Use this research to fill in missing facts, names, dates, and numbers. "
                    "Do not invent. Do not contradict your knowledge.\n\n"
                ) + prompt
            else:
                print("  Brazil: no research found — retrying with fresh call")

        try:
            text = _claude_with_fallback(
                prompt, max_tokens=4000, timeout=60,
                context=f"carousel_builder.brazil(attempt {attempt+1})", model=model,
            )
        except Exception as e:
            print(f"  LLM cascade failed (Brazil, attempt {attempt+1}): {e}")
            continue
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            print(f"  Brazil content generation failed — no JSON in response (attempt {attempt+1})")
            continue
        try:
            return json.loads(m.group())
        except json.JSONDecodeError as e:
            print(f"  Brazil content JSON parse error (attempt {attempt+1}): {e}")
            continue
    print("  Brazil content generation failed after 2 attempts")
    return None


def _fetch_person_photo(search_query, dest_dir, filename):
    """Try to download a CC-licensed photo for a named person.
    Route A: Drive cache (already downloaded this run) — instant.
    Route B: Wikipedia REST API thumbnail — fastest for politicians/public figures.
    Route C: Wikimedia Commons search — broader CC library.
    Returns relative path 'resources/images/<filename>' if downloaded, else empty string.
    Caller must use .bio-initials fallback when this returns empty — never a raw placeholder.
    """
    dest_path = Path(dest_dir) / "resources" / "images" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # Route A: cache hit
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return f"resources/images/{filename}"
    # Route B: Wikipedia REST API thumbnail
    try:
        wiki_name = urllib.parse.quote(search_query.replace(" ", "_"))
        wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_name}"
        wiki_req = urllib.request.Request(wiki_url, headers={"User-Agent": "oak-park-carousel/1.0 (github.com/priihigashi)"})
        wiki_data = json.loads(urllib.request.urlopen(wiki_req, timeout=10).read())
        thumb = wiki_data.get("thumbnail", {}).get("source", "")
        if thumb:
            with urllib.request.urlopen(thumb, timeout=15) as r:
                raw = r.read()
            if len(raw) > 2000:
                dest_path.write_bytes(raw)
                print(f"  Photo fetched (Wikipedia): {filename} ({len(raw)//1024}KB)")
                return f"resources/images/{filename}"
    except Exception as _e:
        print(f"  Wikipedia photo miss ({search_query}): {_e}")
    # Route C: Wikimedia Commons search
    try:
        q = urllib.parse.quote_plus(search_query)
        # Search Wikimedia Commons file namespace (ns=6)
        search_url = (
            f"https://commons.wikimedia.org/w/api.php?action=query&list=search"
            f"&srsearch={q}&srnamespace=6&srlimit=8&format=json&srprop="
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "oak-park-carousel/1.0 (github.com/priihigashi)"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        results = data.get("query", {}).get("search", [])
        for hit in results[:8]:
            title = hit.get("title", "")
            if not any(title.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                continue
            enc = urllib.parse.quote(title.replace(" ", "_"))
            info_url = (
                f"https://commons.wikimedia.org/w/api.php?action=query"
                f"&titles={enc}&prop=imageinfo&iiprop=url&iiurlwidth=600&format=json"
            )
            info_req = urllib.request.Request(info_url, headers={"User-Agent": "oak-park-carousel/1.0"})
            info = json.loads(urllib.request.urlopen(info_req, timeout=10).read())
            for page in info.get("query", {}).get("pages", {}).values():
                ii = (page.get("imageinfo") or [{}])[0]
                img_url = ii.get("thumburl") or ii.get("url", "")
                if not img_url:
                    continue
                with urllib.request.urlopen(img_url, timeout=15) as r:
                    raw = r.read()
                if len(raw) < 2000:
                    continue  # skip tiny/corrupt files
                dest_path.write_bytes(raw)
                print(f"  Photo fetched: {filename} ({len(raw)//1024}KB) ← {title[:60]}")
                return f"resources/images/{filename}"
        print(f"  No CC photo found for: {search_query}")
    except Exception as e:
        print(f"  Photo fetch failed ({search_query}): {e}")
    return ""


def _fetch_slide_photos_brazil(content, work_dir):
    """Fetch per-slide photos for Brazil native motion template.
    For each odd-indexed middle slide (3, 5, 7 …) that has a photo_query in
    clip_suggestions, tries Wikipedia → Wikimedia Commons (person) or
    Pexels (context) to get a CC-licensed photo.
    Returns dict {slide_i: "resources/images/<file>"} — empty string = not found.
    """
    result = {}
    clip_suggestions = content.get("clip_suggestions", [])
    # Build a lookup by slide index
    sugg_by_slide = {s.get("slide", 0): s for s in clip_suggestions if s.get("slide")}

    for slide_i, slide in enumerate(content.get("slides", []), start=2):
        if slide_i % 2 == 0:
            continue  # even slides are static — no bg photo needed
        sugg = sugg_by_slide.get(slide_i, {})
        photo_query = sugg.get("photo_query", "") or sugg.get("youtube_query", "")
        if not photo_query:
            # Prefer context_image_query from the slide JSON (dados-ou-agenda + other templates emit this)
            photo_query = slide.get("context_image_query", "")
        if not photo_query:
            # Last resort: slide heading (vague but better than nothing)
            photo_query = slide.get("heading_pt", "") or slide.get("heading_en", "")
        if not photo_query:
            continue

        safe = re.sub(r"[^\w]", "_", photo_query.lower())[:30]
        fname = f"slide{slide_i}_{safe}.jpg"
        bg_pos = sugg.get("photo_bg_position", "center 20%")

        # Route A: person photo (Wikipedia → Wikimedia)
        visual_hint = sugg.get("visual_hint", "")
        if visual_hint == "bio-card" or slide.get("type") == "profile":
            path = _fetch_person_photo(photo_query, work_dir, fname)
        else:
            path = _fetch_person_photo(photo_query, work_dir, fname)
            if not path:
                path = _fetch_pexels_image(photo_query, work_dir, fname)
            if not path:
                path = _fetch_pixabay_image(photo_query, work_dir, fname)

        if path:
            result[slide_i] = path
            # Embed the bg_position hint as a sidecar so the HTML can read it
            pos_file = Path(work_dir) / "resources" / "images" / f"{fname}.bgpos"
            pos_file.write_text(bg_pos)
        else:
            print(f"  slide_photos_brazil: no photo for slide {slide_i} ({photo_query[:40]})")

    return result


def _generate_ai_cover(prompt, work_dir, filename="cover.jpg"):
    """Generate image via DALL-E 3. Returns relative path or empty string.
    Falls back silently on any error — caller uses placeholder if empty."""
    if not _USE_DALLE or not OPENAI_KEY or not prompt:
        return ""
    dest_path = Path(work_dir) / "resources" / "images" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 5000:
        return f"resources/images/{filename}"
    try:
        payload = json.dumps({
            "model": "dall-e-3",
            "prompt": prompt[:1000],
            "n": 1,
            "size": "1024x1024",
            "quality": "standard",
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=payload,
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        img_url = resp["data"][0]["url"]
        with urllib.request.urlopen(img_url, timeout=30) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest_path.write_bytes(raw)
        print(f"  AI image generated: {filename} ({len(raw)//1024}KB via DALL-E 3)")
        return f"resources/images/{filename}"
    except Exception as e:
        print(f"  AI cover generation failed (non-fatal): {e}")
        return ""


def _generate_gemini_image(prompt, work_dir, filename):
    """Generate image via Gemini Imagen. Fallback when DALL-E fails/rate-limits.
    Returns relative path or empty string."""
    if not GEMINI_KEY or not prompt:
        return ""
    dest_path = Path(work_dir) / "resources" / "images" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 5000:
        return f"resources/images/{filename}"
    try:
        payload = json.dumps({
            "instances": [{"prompt": prompt[:1000]}],
            "parameters": {"sampleCount": 1, "aspectRatio": "1:1", "outputMimeType": "image/jpeg"},
        }).encode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict?key={GEMINI_KEY}"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        b64 = resp["predictions"][0].get("bytesBase64Encoded", "")
        if not b64:
            return ""
        import base64
        raw = base64.b64decode(b64)
        if len(raw) < 5000:
            return ""
        dest_path.write_bytes(raw)
        print(f"  AI image generated: {filename} ({len(raw)//1024}KB via Gemini Imagen)")
        return f"resources/images/{filename}"
    except Exception as e:
        print(f"  Gemini image generation failed (non-fatal): {e}")
        return ""


def _remove_background(img_rel_path, work_dir):
    """Run Replicate cjwbw/rembg on a local image. Returns path to PNG with transparent bg,
    or original path if rembg fails. Saves to resources/images/ with _nobg suffix."""
    if not REPLICATE_KEY or not img_rel_path:
        return img_rel_path
    src = Path(work_dir) / img_rel_path
    if not src.exists():
        return img_rel_path
    out_name = src.stem + "_nobg.png"
    out_path = src.parent / out_name
    out_rel = f"resources/images/{out_name}"
    if out_path.exists() and out_path.stat().st_size > 2000:
        _crop_to_bounding_box(out_path)   # SH-036: remove transparent waste first
        _ensure_transparent_headroom(out_path)
        return out_rel
    try:
        # Step 1: upload file to Replicate Files API → get a URL the model can access
        import email.mime.multipart, email.mime.base, email.encoders
        mime = "image/jpeg" if src.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        boundary = "rembgboundary"
        img_bytes = src.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="content"; filename="{src.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + img_bytes + f"\r\n--{boundary}--\r\n".encode()
        upload_req = urllib.request.Request(
            "https://api.replicate.com/v1/files",
            data=body,
            headers={
                "Authorization": f"Token {REPLICATE_KEY}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        upload_resp = json.loads(urllib.request.urlopen(upload_req, timeout=30).read())
        image_url = upload_resp.get("urls", {}).get("get", "")
        if not image_url:
            print(f"  rembg: file upload returned no URL — using original")
            return img_rel_path

        # Step 2: run rembg prediction with the uploaded file URL
        pred_body = json.dumps({
            "version": "fb8af171cfa1616ddcf1242c093f9c46bcada9ad046cf69ea84be475ec44de75",
            "input": {"image": image_url}
        }).encode()
        req = urllib.request.Request(
            "https://api.replicate.com/v1/predictions",
            data=pred_body,
            headers={"Authorization": f"Token {REPLICATE_KEY}", "Content-Type": "application/json"},
        )
        pred = json.loads(urllib.request.urlopen(req, timeout=30).read())
        poll_url = pred.get("urls", {}).get("get", "")
        for _ in range(30):
            time.sleep(3)
            result = json.loads(urllib.request.urlopen(
                urllib.request.Request(poll_url, headers={"Authorization": f"Token {REPLICATE_KEY}"})
            ).read())
            if result.get("status") == "succeeded":
                out_url = result.get("output")
                if out_url:
                    png_bytes = urllib.request.urlopen(out_url, timeout=20).read()
                    out_path.write_bytes(png_bytes)
                    _crop_to_bounding_box(out_path)   # SH-036
                    _ensure_transparent_headroom(out_path)
                    print(f"  rembg ✅ → {out_name} ({len(png_bytes)//1024}KB)")
                    return out_rel
                break
            if result.get("status") in ("failed", "canceled"):
                break
        print(f"  rembg: prediction did not succeed, using original")
    except Exception as e:
        print(f"  rembg failed (non-fatal): {e}")
    return img_rel_path


def _fetch_pexels_image(query, work_dir, filename):
    """Search Pexels for a royalty-free stock photo. Last-resort fallback for context images.
    Returns relative path or empty string."""
    if not PEXELS_KEY or not query:
        return ""
    dest_path = Path(work_dir) / "resources" / "images" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 2000:
        return f"resources/images/{filename}"
    try:
        q = urllib.parse.quote_plus(query[:100])
        search_url = f"https://api.pexels.com/v1/search?query={q}&per_page=3&orientation=portrait"
        req = urllib.request.Request(search_url, headers={
            "Authorization": PEXELS_KEY,
            "User-Agent": "carousel-builder/1.0",
        })
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        photos = data.get("photos", [])
        if not photos:
            return ""
        img_url = photos[0]["src"]["large"]
        with urllib.request.urlopen(img_url, timeout=20) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest_path.write_bytes(raw)
        _fetch_url_cache[str(dest_path)] = img_url  # SH-056: register for AI-art domain check
        print(f"  Pexels image fetched: {filename} ({len(raw)//1024}KB) ← '{query[:40]}'")
        return f"resources/images/{filename}"
    except Exception as e:
        _log_failure(f"pexels/{query[:40]}", e)
        return ""


def _fetch_pixabay_image(query, work_dir, filename):
    """Search Pixabay for a royalty-free stock photo. Free-tier backup for Pexels.
    Returns relative path or empty string."""
    if not PIXABAY_KEY or not query:
        return ""
    dest_path = Path(work_dir) / "resources" / "images" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 2000:
        return f"resources/images/{filename}"
    try:
        q = urllib.parse.quote_plus(query[:100])
        search_url = (
            f"https://pixabay.com/api/?key={PIXABAY_KEY}&q={q}"
            f"&image_type=photo&orientation=vertical&per_page=3&safesearch=true"
        )
        search_req = urllib.request.Request(search_url, headers={"User-Agent": "carousel-builder/1.0"})
        with urllib.request.urlopen(search_req, timeout=15) as r:
            data = json.loads(r.read())
        hits = data.get("hits", [])
        if not hits:
            return ""
        img_url = hits[0].get("largeImageURL") or hits[0].get("webformatURL", "")
        if not img_url:
            return ""
        dl_req = urllib.request.Request(img_url, headers={"User-Agent": "carousel-builder/1.0"})
        with urllib.request.urlopen(dl_req, timeout=20) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest_path.write_bytes(raw)
        _fetch_url_cache[str(dest_path)] = img_url  # SH-056: register for AI-art domain check
        print(f"  Pixabay image fetched: {filename} ({len(raw)//1024}KB) ← '{query[:40]}'")
        return f"resources/images/{filename}"
    except Exception as e:
        _log_failure(f"pixabay/{query[:40]}", e)
        return ""


def _replicate_run(version_or_slug, input_dict, work_dir, filename, timeout=120, model_label=""):
    """Shared Replicate helper: create prediction, poll until done, download output[0].
    Returns relative path or empty string."""
    if not REPLICATE_KEY or not input_dict:
        return ""
    dest_path = Path(work_dir) / "resources" / "images" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 5000:
        return f"resources/images/{filename}"
    try:
        # Two Replicate call shapes:
        # - Official models: POST /v1/models/<owner>/<name>/predictions (no "version" needed)
        # - Community models: POST /v1/predictions with {"version": "<sha>", ...}
        if "/" in version_or_slug and len(version_or_slug) < 80:
            api_url = f"https://api.replicate.com/v1/models/{version_or_slug}/predictions"
            payload = json.dumps({"input": input_dict}).encode()
        else:
            api_url = "https://api.replicate.com/v1/predictions"
            payload = json.dumps({"version": version_or_slug, "input": input_dict}).encode()
        req = urllib.request.Request(
            api_url, data=payload,
            headers={"Authorization": f"Bearer {REPLICATE_KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "wait=60"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        status = resp.get("status", "")
        pid = resp.get("id", "")
        # poll if not already done (Prefer: wait=60 covers most cases)
        started = time.time()
        while status in ("starting", "processing") and (time.time() - started) < timeout:
            time.sleep(2)
            poll_req = urllib.request.Request(
                f"https://api.replicate.com/v1/predictions/{pid}",
                headers={"Authorization": f"Bearer {REPLICATE_KEY}"},
            )
            resp = json.loads(urllib.request.urlopen(poll_req, timeout=15).read())
            status = resp.get("status", "")
        if status != "succeeded":
            print(f"  Replicate {model_label} status={status} (non-fatal)")
            return ""
        output = resp.get("output")
        if isinstance(output, list):
            output = output[0] if output else ""
        if not output:
            return ""
        with urllib.request.urlopen(output, timeout=30) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest_path.write_bytes(raw)
        print(f"  AI image generated: {filename} ({len(raw)//1024}KB via Replicate {model_label})")
        return f"resources/images/{filename}"
    except Exception as e:
        print(f"  Replicate {model_label} failed (non-fatal): {e}")
        return ""


def _remove_background_with_inference_sh(local_abs_path, out_abs_path):
    """Try bg removal via inference.sh image editing route.
    Returns True on success."""
    key = os.environ.get("PRI_OP_INFSH_API_KEY", "")
    if not key:
        return False
    try:
        raw = Path(local_abs_path).read_bytes()
        payload = json.dumps({
            "app": "google/gemini-3-1-flash-image-preview",
            "input": {
                "prompt": (
                    "Remove only the background from this image and keep the main subject untouched. "
                    "Return a transparent PNG with clean edges, no extra objects, no style changes."
                ),
                "image_base64": base64.b64encode(raw).decode(),
            },
        }).encode()
        req = urllib.request.Request(
            "https://api.inference.sh/apps/run",
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "carousel-builder/1.0",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        task_id = (resp.get("data") or resp).get("id", "")
        if not task_id:
            return False
        image_url = ""
        for _ in range(15):
            time.sleep(6)
            poll = urllib.request.Request(
                f"https://api.inference.sh/tasks/{task_id}",
                headers={"Authorization": f"Bearer {key}", "User-Agent": "carousel-builder/1.0"},
            )
            pdata = json.loads(urllib.request.urlopen(poll, timeout=30).read())
            pdata = pdata.get("data", pdata)
            status = pdata.get("status")
            output = pdata.get("output")
            if status == 10:
                if isinstance(output, dict):
                    imgs = output.get("images", output.get("image", []))
                    if isinstance(imgs, list) and imgs:
                        image_url = imgs[0].get("url", imgs[0]) if isinstance(imgs[0], dict) else imgs[0]
                    elif isinstance(imgs, str):
                        image_url = imgs
                elif isinstance(output, list) and output:
                    image_url = output[0].get("url", output[0]) if isinstance(output[0], dict) else output[0]
                elif isinstance(output, str):
                    image_url = output
                break
            if status in (11, 12):
                break
        if not image_url:
            return False
        raw_out = urllib.request.urlopen(str(image_url), timeout=40).read()
        if len(raw_out) < 5000:
            return False
        Path(out_abs_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_abs_path).write_bytes(raw_out)
        return True
    except Exception:
        return False


def _remove_background_with_replicate(local_abs_path, out_abs_path):
    """Try bg removal via Replicate model(s). Accepts data URI image input."""
    key = os.environ.get("PRI_OP_REPLICATE_API_KEY", "")
    if not key:
        return False
    try:
        b64 = base64.b64encode(Path(local_abs_path).read_bytes()).decode()
        data_uri = f"data:image/jpeg;base64,{b64}"
    except Exception:
        return False

    model_candidates = [
        ("fofr/remove-bg", {"image": data_uri}),
        ("cjwbw/rembg", {"image": data_uri}),
    ]
    for model_slug, input_dict in model_candidates:
        try:
            req = urllib.request.Request(
                f"https://api.replicate.com/v1/models/{model_slug}/predictions",
                data=json.dumps({"input": input_dict}).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "wait=60",
                },
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            status = resp.get("status", "")
            pid = resp.get("id", "")
            started = time.time()
            while status in ("starting", "processing") and (time.time() - started) < 180:
                time.sleep(3)
                poll = urllib.request.Request(
                    f"https://api.replicate.com/v1/predictions/{pid}",
                    headers={"Authorization": f"Bearer {key}"},
                )
                resp = json.loads(urllib.request.urlopen(poll, timeout=20).read())
                status = resp.get("status", "")
            if status != "succeeded":
                continue
            out = resp.get("output")
            if isinstance(out, list):
                out = out[0] if out else ""
            if not out:
                continue
            raw_out = urllib.request.urlopen(str(out), timeout=40).read()
            if len(raw_out) < 5000:
                continue
            Path(out_abs_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_abs_path).write_bytes(raw_out)
            return True
        except Exception:
            continue
    return False


def _ensure_transparent_headroom(image_path, *, top_ratio=0.10, side_ratio=0.035, bottom_ratio=0.02):
    """Add transparent safety padding when a cutout touches the image edge.
    This keeps heads/hair from being clipped when templates scale bust cutouts."""
    try:
        from PIL import Image
    except Exception as e:
        print(f"  Cutout headroom skipped (Pillow unavailable): {e}")
        return False

    path = Path(image_path)
    if not path.exists() or path.suffix.lower() != ".png":
        return False
    try:
        with Image.open(path) as img:
            rgba = img.convert("RGBA")
            alpha = rgba.getchannel("A")
            bbox = alpha.getbbox()
            if not bbox:
                return False

            width, height = rgba.size
            left, top, right, bottom = bbox
            subject_h = max(1, bottom - top)
            subject_w = max(1, right - left)
            desired_top = max(40, int(subject_h * top_ratio))
            desired_side = max(20, int(subject_w * side_ratio))
            desired_bottom = max(12, int(subject_h * bottom_ratio))

            add_top = max(0, desired_top - top)
            add_left = max(0, desired_side - left)
            add_right = max(0, desired_side - (width - right))
            add_bottom = max(0, desired_bottom - (height - bottom))
            if not any((add_top, add_left, add_right, add_bottom)):
                return False

            canvas = Image.new("RGBA", (width + add_left + add_right, height + add_top + add_bottom), (0, 0, 0, 0))
            canvas.paste(rgba, (add_left, add_top))
            canvas.save(path)
            print(
                "  Cutout headroom added: "
                f"{path.name} (+{add_top}px top, +{add_left}px left, +{add_right}px right, +{add_bottom}px bottom)"
            )
            return True
    except Exception as e:
        print(f"  Cutout headroom failed for {path.name} (non-fatal): {e}")
        return False


def _crop_to_bounding_box(image_path) -> bool:
    """SH-036: Crop a PNG to its non-transparent content bounding box.

    Background-removal tools leave large transparent borders that cause the
    subject to appear tiny when placed in a fixed-size CSS container.
    Cropping to the alpha bounding box makes the subject fill the frame,
    so object-position:center top correctly shows the face at the top.
    Call this BEFORE _ensure_transparent_headroom so headroom is added
    to the already-cropped image.
    """
    try:
        from PIL import Image
    except Exception:
        print("  Warning: Pillow not available — _crop_to_bounding_box skipped (install Pillow)")
        return False
    path = Path(image_path)
    if not path.exists() or path.suffix.lower() != ".png":
        return False
    try:
        with Image.open(path) as img:
            rgba = img.convert("RGBA")
            bbox = rgba.getchannel("A").getbbox()
            if not bbox:
                return False
            left, top, right, bottom = bbox
            if left == 0 and top == 0 and right == rgba.width and bottom == rgba.height:
                return False  # already tight — nothing to crop
            cropped = rgba.crop(bbox)
            cropped.save(path)
            print(f"  Cutout bbox crop: {path.name} {rgba.size} → {cropped.size}")
            return True
    except Exception as e:
        print(f"  Cutout bbox crop failed for {path.name} (non-fatal): {e}")
        return False


def _generate_cutouts_for_cutout_template(content, paths, work_dir):
    """Auto-create no-background PNG cutouts for cutout template slides.
    Non-blocking: if all providers fail, template still uses regular images."""
    if (content or {}).get("_template_key") != "cutout":
        return
    cut_dir = Path(work_dir) / "resources" / "cutouts"
    cut_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        (3, paths.get("slides", {}).get(3, "")),
        (4, paths.get("slides", {}).get(4, "")),
        (5, paths.get("slides", {}).get(5, "") or paths.get("cover", "")),
    ]
    for slide_idx, rel_path in candidates:
        if not rel_path:
            continue
        src_abs = Path(work_dir) / rel_path
        if not src_abs.exists():
            continue
        out_abs = cut_dir / f"slide_{slide_idx}.png"
        if out_abs.exists() and out_abs.stat().st_size > 5000:
            _crop_to_bounding_box(out_abs)  # SH-036
            _ensure_transparent_headroom(out_abs)
            continue
        ok = _remove_background_with_inference_sh(str(src_abs), str(out_abs))
        if not ok:
            ok = _remove_background_with_replicate(str(src_abs), str(out_abs))
        if ok:
            _crop_to_bounding_box(out_abs)  # SH-036
            _ensure_transparent_headroom(out_abs)
            print(f"  Cutout generated: resources/cutouts/slide_{slide_idx}.png")
        else:
            print(f"  Cutout skipped for slide {slide_idx} (providers unavailable or failed)")


def _generate_seedream_image(prompt, work_dir, filename):
    """Generate image via Seedream 4.5 on Replicate (photoreal, strong with people/places)."""
    if not REPLICATE_KEY or not prompt:
        return ""
    return _replicate_run(
        "bytedance/seedream-4.5",
        {"prompt": prompt[:1000], "aspect_ratio": "3:4"},
        work_dir, filename, timeout=120, model_label="Seedream 4.5",
    )


def _generate_replicate_sdxl(prompt, work_dir, filename):
    """Generate image via Replicate SDXL (cheapest ultimate fallback)."""
    if not REPLICATE_KEY or not prompt:
        return ""
    # stability-ai/sdxl pinned version (public, stable)
    return _replicate_run(
        "7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc",
        {"prompt": prompt[:1000], "width": 864, "height": 1080,
         "num_inference_steps": 25, "guidance_scale": 7.5},
        work_dir, filename, timeout=180, model_label="SDXL",
    )


def _download_drive_photo(drive_url, dest_path):
    """Download a photo from a Drive viewer URL to dest_path. Returns dest_path or ''.

    Uses Drive API with OAuth (required for shared drive files). Falls back to
    anonymous public URL for publicly shared files only.
    """
    try:
        import re as _re
        m = _re.search(r"/d/([A-Za-z0-9_-]+)", drive_url)
        if not m:
            m = _re.search(r"[?&]id=([A-Za-z0-9_-]+)", drive_url)
        if not m:
            return ""
        file_id = m.group(1)
        dest_dir = os.path.dirname(dest_path)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)

        # Primary: Drive API with OAuth (works for private shared drive files)
        _tok_raw = os.environ.get("SHEETS_TOKEN", "")
        if _tok_raw:
            try:
                import json as _json
                import io as _io
                from google.oauth2.credentials import Credentials as _Creds
                from google.auth.transport.requests import Request as _GRequest
                from googleapiclient.discovery import build as _build
                from googleapiclient.http import MediaIoBaseDownload as _MediaDL
                _tok_data = _json.loads(_tok_raw)
                _creds = _Creds.from_authorized_user_info(_tok_data)
                if _creds.expired and _creds.refresh_token:
                    _creds.refresh(_GRequest())
                _drive_svc = _build("drive", "v3", credentials=_creds)
                _request = _drive_svc.files().get_media(fileId=file_id, supportsAllDrives=True)
                _buf = _io.BytesIO()
                _downloader = _MediaDL(_buf, _request)
                done = False
                while not done:
                    _, done = _downloader.next_chunk()
                with open(dest_path, "wb") as f:
                    f.write(_buf.getvalue())
                if os.path.getsize(dest_path) >= 2000:
                    return dest_path
                os.remove(dest_path)
            except Exception as _api_e:
                print(f"  _download_drive_photo API error: {_api_e}")

        # Fallback: anonymous URL (only works for publicly shared files)
        download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r, open(dest_path, "wb") as f:
            f.write(r.read())
        if os.path.getsize(dest_path) < 2000:
            os.remove(dest_path)
            return ""
        return dest_path
    except Exception as e:
        print(f"  _download_drive_photo: {e}")
        return ""


def fetch_all_media(content, niche, work_dir, brief="", topic=""):
    """Download/generate all images needed by this carousel BEFORE build_html().
    Returns dict:
      {"cover": rel_path_or_empty, "slides": {slide_idx: rel_path_or_empty}}
    slide_idx is 1-based (cover=0 implied, slides start at 2 to match enumerate in _build_brazil_html).
    All paths relative to work_dir (e.g. "resources/images/cover.jpg").
    Safe: all failures are caught and return empty string for that slot.
    """
    paths = {
        "cover": "",
        "slides": {},
        "provenance": {
            "cover": {},
            "slides": {}
        }
    }

    # Phase 1: per-build dedup — never reuse the same catalog photo across
    # cover + slides 2-4 unless no alternative exists.
    used_opc_photo_keys = set()

    # ── TIER 0 — OPC Photo Catalog (real jobsite photos, highest priority) ──
    # Checks the 159+ tagged photos before any stock/AI image is fetched.
    # Sets cover + one mid-slide from the same service category.
    if niche == "opc":
        try:
            from photo_matcher import match_opc_photo  # type: ignore
        except ImportError:
            match_opc_photo = None
        if match_opc_photo:
            topic_text = content.get("headline", "") or content.get("topic", "")
            img_dir = Path(work_dir) / "resources" / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            # SH-151: pass real post topic as fallback so the bucket guard works
            # even when content.headline is empty/missing.
            match = match_opc_photo(topic_text, exclude_keys=used_opc_photo_keys, fallback_topic=topic)
            if match and match.get("drive_url"):
                # SH-153: post-fetch category validator — last line of defense.
                # Even if photo_matcher returned a candidate, refuse interior
                # photos for structural topics (driveway/roof/hardscape).
                from photo_matcher import _topic_buckets, _service_bucket  # type: ignore
                _eff_topic = topic_text or topic or ""
                _tb = _topic_buckets(_eff_topic)
                _svc = match.get("service_type", "") or ""
                _sb = _service_bucket(_svc)
                if _tb == "structural" and _sb == "interior":
                    print(f"  [photo_matcher] SH-153 cover REJECTED: {match.get('filename')} "
                          f"(service '{_svc}' is interior, topic '{_eff_topic[:40]}' is structural). "
                          "Falling back to stock/AI.")
                    match = None
            if match and match.get("drive_url"):
                dest = str(img_dir / "opc_catalog_cover.jpg")
                dl = _download_drive_photo(match["drive_url"], dest)
                if dl:
                    paths["cover"] = dl
                    # Phase 1: mark this photo as used so slides 2-4 can't reuse it.
                    used_opc_photo_keys.add((match.get("filename", "") or "").strip().lower())
                    used_opc_photo_keys.add((match.get("drive_url", "") or "").strip().lower())
                    paths["provenance"]["cover"] = {
                        "path": dl, "provider": "opc_catalog",
                        "source_type": "real_photo",
                        "query": match.get("description", ""),
                        # Phase 11/M1 — persist service_type for reviewer
                        # wrong-image gate Tier-1 lookup.
                        "service_type": match.get("service_type", ""),
                        "prompt": "",
                    }
                    print(f"  [photo_matcher] cover: {match.get('filename')} ({match.get('service_type')})")

    def _set_cover(rel_path, provider, source_type, query="", prompt=""):
        paths["cover"] = rel_path
        paths["provenance"]["cover"] = {
            "path": rel_path,
            "provider": provider,
            "source_type": source_type,
            "query": query,
            "prompt": prompt,
        }

    def _set_slide(slide_idx, rel_path, provider, source_type, query="", prompt="", service_type=""):
        paths["slides"][slide_idx] = rel_path
        paths["provenance"]["slides"][str(slide_idx)] = {
            "path": rel_path,
            "provider": provider,
            "source_type": source_type,
            "query": query,
            "prompt": prompt,
            # Phase 11/M1 — present only when known (e.g. opc_catalog matches).
            "service_type": service_type,
        }

    # ── COVER IMAGE CASCADE ────────────────────────────────────────────────
    # Named-person covers: Wiki REST → Wikimedia Commons → bio-initials (NO AI).
    #   Editorial rule: never AI-generate a politician's/public figure's face.
    # Non-person covers (place/event/concept): AI cascade FIRST (NB2 → Seedream4.5
    #   → Seedream5.0 → Gemini → SDXL → DALL-E) for prompt-specific realistic
    #   images, then real-photo fallback (Wiki CC → Pexels → Pixabay) if AI fails.
    #   Real-photo search returns generic stock that often duplicates across slides.
    # Filename slugs the subject so resources/images/ is self-documenting.
    cv = content.get("cover_visual", {})
    if cv:
        search_q = cv.get("option_a", {}).get("search_query", "")
        subject_type = (cv.get("subject_type") or "").strip().lower()
        ai_prompt = cv.get("option_b", {}).get("prompt", "")
        cover_slug = re.sub(r"[^a-z0-9]+", "_", (search_q or "cover").lower()).strip("_")[:40] or "cover"
        cover_fname = f"slide1_{cover_slug}.jpg"

        # Tier 0 — library-first reuse for cover
        if not paths["cover"] and _IMAGE_LIBRARY_AVAILABLE and search_q:
            try:
                lib_hit = _search_library(search_q, niche)
                if lib_hit:
                    rel = _enhance_library_image(lib_hit.get("drive_url", ""), work_dir, cover_fname, search_q)
                    if rel and _vision_accept(rel, search_q, "cover/library", work_dir=work_dir, opc_strict=(niche == "opc")):
                        _set_cover(rel, "library", "library", query=search_q, prompt="scene-lock enhance from library")
                        if _mark_library_used:
                            _mark_library_used(lib_hit.get("row_idx", 0), f"{niche}:{search_q[:40]}")
            except Exception as _e:
                _log_failure("image_library/cover_lookup", _e)

        # Step 1 — named persons go straight to real photo (Wiki REST + Wikimedia Commons).
        # Editorial rule: never AI-generate public figures' faces.
        # Person matches skip Vision validation (Wiki REST already targets the named person by URL).
        if subject_type == "person" and search_q:
            c = _fetch_person_photo(search_q, work_dir, cover_fname)
            if c:
                _set_cover(c, "wikimedia", "cc", query=search_q)

        # Step 2 — non-person covers: AI cascade FIRST (prompt-specific, realistic)
        # SH-049: OPC is real-photos-only — skip ALL AI providers here, fall through to Wikimedia/Pexels.
        if not paths["cover"] and subject_type != "person":
            if _IMAGE_PROVIDERS_AVAILABLE and niche != "opc":
                fresh_prompt = _build_img_prompt(
                    slide_text=search_q, context_image_query=search_q,
                    niche=niche, slide_num=1, subject_type=subject_type,
                    work_dir=work_dir, save=True, brief=brief,
                ) or ai_prompt
                cover_fname = _make_img_filename(search_q, "ai", 1)
                c, used_prov = _gen_ai_image(fresh_prompt, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, f"cover/{used_prov}", work_dir=work_dir):
                    _set_cover(c, used_prov, "ai", query=search_q, prompt=fresh_prompt)
            elif _IMAGE_PROVIDERS_AVAILABLE and niche == "opc":
                print(
                    f"  [OPC] cover: all AI tiers skipped (SH-049 real-photo-only). "
                    f"Falling through to Wikimedia/Pexels for '{search_q[:50]}'"
                )
            elif ai_prompt:
                # Legacy fallback when image_providers not available.
                # OPC: DALL-E is forbidden — real photos only (SH-039). Skip all AI tiers.
                if not (niche == "opc"):
                    c = _generate_gemini_image(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/gemini", work_dir=work_dir):
                        _set_cover(c, "gemini", "ai", query=search_q, prompt=ai_prompt)
                if not paths["cover"] and not (niche == "opc"):
                    c = _generate_seedream_image(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/seedream", work_dir=work_dir):
                        _set_cover(c, "seedream", "ai", query=search_q, prompt=ai_prompt)
                # DALL-E: explicitly skipped for OPC (real-photo only — SH-039)
                if not paths["cover"] and not (niche == "opc"):
                    c = _generate_ai_cover(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/dall-e-3", work_dir=work_dir):
                        _set_cover(c, "dall-e-3", "ai", query=search_q, prompt=ai_prompt)
                if not paths["cover"] and not (niche == "opc"):
                    c = _generate_replicate_sdxl(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/sdxl", work_dir=work_dir):
                        _set_cover(c, "sdxl", "ai", query=search_q, prompt=ai_prompt)
                if not paths["cover"] and niche == "opc":
                    print(
                        f"  [OPC] cover: AI tiers skipped (real-photo only). "
                        f"Falling through to Wikimedia/Pexels/Pixabay for '{search_q[:50]}'"
                    )

        # Step 3 — real-photo fallback (Wiki CC → Pexels → Pixabay)
        # Triggered only when AI cascade exhausted for non-persons,
        # OR for persons whose Wikimedia REST lookup missed.
        if not paths["cover"] and search_q:
            c = _fetch_person_photo(search_q, work_dir, cover_fname)
            # Wiki for non-persons gets Vision check; person path is exact-name match (skip)
            if c and (subject_type == "person" or _vision_accept(c, search_q, "cover/wikimedia", work_dir=work_dir)):
                _set_cover(c, "wikimedia", "cc", query=search_q)
            if not paths["cover"] and subject_type != "person":
                c = _fetch_pexels_image(search_q, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, "cover/pexels", work_dir=work_dir):
                    _set_cover(c, "pexels", "stock", query=search_q)
            if not paths["cover"] and subject_type != "person":
                c = _fetch_pixabay_image(search_q, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, "cover/pixabay", work_dir=work_dir):
                    _set_cover(c, "pixabay", "stock", query=search_q)

        # Person photo missed all CC tiers → contextual Pexels fallback (no face, topic context).
        # This ensures the cover is never empty — a courtroom/institution/event photo is
        # better than black text on black. Editorial rule preserved: we never AI-generate
        # the person's face, but a contextual background is always acceptable.
        if not paths["cover"] and subject_type == "person" and search_q:
            ctx_q = cv.get("option_b", {}).get("prompt", "") or f"{search_q} justice court law"
            ctx_q = ctx_q[:80]
            c = _fetch_pexels_image(ctx_q, work_dir, cover_fname)
            if c:
                _set_cover(c, "pexels", "stock", query=ctx_q)
                print(f"  cover fallback → Pexels context image for person miss: {ctx_q[:50]}")
            if not paths["cover"]:
                c = _fetch_pixabay_image(ctx_q, work_dir, cover_fname)
                if c:
                    _set_cover(c, "pixabay", "stock", query=ctx_q)
                    print(f"  cover fallback → Pixabay context image for person miss")

        # Non-person last resort — vision may have rejected everything above.
        # Accept ANY Pexels/Pixabay result without vision check so cover is never black.
        if not paths["cover"] and subject_type != "person" and search_q:
            c = _fetch_pexels_image(search_q[:60], work_dir, cover_fname)
            if c:
                _set_cover(c, "pexels", "stock", query=search_q)
                print(f"  cover last-resort → Pexels (no vision): {search_q[:50]}")
            if not paths["cover"]:
                c = _fetch_pixabay_image(search_q[:60], work_dir, cover_fname)
                if c:
                    _set_cover(c, "pixabay", "stock", query=search_q)
                    print(f"  cover last-resort → Pixabay (no vision): {search_q[:50]}")

    # ── MIDDLE SLIDES — CONTEXT IMAGES ────────────────────────────────────
    # Cascade: AI cascade FIRST (NB2 → Seedream4.5 → Seedream5.0 → Gemini → SDXL
    # → DALL-E) for prompt-specific realistic images. Real-photo fallback
    # (Wiki CC → Pexels → Pixabay) only when AI exhausted — generic stock
    # frequently duplicates across slides for similar construction queries.
    # Applies only to slides with visual_hint == "context-image" or "product-photo"
    # (OPC tip/progress/illustrated/cutout all use product-photo on mid slides).
    # bio-cards are rendered separately from mentioned_people[*].image_hint.
    for i, slide in enumerate(content.get("slides", []), start=2):
        _slide_hint = slide.get("visual_hint", "")
        # OPC: match catalog for both context-image AND product-photo slides.
        # Non-OPC: only context-image (product-photo is not a used hint for news).
        if niche == "opc":
            if _slide_hint not in ("context-image", "product-photo"):
                continue
        else:
            if _slide_hint != "context-image":
                continue
        cq = slide.get("context_image_query", "").strip()
        if not cq:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", cq.lower()).strip("_")[:40] or "context"
        fname = f"slide{i}_{slug}.jpg"
        ai_prompt = (
            f"Ultra photorealistic documentary photograph of {cq}, natural materials and textures, "
            f"real lighting, no illustration, no 3D render, no cartoon, no plastic skin, no text."
        )

        img_path = ""
        accepted = False

        # ── TIER 0 (OPC only) — Photo Catalog (real jobsite photos) ─────────
        # Checked BEFORE any AI or stock spend. Matches by keyword against the
        # AI description + service type + filename in the 158-row catalog.
        # Phase 1+2: ask for top-3 candidates with dedup; if Vision rejects the
        # best, try candidate 2 then 3 before falling to stock/AI.
        if niche == "opc" and not accepted:
            try:
                from photo_matcher import match_opc_photo_candidates as _opc_candidates  # type: ignore
                _img_dir = Path(work_dir) / "resources" / "images"
                _img_dir.mkdir(parents=True, exist_ok=True)
                for _idx, opc_hit in enumerate(
                    _opc_candidates(cq, exclude_keys=used_opc_photo_keys, limit=3), start=1
                ):
                    if not opc_hit.get("drive_url"):
                        continue
                    _try_fname = fname if _idx == 1 else f"slide{i}_cand{_idx}_{re.sub(r'[^a-z0-9]+','_',(opc_hit.get('filename','') or '').lower()).strip('_')[:30] or 'opc'}.jpg"
                    _dl = _download_drive_photo(opc_hit["drive_url"], str(_img_dir / _try_fname))
                    _fname_key = (opc_hit.get("filename", "") or "").strip().lower()
                    _drive_key = (opc_hit.get("drive_url", "") or "").strip().lower()
                    # Use post topic (not slide-specific cq) — Vision can answer "is this about
                    # pergola work?" but cannot answer "does this show cost overrun?" (abstract).
                    _cat_vision_q = (topic or cq).strip()
                    if _dl and _vision_accept(_dl, _cat_vision_q, f"slide{i}/opc_catalog", work_dir=work_dir, opc_strict=True):
                        _set_slide(i, _dl, "opc_catalog", "real_photo", query=cq,
                                   service_type=opc_hit.get("service_type", ""))
                        accepted = True
                        used_opc_photo_keys.add(_fname_key)
                        used_opc_photo_keys.add(_drive_key)
                        print(f"  [photo_matcher] slide{i}: {opc_hit.get('filename')} "
                              f"(cand{_idx}, {opc_hit.get('service_type')}, q={opc_hit.get('quality')})")
                        break
                    else:
                        # Vision rejected this candidate — exclude it from later slides too.
                        used_opc_photo_keys.add(_fname_key)
                        used_opc_photo_keys.add(_drive_key)
                        print(f"  [photo_matcher] slide{i} cand{_idx} rejected: {opc_hit.get('filename')}")
            except Exception as _opc_e:
                _log_failure(f"photo_matcher/slide{i}", _opc_e)

        # Tier 1 — library-first reuse + scene-preserving enhancement
        if _IMAGE_LIBRARY_AVAILABLE:
            try:
                lib_hit = _search_library(cq, niche)
                if lib_hit:
                    rel = _enhance_library_image(lib_hit.get("drive_url", ""), work_dir, fname, cq)
                    if rel and _vision_accept(rel, cq, f"slide{i}/library", work_dir=work_dir, opc_strict=(niche == "opc")):
                        _set_slide(i, rel, "library", "library", query=cq, prompt="scene-lock enhance from library")
                        accepted = True
                        if _mark_library_used:
                            _mark_library_used(lib_hit.get("row_idx", 0), f"{niche}:{cq[:40]}")
            except Exception as _e:
                _log_failure("image_library/slide_lookup", _e)

        # Tier 1: AI cascade — NB2 → Seedream 4.5 → Seedream 5.0 → Gemini → SDXL → DALL-E
        # SH-049: OPC is real-photos-only — skip ALL AI providers, fall through to Wikimedia/Pexels.
        if not accepted and _IMAGE_PROVIDERS_AVAILABLE and niche != "opc":
            fresh_prompt = _build_img_prompt(
                slide_text=cq, context_image_query=cq,
                niche=niche, slide_num=i, work_dir=work_dir, save=True, brief=brief,
            ) or ai_prompt
            fname = _make_img_filename(cq, "ai", i)
            img_path, used_prov = _gen_ai_image(fresh_prompt, work_dir, fname)
            if img_path and _vision_accept(img_path, cq, f"slide{i}/{used_prov}", work_dir=work_dir):
                print(f"  Slide {i}: {used_prov} image for '{cq[:50]}'")
                _set_slide(i, img_path, used_prov, "ai", query=cq, prompt=fresh_prompt)
                accepted = True
            else:
                img_path = ""
        elif not accepted and _IMAGE_PROVIDERS_AVAILABLE and niche == "opc":
            print(f"  [OPC] slide{i}: AI tiers skipped (SH-049). Falling through to Wikimedia/Pexels.")
        else:
            # Legacy fallback when image_providers not available.
            # OPC: skip DALL-E (AI-generated photos not allowed for OPC — real photos only).
            if not (niche == "opc"):
                img_path = _generate_gemini_image(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/gemini", work_dir=work_dir):
                    _set_slide(i, img_path, "gemini", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""
            if not accepted and not (niche == "opc"):
                img_path = _generate_seedream_image(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/seedream", work_dir=work_dir):
                    _set_slide(i, img_path, "seedream", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""
            if not accepted and not (niche == "opc"):
                img_path = _generate_replicate_sdxl(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/sdxl", work_dir=work_dir):
                    _set_slide(i, img_path, "sdxl", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""
            # DALL-E: explicitly skipped for OPC (real-photo rule SH-039)
            if not accepted and not (niche == "opc"):
                img_path = _generate_ai_cover(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/dall-e-3", work_dir=work_dir):
                    _set_slide(i, img_path, "dall-e-3", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""

        # Tier 2: real-photo fallback (Wiki CC → Pexels → Pixabay) — only when AI exhausted.
        # For OPC this is TIER 1 (AI tiers were skipped above) — Wikimedia → Pexels → Pixabay → STOP.
        # SH-056: OPC queries get a photorealistic suffix to guide stock providers.
        _cq_stock = _opc_photo_query(cq, niche)
        if not accepted:
            img_path = _fetch_person_photo(_cq_stock, work_dir, fname)
            if img_path and _vision_accept(img_path, _cq_stock, f"slide{i}/wikimedia", work_dir=work_dir):
                print(f"  Slide {i}: Wikimedia fallback for '{cq[:50]}'")
                _set_slide(i, img_path, "wikimedia", "cc", query=cq, prompt=ai_prompt)
                accepted = True
            else:
                img_path = ""
        if not accepted:
            img_path = _fetch_pexels_image(_cq_stock, work_dir, fname)
            if img_path and _vision_accept(img_path, _cq_stock, f"slide{i}/pexels", work_dir=work_dir):
                print(f"  Slide {i}: Pexels fallback for '{cq[:50]}'")
                _set_slide(i, img_path, "pexels", "stock", query=cq, prompt=ai_prompt)
                accepted = True
            else:
                img_path = ""
        if not accepted:
            img_path = _fetch_pixabay_image(_cq_stock, work_dir, fname)
            if img_path and _vision_accept(img_path, _cq_stock, f"slide{i}/pixabay", work_dir=work_dir):
                print(f"  Slide {i}: Pixabay fallback for '{cq[:50]}'")
                _set_slide(i, img_path, "pixabay", "stock", query=cq, prompt=ai_prompt)
                accepted = True

        # ── ALT QUERY RETRY ────────────────────────────────────────────────
        # Primary cq is the ideal/specific match. context_image_query_alt is
        # the broader safety net. If the primary cascade missed every tier,
        # retry the catalog + stock cascade with the alt query before giving
        # up. Catalog match on the broader query often hits a real OPC photo
        # that the strict primary query rejected.
        cq_alt = slide.get("context_image_query_alt", "").strip()
        if not accepted and cq_alt and cq_alt.lower() != cq.lower():
            print(f"  Slide {i}: primary query missed — retrying with alt '{cq_alt[:60]}'")
            _alt_slug = re.sub(r"[^a-z0-9]+", "_", cq_alt.lower()).strip("_")[:40] or "context_alt"
            _alt_fname = f"slide{i}_{_alt_slug}.jpg"
            _cq_alt_stock = _opc_photo_query(cq_alt, niche)

            # Retry catalog (OPC only) with alt query — also dedup-aware
            if niche == "opc":
                try:
                    from photo_matcher import match_opc_photo_candidates as _opc_alt_candidates  # type: ignore
                    _img_dir = Path(work_dir) / "resources" / "images"
                    _img_dir.mkdir(parents=True, exist_ok=True)
                    for _aidx, _alt_hit in enumerate(
                        _opc_alt_candidates(cq_alt, exclude_keys=used_opc_photo_keys, limit=3), start=1
                    ):
                        if not _alt_hit.get("drive_url"):
                            continue
                        _try_alt_fname = _alt_fname if _aidx == 1 else f"slide{i}_alt{_aidx}_{re.sub(r'[^a-z0-9]+','_',(_alt_hit.get('filename','') or '').lower()).strip('_')[:30] or 'opc'}.jpg"
                        _dl = _download_drive_photo(_alt_hit["drive_url"], str(_img_dir / _try_alt_fname))
                        _fname_key = (_alt_hit.get("filename", "") or "").strip().lower()
                        _drive_key = (_alt_hit.get("drive_url", "") or "").strip().lower()
                        _cat_alt_vision_q = (topic or cq_alt).strip()
                        if _dl and _vision_accept(_dl, _cat_alt_vision_q, f"slide{i}/opc_catalog_alt", work_dir=work_dir):
                            _set_slide(i, _dl, "opc_catalog", "real_photo", query=cq_alt,
                                       service_type=_alt_hit.get("service_type", ""))
                            accepted = True
                            used_opc_photo_keys.add(_fname_key)
                            used_opc_photo_keys.add(_drive_key)
                            print(f"  [photo_matcher alt] slide{i}: {_alt_hit.get('filename')} (cand{_aidx})")
                            break
                        else:
                            used_opc_photo_keys.add(_fname_key)
                            used_opc_photo_keys.add(_drive_key)
                except Exception:
                    pass

            # Retry Wikimedia with alt
            if not accepted:
                _alt_path = _fetch_person_photo(_cq_alt_stock, work_dir, _alt_fname)
                if _alt_path and _vision_accept(_alt_path, _cq_alt_stock, f"slide{i}/wikimedia_alt", work_dir=work_dir):
                    print(f"  Slide {i}: Wikimedia alt for '{cq_alt[:50]}'")
                    _set_slide(i, _alt_path, "wikimedia", "cc", query=cq_alt, prompt=ai_prompt)
                    accepted = True

            # Retry Pexels with alt
            if not accepted:
                _alt_path = _fetch_pexels_image(_cq_alt_stock, work_dir, _alt_fname)
                if _alt_path and _vision_accept(_alt_path, _cq_alt_stock, f"slide{i}/pexels_alt", work_dir=work_dir):
                    print(f"  Slide {i}: Pexels alt for '{cq_alt[:50]}'")
                    _set_slide(i, _alt_path, "pexels", "stock", query=cq_alt, prompt=ai_prompt)
                    accepted = True

            # Retry Pixabay with alt
            if not accepted:
                _alt_path = _fetch_pixabay_image(_cq_alt_stock, work_dir, _alt_fname)
                if _alt_path and _vision_accept(_alt_path, _cq_alt_stock, f"slide{i}/pixabay_alt", work_dir=work_dir):
                    print(f"  Slide {i}: Pixabay alt for '{cq_alt[:50]}'")
                    _set_slide(i, _alt_path, "pixabay", "stock", query=cq_alt, prompt=ai_prompt)
                    accepted = True

        # OPC — all real-photo tiers (primary + alt) exhausted → leave slot empty (no DALL-E)
        if not accepted and niche == "opc":
            print(
                f"  [OPC] slide{i}: all real-photo tiers exhausted for '{cq[:50]}'"
                f"{' / alt ' + cq_alt[:40] if cq_alt else ''} — "
                f"placeholder/bio-initials will render (DALL-E not allowed for OPC)"
            )

    # ── BRAZIL NATIVE SLIDE PHOTOS (motion alternating slides) ────────────────
    # Fetches per-slide CC photos for odd slides (3, 5, 7…) in the Brazil native
    # template. Uses photo_query from clip_suggestions (set by Haiku) or falls
    # back to slide heading. Stores in paths["slide_photos"] = {slide_i: path}.
    if niche in ("brazil", "usa"):
        try:
            slide_ph = _fetch_slide_photos_brazil(content, work_dir)
            paths["slide_photos"] = slide_ph
            print(f"  Slide photos fetched: {list(slide_ph.keys())}")
        except Exception as _e:
            paths["slide_photos"] = {}
            print(f"  Slide photos fetch failed (non-fatal): {_e}")
    else:
        paths["slide_photos"] = {}

    fetched_total = (1 if paths["cover"] else 0) + len(paths["slides"])
    _generate_cutouts_for_cutout_template(content, paths, work_dir)
    print(f"  Media fetch: {fetched_total} image(s) ready (cover={bool(paths['cover'])}, slides={list(paths['slides'].keys())})")
    return paths


def _fetch_pexels_video(query, dest_dir, filename):
    """Download a Pexels stock video clip (portrait orientation preferred).
    Good for places, events, institutions — NOT specific people.
    Returns absolute path if downloaded, else empty string.
    """
    if not PEXELS_KEY:
        return ""
    dest_path = Path(dest_dir) / "clips" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 10000:
        return str(dest_path)
    try:
        q = urllib.parse.urlencode({"query": query, "per_page": "5", "size": "medium", "orientation": "portrait"})
        req = urllib.request.Request(
            f"https://api.pexels.com/videos/search?{q}",
            headers={"Authorization": PEXELS_KEY}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        videos = data.get("videos", [])
        if not videos:
            return ""
        # Pick first video with a portrait MP4 file
        for v in videos:
            for vf in sorted(v.get("video_files", []), key=lambda x: x.get("width", 0)):
                if vf.get("file_type") == "video/mp4" and vf.get("height", 0) >= vf.get("width", 1):
                    url = vf.get("link", "")
                    if not url:
                        continue
                    raw = urllib.request.urlopen(url, timeout=60).read()
                    if len(raw) < 10000:
                        continue
                    dest_path.write_bytes(raw)
                    print(f"  Pexels video: {filename} ({len(raw)//1024}KB) ← '{query[:40]}'")
                    return str(dest_path)
    except Exception as e:
        print(f"  Pexels video miss ({query[:40]}): {e}")
    return ""


def _fetch_youtube_clip_apify(youtube_query, dest_dir, filename):
    """Route A: Apify streamers~youtube-scraper → search → get URL.
    Route B: Apify streamers~youtube-video-downloader → download MP4.
    Returns absolute path if downloaded, else empty string.
    """
    if not APIFY_KEY:
        return ""
    dest_path = Path(dest_dir) / "clips" / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 10000:
        return str(dest_path)

    def _apify_run(actor_id, input_body, wait=120):
        """Synchronous Apify actor run. Returns dataset items list or []."""
        try:
            actor_slug = actor_id.replace("/", "~")
            run_url = f"https://api.apify.com/v2/acts/{actor_slug}/runs?token={APIFY_KEY}&waitForFinish={wait}"
            body = json.dumps(input_body).encode()
            req = urllib.request.Request(run_url, data=body,
                                          headers={"Content-Type": "application/json"}, method="POST")
            resp = json.loads(urllib.request.urlopen(req, timeout=wait + 30).read())
            run_data = resp.get("data", {})
            if run_data.get("status") not in ("SUCCEEDED",):
                print(f"  Apify {actor_id}: status={run_data.get('status')} — skipping")
                return []
            dataset_id = run_data.get("defaultDatasetId", "")
            if not dataset_id:
                return []
            items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_KEY}"
            items = json.loads(urllib.request.urlopen(items_url, timeout=30).read())
            return items if isinstance(items, list) else []
        except Exception as e:
            print(f"  Apify {actor_id} run error: {e}")
            return []

    try:
        # Step 1: search YouTube for the query → get video URL
        search_items = _apify_run("streamers~youtube-scraper",
                                   {"searchTerms": [youtube_query], "maxResults": 1, "saveVideos": False})
        if not search_items:
            return ""
        video_url = search_items[0].get("url") or search_items[0].get("videoUrl") or ""
        if not video_url or "youtube" not in video_url:
            return ""
        print(f"  Apify found: {video_url[:60]} for '{youtube_query[:40]}'")

        # Step 2: download the video via downloader actor.
        # streamers~youtube-video-downloader accepts slightly different shapes across versions —
        # pass videoUrls (array), quality AND resolution to maximize hit rate.
        dl_items = _apify_run("streamers~youtube-video-downloader",
                               {"videoUrls": [{"url": video_url}],
                                "url": video_url,
                                "format": "mp4",
                                "quality": "360p",
                                "resolution": "360p"}, wait=180)
        download_url = ""
        for item in dl_items:
            download_url = (item.get("downloadUrl") or item.get("url") or
                            item.get("videoUrl") or item.get("link") or "")
            if download_url:
                break
        if not download_url:
            print(f"  Apify downloader returned no URL for {video_url[:60]}")
            return ""

        raw = urllib.request.urlopen(download_url, timeout=120).read()
        if len(raw) < 10000:
            return ""
        dest_path.write_bytes(raw)
        print(f"  YouTube clip via Apify: {filename} ({len(raw)//1024}KB)")
        return str(dest_path)

    except Exception as e:
        print(f"  Apify YouTube clip error ({youtube_query[:40]}): {e}")
        return ""


# ── Phase 8C — template-aware image augmentation ──────────────────────────
# Runs AFTER fetch_all_media() when content["_slide_plan"] is present (smart
# path). For each standalone slide that needs images fetch_all_media did NOT
# cover, this helper fetches the right kind into the right media_paths key:
#
#   opc_four_card_grid → 4 distinct photos in media_paths["cards"][1..4]
#   opc_base           → bg (cover) + sticker (slides[N]) — both verified
#   opc_statement      → person photo → media_paths["slides"][N]
#   opc_progress_media → jobsite photo → media_paths["slides"][N] (OPC catalog first)
#   opc_duotone        → hero high-contrast → media_paths["slides"][N]
#   opc_item_spotlight → 1 close-up → media_paths["slides"][N]
#   opc_material_profile → no image needed
#
# The Haiku-emitted image_query lives at content[<tid>]["image_query"] (and
# content[<tid>]["card_image_queries"] for four_card_grid). Phase 8D feeds
# those queries; if missing we derive from tip-shape context_image_query.

def _opc_template_image_need(template_id):
    """Returns the per-template image strategy. Used by fetch_template_aware_media."""
    return {
        "opc_material_profile": "none",
        "opc_four_card_grid":   "four_cards",
        "opc_item_spotlight":   "single_closeup",
        "opc_statement":        "person",
        "opc_base":             "hero_plus_sticker",
        "opc_progress_media":   "jobsite",
        "opc_duotone":          "hero_drama",
    }.get(template_id, "none")


def _derive_image_query_for_template(content, template_id, slide_num, fallback_topic=""):
    """Return the best image-search query string for a standalone slide.
    Order: per-template Haiku query → per-card list (for fcg) → tip
    context_image_query for that slide → tip cover query → topic.
    """
    nested = (content or {}).get(template_id) or {}
    if isinstance(nested, dict):
        q = nested.get("image_query") or nested.get("photo_query")
        if q:
            return str(q).strip()
    # Tip-shape per-slide context_image_query (slide_num matches "slide" key)
    for s in content.get("slides", []) or []:
        if s.get("slide") == slide_num and s.get("context_image_query"):
            return str(s["context_image_query"]).strip()
    cv = (content.get("cover_visual") or {}).get("option_a") or {}
    if cv.get("search_query"):
        return str(cv["search_query"]).strip()
    return fallback_topic.strip()


def fetch_template_aware_media(content, niche, work_dir, paths, brief=""):
    """Phase 8C — augment paths in-place based on the slide plan's template_ids.
    No-op when there's no slide plan (legacy tip path) or niche != opc.
    """
    if niche != "opc":
        return paths
    plan = (content or {}).get("_slide_plan") or {}
    slides = plan.get("slides") or []
    if not slides:
        return paths

    fallback_topic = (
        plan.get("topic")
        or (content or {}).get("topic", "")
        or (content or {}).get("headline", "")
    )
    comparison_pair = (content or {}).get("_comparison_pair") or {}
    img_dir = Path(work_dir) / "resources" / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    # Per-card image fetcher for four_card_grid — needs 4 distinct queries.
    def _fetch_card_image(query, idx):
        slug = re.sub(r"[^a-z0-9]+", "_", (query or "card").lower()).strip("_")[:40] or f"card{idx}"
        fname = f"fcg_card{idx}_{slug}.jpg"
        # Pexels then Pixabay — these are decision-grid thumbnails, no AI tier.
        path = _fetch_pexels_image(_opc_photo_query(query, "opc"), work_dir, fname)
        if not path:
            path = _fetch_pixabay_image(_opc_photo_query(query, "opc"), work_dir, fname)
        return path

    # OPC Drive catalog matcher — re-used here to fetch a SECOND/THIRD photo
    # when a standalone needs jobsite/sticker imagery distinct from the cover.
    try:
        from photo_matcher import match_opc_photo  # type: ignore
    except ImportError:
        match_opc_photo = None

    def _fetch_catalog_photo(query, dest_filename):
        """Try OPC catalog match → download → return relative path. None on miss."""
        if not match_opc_photo:
            return None
        m = match_opc_photo(query or fallback_topic)
        if not m or not m.get("drive_url"):
            return None
        dest = str(img_dir / dest_filename)
        return _download_drive_photo(m["drive_url"], dest)

    paths.setdefault("cards", {})
    fetched_log = []

    for s in slides:
        n = s.get("slide")
        tid = s.get("template_id", "")
        need = _opc_template_image_need(tid)
        if need == "none":
            continue

        # opc_four_card_grid — 4 cards, 4 queries.
        if need == "four_cards" and tid == "opc_four_card_grid":
            nested = (content or {}).get(tid) or {}
            queries = nested.get("card_image_queries") or []
            titles  = nested.get("card_titles") or []
            if comparison_pair:
                pair_queries = _comparison_media_queries(comparison_pair, fallback_topic)
                # Always use pair-aware queries for comparison card media.
                # Generated text can stay nuanced; media must stay balanced.
                queries = pair_queries
            for i in range(4):
                if paths["cards"].get(i + 1):
                    continue
                q = ""
                if i < len(queries) and queries[i]:
                    q = str(queries[i]).strip()
                elif i < len(titles) and titles[i]:
                    q = f"{titles[i]} construction detail residential"
                else:
                    q = f"{fallback_topic} option {i+1}"
                p = _fetch_card_image(q, i + 1)
                if p:
                    paths["cards"][i + 1] = p
                    fetched_log.append(f"card{i+1}={p}")
            continue

        # Single-slot standalones — populate slides[N] if empty.
        if paths.get("slides", {}).get(n):
            continue
        q = _derive_image_query_for_template(content, tid, n, fallback_topic)

        path = ""
        if need == "jobsite":
            # Real OPC photo first, NEVER stock — progress_media is editorial proof.
            path = _fetch_catalog_photo(q, f"slide{n}_jobsite.jpg") or ""
            if not path:
                # Editorial fallback: re-use cover photo from OPC catalog if possible.
                cover_p = paths.get("cover", "")
                if cover_p and "/opc_catalog_" in cover_p:
                    path = cover_p
        elif need == "person":
            # Person photo — Pexels portrait + B&W treatment in CSS already handles look.
            path = _fetch_pexels_image(_opc_photo_query(f"{q} portrait construction worker", "opc"),
                                        work_dir, f"slide{n}_person.jpg")
        elif need in ("hero_drama", "single_closeup"):
            # Stock photo with template hint added.
            mod = " dramatic high contrast" if need == "hero_drama" else " close-up detail"
            path = _fetch_pexels_image(_opc_photo_query(q + mod, "opc"),
                                        work_dir, f"slide{n}_{need}.jpg")
            if not path:
                path = _fetch_pixabay_image(_opc_photo_query(q + mod, "opc"),
                                             work_dir, f"slide{n}_{need}.jpg")
        elif need == "hero_plus_sticker":
            # Bg already from cover; sticker = catalog detail or stock close-up.
            path = _fetch_catalog_photo(q, f"slide{n}_sticker.jpg") or _fetch_pexels_image(
                _opc_photo_query(q + " detail close-up", "opc"),
                work_dir, f"slide{n}_sticker.jpg",
            )

        if path:
            paths.setdefault("slides", {})[n] = path
            fetched_log.append(f"slide{n}({tid})={path}")

    if fetched_log:
        print(f"  [phase8c] template-aware images: {' | '.join(fetched_log)}")
    return paths


def fetch_clips(content, work_dir):
    """Download video clips for motion version. Returns (clips, clip_failures).

    clips       = {slide_idx: abs_clip_path} for every slot that succeeded.
    clip_failures = {slide_idx: slot_name}   for every slot that exhausted all tiers.
    Callers must unpack both: clips, clip_failures = fetch_clips(content, work_dir)

    Distribution: cover + up to 2 evenly spaced middle slides (never sources).
    Source chain per slot is delegated to motion_sources.fetch_clip_with_fallback:
      1. Real short clips (clip collections / YouTube / Instagram / Archive.org / Wikimedia)
      2. GIPHY when safe/relevant
      3. Empty = static PNG/no motion fallback by caller.

    Philosophy (Priscila, 2026-04-20): "so many fallbacks that something will go through."
    Legacy keep-alive: _fetch_youtube_clip_apify and _fetch_pexels_video remain as
    the engines for tiers 1 and 3 and are still called directly by motion_sources.
    """
    try:
        from motion_sources import fetch_clip_with_fallback
    except ImportError:
        from scripts.content_creator.motion_sources import fetch_clip_with_fallback  # type: ignore

    clips = {}
    clip_failures = {}
    suggestions = content.get("clip_suggestions", [])
    if not suggestions:
        return clips, clip_failures

    phase1_cover_only = os.environ.get("MOTION_PHASE1_TEST", "0").strip() == "1"
    if phase1_cover_only and os.environ.get("MOTION_FORCE_NO_CLIP", "0").strip() == "1":
        print("  fetch_clips: Phase 1 no-clip proof — skipping all clip providers")
        return clips, clip_failures

    slides = content.get("slides", [])
    n_slides = len(slides)

    by_slide = {c.get("slide", 0): c for c in suggestions}

    # Distribution: cover always first. Then pick up to 2 evenly from middle slides (not last).
    clip_slots = []
    cover_suggestion = by_slide.get(1) or (suggestions[0] if suggestions else None)
    if cover_suggestion:
        clip_slots.append(("cover", 1, cover_suggestion))

    if phase1_cover_only:
        print("  fetch_clips: Phase 1 proof mode — cover clip only")
    else:
        middle_candidates = [c for c in suggestions if c.get("slide", 0) not in (1, n_slides + 1)]
        if len(middle_candidates) >= 2:
            mid_idx = len(middle_candidates) // 2
            chosen_middle = [middle_candidates[0]]
            if mid_idx != 0 and middle_candidates[mid_idx] is not chosen_middle[0]:
                chosen_middle.append(middle_candidates[mid_idx])
        elif middle_candidates:
            chosen_middle = middle_candidates
        else:
            chosen_middle = []

        for c in chosen_middle:
            clip_slots.append((f"slide_{c.get('slide',0)}", c.get("slide", 0), c))

    # Fetch each slot through the unified 7-tier chain
    for slot_name, slide_idx, sugg in clip_slots:
        # Require at least one query string
        if not any(sugg.get(k) for k in ("youtube_query", "instagram_query",
                                          "pexels_query", "pixabay_query",
                                          "archive_query", "wikimedia_query", "query")):
            continue
        visual_hint = sugg.get("visual_hint", "context-image")
        fname = f"{slot_name}.mp4"
        path = fetch_clip_with_fallback(sugg, work_dir, fname, visual_hint=visual_hint)
        if path:
            clips[slide_idx] = path
        else:
            print(f"  Clip slot '{slot_name}': every tier missed — static Phase 1 fallback/no clip slot")
            clip_failures[slide_idx] = slot_name

    print(f"  fetch_clips: {len(clips)}/{len(clip_slots)} clip(s) ready: {list(clips.keys())}")
    if clip_failures:
        print(f"  fetch_clips: {len(clip_failures)} slot(s) failed — placeholder div will render in motion HTML: {list(clip_failures.keys())}")
    return clips, clip_failures


def build_motion_html(content, niche, topic_slug, work_dir, clips, media_paths=None, clip_failures=None):
    """Generate per-slide motion HTML files for Playwright video recording.

    All-middle rule (cover + all middles, sources always excluded):
      - Cover always gets a motion file.
      - Middle slides: all get motion files (Phase 1 guard overrides to cover-only via MOTION_PHASE1_TEST).
      - Sources slide: never gets motion (sources index = n_slides+2, outside range(2, n_slides+2)).

    Each motion HTML has static text plus either:
      - Layout A/B: a looping <video> in a framed clip slot when a clip is available.
      - Layout D: full-bleed looping <video> behind a dark overlay when a clip is available.
      - No clip: a static background image with no animation for Phase 1 proof tests.

    Works for all niches (brazil, usa, opc). Existing cover.html is NOT modified.
    Returns list of (slide_idx, html_path) tuples.
    """

    results = []
    slides = content.get("slides", [])
    n_slides = len(slides)   # middle slides only; cover=1, sources=n_slides+2
    total_slides = n_slides + 2  # cover + middles + sources
    suggestions_by_slide = {
        int(s.get("slide", 0)): s
        for s in (content.get("clip_suggestions", []) or [])
        if str(s.get("slide", "")).isdigit()
    }

    css = (
        _brazil_motion_css()
        .replace("{SLIDE_INSET_PX}", str(SLIDE_INSET_PX))
        .replace("{{", "{")
        .replace("}}", "}")
    )

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # Which slide indices get motion: Phase 1 proof tests are cover-only.
    # Legacy/expanded motion can still record cover + all middle slides.
    if os.environ.get("MOTION_PHASE1_TEST", "0").strip() == "1":
        motion_indices = [1]
        print("  build_motion_html: Phase 1 proof mode — cover motion HTML only")
    else:
        motion_indices = [1] + [i for i in range(2, n_slides + 2)]

    for slide_idx in motion_indices:
        clip_path = clips.get(slide_idx)
        rel_clip = os.path.relpath(clip_path, work_dir) if clip_path else None
        html_body = ""

        # Resolve slide_data and layout_hint before building the clip block so routing
        # decisions can depend on per-slide metadata even when that data comes from the
        # middle-slide array (which would otherwise only be accessed inside the else branch).
        if slide_idx == 1:
            slide_data = {}
            _env_layout = os.environ.get("MOTION_COVER_LAYOUT", "A").upper()
            cover_sugg = suggestions_by_slide.get(1, {})
            layout_hint = (
                content.get("cover_layout_hint")
                or content.get("layout_hint")
                or cover_sugg.get("layout_hint")
                or _env_layout
            ).upper()
        else:
            _data_idx = max(0, min(slide_idx - 2, len(slides) - 1)) if slides else 0
            slide_data = slides[_data_idx] if slides else {}
            slide_sugg = suggestions_by_slide.get(slide_idx, {})
            layout_hint = (slide_data.get("layout_hint") or slide_sugg.get("layout_hint") or "A").upper()
        text_density = str(slide_data.get("text_density") or suggestions_by_slide.get(slide_idx, {}).get("text_density") or "").lower()
        if slide_idx != 1 and layout_hint == "D":
            layout_hint = "B"
        if layout_hint == "D" and text_density == "high":
            layout_hint = "A"

        # Build clip block, dark overlay, and slide-class modifier from layout_hint.
        # Layout A: framed sticker 260×340 top-right (default — safe when layout_hint absent).
        # Layout B: landscape clip 380×220 bottom-left (place / event / institution).
        # Layout D: full-bleed clip + dark overlay (text must be high-contrast).
        # Layout C: deferred (multi-face/network grid — not yet implemented, falls back to A).
        clip_block = ""
        overlay_block = ""
        layout_class = ""
        if rel_clip:
            if layout_hint == "D":
                clip_block = f"""
    <div class="clip-layout-d">
      <video class="clip-video" autoplay muted loop playsinline>
        <source src="{rel_clip}" type="video/mp4">
      </video>
    </div>"""
                overlay_block = '<div class="layout-d-overlay"></div>'
                layout_class = "slide-layout-d"
            elif layout_hint == "B":
                clip_block = f"""
    <div class="clip-frame clip-layout-b{'  clip-frame-mid' if slide_idx != 1 else ''}">
      <video class="clip-video" autoplay muted loop playsinline>
        <source src="{rel_clip}" type="video/mp4">
      </video>
    </div>"""
            else:  # Layout A (default — also covers C until implemented)
                clip_block = f"""
    <div class="clip-frame{'  clip-frame-mid' if slide_idx != 1 else ''}">
      <video class="clip-video" autoplay muted loop playsinline>
        <source src="{rel_clip}" type="video/mp4">
      </video>
    </div>"""
        elif (clip_failures or {}).get(slide_idx):
            slot = (clip_failures or {}).get(slide_idx, "cover")
            clip_block = f"""
    <div class="clip-frame{'  clip-frame-mid' if slide_idx != 1 else ''} clip-frame-missing">
      <div class="clip-missing-badge">⚠️ CLIP INDISPONÍVEL<br><small>Todos os tiers falharam.<br>Adicione manualmente:<br>resources/clips/{slot}.mp4</small></div>
    </div>"""

        if slide_idx == 1:
            # Cover slide
            cover_img = (media_paths or {}).get("cover", "")
            bg_style = (
                f'style="background-image:url(\'{cover_img}\');background-size:cover;'
                f'background-position:center top;filter:brightness(0.52) contrast(1.1);"'
                if cover_img else ""
            )
            if niche == "opc":
                tag_text = esc(content.get("tag", "Oak Park Construction"))
                headline = esc(content.get("headline", ""))
                subhead  = esc(content.get("subhead", ""))
                html_body = f"""
<div class="slide slide-cover motion-slide opc-cover {layout_class}">
  <div class="kb-bg" {bg_style}></div>
  {clip_block}
  {overlay_block}
  <div class="slide-content">
    <div class="tag">{tag_text}</div>
    <div class="cover-hl">{headline}</div>
    <div class="cover-en">{subhead}</div>
    <div class="swipe">SWIPE &#8594;</div>
  </div>
</div>"""
            else:
                cover_pt = esc(content.get("cover_pt", "TÍTULO"))
                cover_en = esc(content.get("cover_en", ""))
                cover_accent = esc(content.get("cover_accent", ""))
                raw_cover = content.get("cover_pt", "")
                if cover_accent and cover_accent in raw_cover:
                    cover_hl = cover_pt.replace(cover_accent,
                        f'<span class="accent">{cover_accent}</span>', 1)
                else:
                    cover_hl = cover_pt
                cover_date = esc(content.get("cover_date", ""))
                html_body = f"""
<div class="slide slide-cover motion-slide {layout_class}">
  <div class="kb-bg" {bg_style}></div>
  {clip_block}
  {overlay_block}
  <div class="slide-content">
    <div class="tag">Quem decidiu isso?</div>
    <div class="cover-date">{cover_date}</div>
    <div class="cover-hl">{cover_hl}</div>
    <div class="cover-en">{cover_en}</div>
    <div class="swipe">SWIPE &#8594;</div>
  </div>
</div>"""
        else:
            # Middle slide — slide_data and layout_hint already resolved above the clip block.
            if niche == "opc":
                h_pt = esc(slide_data.get("heading", slide_data.get("heading_pt", "")))
                h_en = ""
            else:
                h_pt = esc(slide_data.get("heading_pt", ""))
                h_en = esc(slide_data.get("heading_en", ""))
            slide_img = (media_paths or {}).get("slides", {}).get(slide_idx, "")
            bg_style = (
                f'style="background-image:url(\'{slide_img}\');background-size:cover;'
                f'background-position:center top;opacity:0.3;"' if slide_img else ""
            )
            html_body = f"""
<div class="slide motion-slide {layout_class}">
  <div class="kb-bg" {bg_style}></div>
  {clip_block}
  {overlay_block}
  <div class="slide-content">
    <div class="slide-hl">{h_pt}</div>
    {'<div class="slide-en">' + h_en + '</div>' if h_en else ''}
    <div class="swipe">SWIPE &#8594;</div>
  </div>
</div>"""

        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;1,9..144,700&family=Inter:wght@400;500;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
{css}
</style>
</head>
<body style="margin:0;padding:0;background:#0E0D0B;">
{html_body}
</body>
</html>"""

        fname = f"motion_slide_{slide_idx}.html"
        out_path = Path(work_dir) / fname
        out_path.write_text(html, encoding="utf-8")
        results.append((slide_idx, str(out_path)))
        clip_info = f"clip: {os.path.basename(clip_path)}" if clip_path else "no clip — static bg only"
        print(f"  Motion HTML: {fname} ({clip_info})")

    return results


def _brazil_motion_css():
    """CSS for per-slide motion HTML files — static background + clip frame styling."""
    return """
*{box-sizing:border-box;margin:0;padding:0}
:root{{--ob:#0E0D0B;--pa:#F2ECE0;--ca:#C9A84C;--gr:#7A7267;--W:1080px;--H:1350px;--P:{SLIDE_INSET_PX}px}}
body{background:var(--ob);overflow:hidden}
.slide{width:var(--W);height:var(--H);background:var(--ob);color:var(--pa);position:relative;overflow:hidden;font-family:'Inter',sans-serif}
.kb-bg{position:absolute;inset:0;background-size:cover;background-position:center top;}
.slide-content{position:relative;z-index:2;padding:var(--P);height:100%;display:flex;flex-direction:column;}
.tag{font-family:'JetBrains Mono',monospace;font-size:26px;color:var(--gr);letter-spacing:.06em;text-transform:uppercase;margin-bottom:28px}
.accent{color:var(--ca)}
.cover-date{font-family:'JetBrains Mono',monospace;font-size:24px;color:var(--gr);margin-bottom:40px}
.cover-hl{font-family:'Fraunces',serif;font-weight:700;font-size:88px;line-height:1.0;text-transform:uppercase;margin-bottom:20px;text-shadow:0 2px 20px rgba(0,0,0,.8);}
.cover-en{font-family:'Inter',sans-serif;font-style:italic;font-size:30px;color:var(--gr)}
.slide-hl{font-family:'Fraunces',serif;font-weight:700;font-size:68px;line-height:1.1;text-transform:uppercase;margin-bottom:12px;text-shadow:0 2px 20px rgba(0,0,0,.8);}
.slide-en{font-family:'Inter',sans-serif;font-style:italic;font-size:26px;color:var(--gr);margin-bottom:28px}
.swipe{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);right:var(--P)}
/* CLIP FRAME — Layout A (default): framed sticker 260×340, top-right. z-index:1 < .slide-content z-index:2. Text always wins. */
.clip-frame{position:absolute;top:120px;right:var(--P);width:260px;height:340px;
            z-index:1;border:2px solid var(--ca);background:#000;overflow:hidden;
            border-radius:14px;
            box-shadow:0 4px 18px rgba(0,0,0,.55),0 12px 48px rgba(0,0,0,.45),0 0 0 1px rgba(203,204,16,.18);}
.clip-frame-mid{top:auto;bottom:200px;}
/* Layout B: landscape clip 380×220, bottom-left — place/event/institution. */
.clip-layout-b{position:absolute;bottom:200px;left:var(--P);width:380px;height:220px;
               z-index:1;border:2px solid var(--ca);background:#000;overflow:hidden;
               border-radius:14px;
               box-shadow:0 4px 18px rgba(0,0,0,.55),0 12px 48px rgba(0,0,0,.45);}
/* Layout D: full-bleed clip + overlay. Clip at z:1, overlay at z:2, .slide-content bumped to z:3. */
.clip-layout-d{position:absolute;inset:0;z-index:1;}
.clip-layout-d .clip-video{width:100%;height:100%;object-fit:cover;display:block;}
.layout-d-overlay{position:absolute;inset:0;z-index:2;background:rgba(14,13,11,0.65);}
.slide-layout-d .slide-content{z-index:3;}
.clip-stamp{font-family:'JetBrains Mono',monospace;font-size:18px;color:var(--ca);
            background:var(--ob);padding:6px 12px;position:absolute;top:-1px;right:-1px;
            border:1px solid var(--ca);z-index:3;letter-spacing:.05em;border-radius:0 14px 0 6px;}
.clip-video{width:100%;height:100%;object-fit:cover;display:block;}
.clip-frame-missing{border:2px dashed #ff4444;background:#1a0000;border-radius:14px;}
.clip-missing-badge{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  text-align:center;color:#ff6666;font-family:'JetBrains Mono',monospace;font-size:15px;
  line-height:1.5;padding:12px;background:rgba(0,0,0,.7);}
.clip-missing-badge small{font-size:12px;color:#ff9999;}
"""


def _build_verdade_html(content, slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    """FORMAT-024 Verdade Pela Metade — 7-slide debunk carousel (PT-BR, dark brand)."""
    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    mode        = content.get("_mode", "mode_b")
    cover_pt    = esc(content.get("cover_pt", "VERDADE PELA METADE"))
    cover_en    = esc(content.get("cover_en", "Half-Truth"))
    cover_accent = esc(content.get("cover_accent", ""))
    cover_claim = esc(content.get("cover_claim", ""))
    cover_date  = esc(content.get("cover_date", ""))

    raw_cover = content.get("cover_pt", "")
    cover_hl = cover_pt.replace(cover_accent, f'<em>{cover_accent}</em>', 1) if cover_accent and cover_accent in raw_cover else cover_pt

    cover_img = (media_paths or {}).get("cover", "")
    if cover_img:
        cover_sticker_el = f'<div class="sticker-slot"><img src="{cover_img}" alt="cover portrait"></div>'
        cover_sticker_class = "cover-with-sticker"
        cover_bg_el = (
            f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
            f'<div class="halftone"></div>'
        )
    else:
        person = content.get("person", {})
        pname = esc(person.get("name", ""))
        pini = "".join(w[0].upper() for w in pname.split() if w)[:2] if pname else ""
        cover_sticker_el = (
            f'<div class="sticker-slot sticker-initials">'
            f'<div class="bio-initials">{pini}</div>'
            f'<div class="bio-init-name">{pname}</div>'
            f'</div>'
        ) if pini else ""
        cover_sticker_class = "cover-with-sticker" if pini else ""
        cover_bg_el = ""

    # Slide 3 mode-branch content
    mode_h_pt = esc(content.get("slide_mode_heading_pt", "Quem Realmente Decidiu" if mode == "mode_a" else "Número Real vs O Que Disseram"))
    mode_h_en = esc(content.get("slide_mode_heading_en", "Who Actually Decided" if mode == "mode_a" else "Real Number vs What They Said"))
    mode_content = content.get("slide_mode_content", {})
    if mode == "mode_a":
        mode_body_html = f"""
  <div class="fact-row"><span class="fact-label">Responsável</span><span class="fact-val">{esc(mode_content.get("responsible_party", ""))}</span></div>
  <div class="fact-row"><span class="fact-label">Decisão</span><span class="fact-val">{esc(mode_content.get("decision_name", ""))}</span></div>
  <div class="fact-row"><span class="fact-label">Ano</span><span class="fact-val">{esc(str(mode_content.get("year", "")))}</span></div>
  <div class="source-line">{esc(mode_content.get("source_url", ""))}</div>"""
    else:
        mode_body_html = f"""
  <div class="compare-row viral"><span class="compare-label">Viral</span><span class="compare-val">{esc(mode_content.get("original_stat", ""))}</span></div>
  <div class="compare-row real"><span class="compare-label">Real</span><span class="compare-val">{esc(mode_content.get("real_stat", ""))}</span></div>
  <div class="compare-context">{esc(mode_content.get("context", ""))}</div>"""

    fontes_html = "".join(
        f'<div class="fonte-row">{esc(f)}</div>'
        for f in content.get("fontes", [])
    )
    sources_html = "".join(
        f'<div class="src-row"><span class="src-num">{i:02d}</span><span>{esc(s)}</span></div>'
        for i, s in enumerate(content.get("sources", []), 1)
    )

    slides_html = f"""
<div class="slide slide-cover slide-motion {cover_sticker_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  {cover_bg_el}
  {cover_sticker_el}
  <div class="tag">Verdade Pela Metade</div>
  <div class="cover-date">{cover_date}</div>
  <div class="cover-hl">{cover_hl}</div>
  <div class="cover-claim">"{cover_claim}"</div>
  <div class="cover-en">{cover_en}</div>
  <div class="swipe">SEGUE O FIO &#8594;</div>
  <div class="footer-handle">{handle}</div>
</div>

<div class="slide slide-o-que-diz">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="slide-tag">O QUE DIZ O BOATO</div>
  <div class="slide-en">What the rumor claims</div>
  <div class="slide-body">{esc(content.get("slide_o_que_diz", ""))}</div>
  <div class="footer-handle">{handle}</div>
</div>

<div class="slide slide-mode-branch slide-motion">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="slide-tag">{mode_h_pt}</div>
  <div class="slide-en">{mode_h_en}</div>
  <div class="mode-content">{mode_body_html}</div>
  <div class="footer-handle">{handle}</div>
</div>

<div class="slide slide-contexto">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="slide-tag">CONTEXTO</div>
  <div class="slide-en">What the narrative leaves out</div>
  <div class="slide-body">{esc(content.get("contexto", ""))}</div>
  <div class="footer-handle">{handle}</div>
</div>

<div class="slide slide-fontes slide-motion">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="slide-tag">FONTES VERIFICADAS</div>
  <div class="slide-en">Verified outlets</div>
  <div class="fontes-list">{fontes_html}</div>
  <div class="footer-handle">{handle}</div>
</div>

<div class="slide slide-conclusao">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="slide-tag">CONCLUSÃO</div>
  <div class="slide-en">Verdict</div>
  <div class="conclusao-text">{esc(content.get("conclusao", ""))}</div>
  <div class="footer-handle">{handle}</div>
</div>

<div class="slide slide-sources">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="slide-tag">FONTES</div>
  <div class="sources-list">{sources_html}</div>
  <div class="cta">{esc(content.get("cta_pt", "Salva e compartilha."))}</div>
  <div class="footer-handle">{handle}</div>
</div>
"""

    css = """
<style>
:root {
  --bg: #0d0d0d;
  --accent: #FFE500;
  --text: #f0f0f0;
  --muted: #888;
  --viral-red: #ff3b30;
  --real-green: #34c759;
  --slide-w: 1080px;
  --slide-h: 1350px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #333; font-family: 'Barlow Condensed', 'Impact', sans-serif; }
.slide {
  width: var(--slide-w); height: var(--slide-h);
  background: var(--bg); color: var(--text);
  position: relative; overflow: hidden;
  display: flex; flex-direction: column; justify-content: flex-end;
  padding: 60px 64px; gap: 0;
}
.corner { position: absolute; width: 28px; height: 28px; border-color: var(--accent); border-style: solid; }
.corner.tl { top: 32px; left: 32px; border-width: 3px 0 0 3px; }
.corner.tr { top: 32px; right: 32px; border-width: 3px 3px 0 0; }
.corner.bl { bottom: 32px; left: 32px; border-width: 0 0 3px 3px; }
.corner.br { bottom: 32px; right: 32px; border-width: 0 3px 3px 0; }

/* Cover */
.bg-photo { position: absolute; inset: 0; background-size: cover; background-position: center; filter: grayscale(1) contrast(1.1); z-index: 0; }
.halftone { position: absolute; inset: 0; background: repeating-radial-gradient(circle, rgba(0,0,0,.45) 0 2px, transparent 2px 6px); z-index: 1; }
.sticker-slot { position: absolute; right: 0; bottom: 0; width: 480px; height: 700px; z-index: 2; overflow: hidden; }
.sticker-slot img { width: 100%; height: 100%; object-fit: cover; filter: grayscale(1); }
.sticker-initials { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; padding-bottom: 80px; }
.bio-initials { width: 110px; height: 130px; background: var(--muted); display: flex; align-items: center; justify-content: center; font-size: 48px; font-weight: 900; color: var(--text); }
.bio-init-name { font-size: 18px; color: var(--text); margin-top: 8px; text-align: center; }
.tag, .slide-tag { font-size: 20px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase; color: var(--accent); margin-bottom: 12px; position: relative; z-index: 3; }
.cover-date { font-size: 16px; color: var(--muted); margin-bottom: 16px; z-index: 3; position: relative; }
.cover-hl { font-size: 88px; font-weight: 900; line-height: 0.92; text-transform: uppercase; z-index: 3; position: relative; margin-bottom: 20px; }
.cover-hl em { font-style: normal; color: var(--accent); }
.cover-claim { font-size: 32px; font-weight: 600; font-style: italic; color: #ddd; z-index: 3; position: relative; margin-bottom: 12px; line-height: 1.3; }
.cover-en { font-size: 18px; color: var(--muted); z-index: 3; position: relative; margin-bottom: 24px; }
.swipe { font-size: 22px; font-weight: 700; letter-spacing: 2px; color: var(--accent); z-index: 3; position: relative; }
.footer-handle { font-size: 18px; color: var(--muted); margin-top: 16px; z-index: 3; position: relative; }

/* Body slides */
.slide-en { font-size: 18px; color: var(--muted); margin-bottom: 28px; }
.slide-body { font-size: 36px; font-weight: 500; line-height: 1.45; color: var(--text); }

/* Mode branch */
.mode-content { display: flex; flex-direction: column; gap: 20px; }
.fact-row { display: flex; flex-direction: column; gap: 4px; border-left: 4px solid var(--accent); padding-left: 20px; }
.fact-label { font-size: 16px; font-weight: 700; letter-spacing: 2px; color: var(--muted); text-transform: uppercase; }
.fact-val { font-size: 38px; font-weight: 800; }
.source-line { font-size: 18px; color: var(--muted); margin-top: 8px; }
.compare-row { display: flex; align-items: center; gap: 24px; padding: 20px; border-radius: 4px; }
.compare-row.viral { background: rgba(255,59,48,.15); border: 1px solid var(--viral-red); }
.compare-row.real { background: rgba(52,199,89,.15); border: 1px solid var(--real-green); }
.compare-label { font-size: 18px; font-weight: 900; letter-spacing: 2px; text-transform: uppercase; min-width: 60px; }
.compare-row.viral .compare-label { color: var(--viral-red); }
.compare-row.real .compare-label { color: var(--real-green); }
.compare-val { font-size: 34px; font-weight: 700; }
.compare-context { font-size: 28px; color: #bbb; line-height: 1.4; margin-top: 12px; }

/* Fontes */
.fontes-list { display: flex; flex-direction: column; gap: 20px; }
.fonte-row { font-size: 30px; font-weight: 500; border-left: 4px solid var(--accent); padding-left: 20px; }

/* Conclusão */
.conclusao-text { font-size: 44px; font-weight: 800; line-height: 1.25; color: var(--text); }

/* Sources */
.sources-list { display: flex; flex-direction: column; gap: 14px; margin-bottom: 24px; }
.src-row { font-size: 22px; display: flex; gap: 16px; align-items: flex-start; }
.src-num { font-weight: 900; color: var(--accent); min-width: 32px; }
.cta { font-size: 28px; font-weight: 700; color: var(--accent); letter-spacing: 1px; }
</style>
"""

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>Verdade Pela Metade — {esc(slug)}</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,400;0,700;0,900;1,700&display=swap" rel="stylesheet">
{css}
</head><body>
{slides_html}
</body></html>"""

    out = Path(work_dir) / "cover.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


def build_html(content, niche, topic_slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    if niche == "opc":
        # Phase 4: when the planner has attached a slide plan, render via the
        # smart slide-by-slide path. Falls back to the legacy tip builder if
        # the plan is missing/blocked. Feature flag lives in main.py
        # (OPC_SLIDE_PLANNER_ENABLED) — by the time we reach build_html the
        # decision to attach _slide_plan has already been made.
        slide_plan = (content or {}).get("_slide_plan") or {}
        if slide_plan.get("status") == "passed" and len(slide_plan.get("slides", [])) == 5:
            return build_opc_from_slide_plan(content, topic_slug, work_dir, media_paths=media_paths)
        template_key = content.get("_template_key", "tip")
        if template_key == "progress":
            return _build_opc_progress_html(content, topic_slug, work_dir, media_paths=media_paths)
        if template_key == "illustrated":
            return _build_opc_illustrated_html(content, topic_slug, work_dir, media_paths=media_paths)
        if template_key == "cutout":
            return _build_opc_cutout_html(content, topic_slug, work_dir, media_paths=media_paths)
        return _build_opc_html(content, topic_slug, work_dir, media_paths=media_paths)
    if niche in ("brazil", "usa"):
        template_key = content.get("_template_key")
        if template_key == "who-is":
            return _build_who_is_html(content, topic_slug, work_dir, handle=handle, media_paths=media_paths)
        if template_key == "the-case":
            return _build_the_case_html(content, topic_slug, work_dir, handle=handle, media_paths=media_paths)
        if template_key in ("illustrated", "cutout"):
            return _build_news_shared_template_html(
                content, topic_slug, work_dir, template_key, handle=handle, media_paths=media_paths, niche=niche
            )
        if template_key == "verdade-pela-metade":
            return _build_verdade_html(content, topic_slug, work_dir, handle=handle, media_paths=media_paths)
        return _build_brazil_html(content, topic_slug, work_dir, handle=handle, media_paths=media_paths)
    return None


def _cap34(text: str) -> str:
    """Hard-cap list item titles at 34 chars (reviewer limit) at word boundary."""
    if len(text) <= 34:
        return text
    t = text[:34]
    return t[:t.rfind(" ")].rstrip() if " " in t else t


def _opc_tip_context_slot(slide_num, fallback_label, opc_slides_meta, media_paths):
    """Picks the right context image for an OPC tip slide.
    SH-156: when no image is fetched, OMIT the slot entirely instead of rendering a
    visible placeholder label. Internal labels like 'STAT CONTEXT IMAGE' must NEVER
    reach the rendered HTML the client/Priscila reviews. The HTML completeness gate
    (verify_html_completeness) treats their presence as a hard block.
    Module-level so the slide-component renderers + opc_slide_planner can reuse it."""
    slide_meta_idx = max(0, slide_num - 2)
    slide_meta = opc_slides_meta[slide_meta_idx] if slide_meta_idx < len(opc_slides_meta) else {}
    visual_hint = str(slide_meta.get("visual_hint", "context-image")).strip().lower()
    query = str(slide_meta.get("context_image_query", "")).strip()
    query_attr = query.replace('"', "&quot;")
    img_path = ((media_paths or {}).get("slides", {}) or {}).get(slide_num, "")
    # If a real image was fetched, always show it
    if img_path:
        return (
            f'<div class="context-img-slot" data-query="{query_attr}">'
            f'<img src="{img_path}" alt="">'
            '</div>'
        )
    # No image: omit the visual slot but keep data-query as a hidden metadata span
    # so SH-157 can still validate the query string (empty slot ≠ missing metadata).
    print(f"  [SH-156] slide{slide_num}: no context image — omitting slot (was: {fallback_label})")
    return (
        f'<span class="context-img-metadata" data-query="{query_attr}" style="display:none"></span>'
        f'<!-- context-img-slot omitted (slide {slide_num} — no image) -->'
    )


def render_opc_tip_cover(content, v_class, *, hl_html, bg_photo_el):
    """OPC tip — slide 1 (cover). Public so opc_slide_planner / build_opc_from_slide_plan
    can call it independently of the full 5-slide tip carousel."""
    return (
        f'<div class="slide slide-cover {v_class}">\n'
        f'  {bg_photo_el}\n'
        f'  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>\n'
        f'  <div class="tag">Tip of the Week · Oak Park Construction</div>\n'
        f'  <div class="headline">{hl_html}</div>\n'
        f'  <div class="body-text">{content["subhead"]}</div>\n'
        f'  <div class="arrow">SWIPE &#8594;</div>\n'
        f'  <div class="slide-logo">Oak Park Construction · CBC1263425</div>\n'
        f'</div>'
    )


def render_opc_tip_stat(content, v_class, *, context_slot):
    """OPC tip — slide 2 (THE REAL NUMBER stat slide)."""
    return (
        f'<div class="slide slide-stat {v_class}">\n'
        f'  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>\n'
        f'  <div class="tag">The Real Number</div>\n'
        f'  <div class="stat-big">{content.get("slide2_stat", "—")}</div>\n'
        f'  <div class="stat-label">{content.get("slide2_label", "")}</div>\n'
        f'  {context_slot}\n'
        f'  <div class="project-note">What you are seeing here: cost, scope, and site conditions can change this number.</div>\n'
        f'  <div class="arrow">SWIPE &#8594;</div>\n'
        f'  <div class="slide-logo">Oak Park Construction · CBC1263425</div>\n'
        f'</div>'
    )


def _opc_story_slide3_headline(content):
    """Short slide-3 story title for the fixed legacy list layout."""
    items = content.get("slide3_items", []) if isinstance(content.get("slide3_items", []), list) else []
    item_text = " ".join(
        f"{(i or {}).get('title', '')} {(i or {}).get('sub', '')}"
        for i in items if isinstance(i, dict)
    ).lower()
    pair = content.get("_comparison_pair") if isinstance(content.get("_comparison_pair"), dict) else {}
    has_comparison = bool(pair.get("left") and pair.get("right"))
    if "$" in item_text or "cost" in item_text or "price" in item_text or "budget" in item_text:
        return "COST SPLIT" if has_comparison else "COST DRIVERS"
    if has_comparison:
        return "TRADEOFFS"
    if "risk" in item_text or "red flag" in item_text or "mistake" in item_text:
        return "THE RISKS"
    return "THE CAUSE"


def _opc_story_slide4_headline(content):
    """Replace generic warning labels with a specific action headline."""
    raw = str(content.get("slide4_headline") or "").strip() or "COMPARE TOTAL COST"
    generic = {
        "AVOID THIS", "WATCH OUT", "RED FLAG", "THE PRO MOVE",
        "PRO TIP", "EXPERT ADVICE", "THE FIX",
    }
    if raw.upper().strip(".") not in generic:
        return raw
    body = str(content.get("slide4_body") or "").lower()
    if "long-term" in body or "total cost" in body or "upfront" in body:
        return "COMPARE TOTAL COST"
    if "repair" in body or "maintenance" in body:
        return "PLAN REPAIRS EARLY"
    if "drain" in body or "slope" in body or "water" in body:
        return "CHECK DRAINAGE FIRST"
    if "permit" in body or "code" in body:
        return "CHECK PERMITS FIRST"
    return "COMPARE TOTAL COST"


def _opc_story_cover_headline(content, slug):
    """Keep the OPC cover hook from degrading into a neutral topic label."""
    raw = str(content.get("headline") or "").strip()
    hl = raw or re.sub(r"[^a-zA-Z0-9 ]", " ", slug or "").upper().strip() or "THE GUIDE"
    if re.search(r"[$%]|\d", hl) or re.search(r"\b(COST|RISK|TRAP|MISTAKE|LOSS|FAIL|OVERPAY)\b", hl, re.I):
        return hl.upper()
    stat = str(content.get("slide2_stat") or "").strip().upper()
    m = re.search(r"(?:UP TO\s+)?[$]?\d[\d,]*(?:K|M)?(?:\s*[-–]\s*[$]?\d[\d,]*(?:K|M)?)?%?", stat)
    token = (m.group(0) if m else "").strip()
    if token:
        token = re.sub(r"^UP TO\s+", "", token).strip()
        return f"{token} DECISION"
    return hl.upper()


def render_opc_tip_list(content, v_class, *, items_html, context_slot):
    """OPC tip — slide 3 (TEACH / why-it-happens checklist slide)."""
    _s3_hl = _opc_story_slide3_headline(content)
    return (
        f'<div class="slide slide-list {v_class}">\n'
        f'  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>\n'
        f'  <div class="tag">Why It Happens</div>\n'
        f'  <div class="headline" style="font-size:96px; margin-bottom:36px;">{_s3_hl}<span class="accent">.</span></div>\n'
        f'  {context_slot}\n'
        f'  <div class="list">\n'
        f'{items_html}  </div>\n'
        f'  <div class="arrow">SWIPE &#8594;</div>\n'
        f'  <div class="slide-logo">Oak Park Construction · CBC1263425</div>\n'
        f'</div>'
    )


def render_opc_tip_explainer(content, v_class, *, s4_hl, s4_accent, s4_accent_style, context_slot):
    """OPC tip — slide 4 (THE PRO MOVE explainer slide)."""
    s4_with_accent = s4_hl.replace(s4_accent, f'<span style="color:{s4_accent_style};">{s4_accent}</span>')
    return (
        f'<div class="slide slide-tip {v_class}">\n'
        f'  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>\n'
        f'  <div class="tag">Pro Tip</div>\n'
        f'  <div class="tip-label"><span class="tip-arrow">&#9658;</span> The Pro Move</div>\n'
        f'  <div class="tip-big">{s4_with_accent}</div>\n'
        f'  {context_slot}\n'
        f'  <div class="tip-explain">{content.get("slide4_body", "")}</div>\n'
        f'  <div class="arrow">SWIPE &#8594;</div>\n'
        f'  <div class="slide-logo">Oak Park Construction · CBC1263425</div>\n'
        f'</div>'
    )


def render_opc_tip_sources(content, v_class, *, sources_html, src_accent_style, sources_bg_el, cta):
    """OPC tip — slide 5 (sources / CTA closing slide)."""
    return (
        f'<div class="slide slide-sources {v_class}">\n'
        f'  {sources_bg_el}\n'
        f'  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>\n'
        f'  <div class="tag">Sources</div>\n'
        f'  <div class="src-head">WHERE THIS<br>COMES <span style="color:{src_accent_style};">FROM.</span></div>\n'
        f'  <div class="src-list">\n'
        f'{sources_html}  </div>\n'
        f'  <div class="save-cta">{cta}</div>\n'
        f'  <div class="footer">\n'
        f'    <span class="handle">@oakparkconstruction</span>\n'
        f'    <span class="license">LIC · CBC1263425</span>\n'
        f'  </div>\n'
        f'</div>'
    )


# =============================================================================
# Phase 6 — Standalone OPC slide-component renderers (added 2026-05-06)
# =============================================================================
# These port the approved standalone HTML designs in docs/templates/ into
# Python builders. Each takes (content_or_slide_dict, v_class, **kwargs) and
# returns a single <div class="slide ..."> block. Per-template CSS lives in
# opc_standalones.css and is auto-loaded by build_opc_from_slide_plan.
#
# Field-resolution rule: each builder accepts EITHER a top-level content dict
# (legacy) OR a slide-specific subdict at content[<template_id>]. This keeps
# the Haiku content prompt simple — it can emit one nested object per planned
# slide instead of flattening everything.

def _opc_field(content, slide_id, key, default=""):
    """Look up a field for a standalone slide. Tries:
      content['<slide_id>'][<key>]  → preferred (planner-aware content shape)
      content[<key>]                → legacy / shared with tip
      <default>                     → safe default so missing fields never crash
    Falsy "" is treated as missing so callers can still get the default.
    """
    sub = content.get(slide_id) or {}
    if isinstance(sub, dict):
        v = sub.get(key)
        if v not in (None, ""):
            return v
    v = content.get(key)
    if v not in (None, ""):
        return v
    return default


def _opc_license_code(content):
    """OPC license stays static unless content overrides it. Single source of truth."""
    return content.get("license") or "CBC1263425"


def _opc_swipe_label(content, default="Swipe →"):
    """Allow the cta field to override the swipe arrow label on standalone slides."""
    return content.get("cta") or default


def _opc_image_or_placeholder(img_path, placeholder_html):
    """Return an <img src="..."> if a real image is available, else the placeholder block."""
    if img_path:
        return f'<img src="{img_path}" alt="">'
    return placeholder_html


def render_opc_material_profile(content, v_class, *, slide_num, media_paths=None):
    """opc_material_profile — 6-field material/product/service profile card.
    Text-only by design (image_need='none')."""
    SID = "opc_material_profile"
    label    = _opc_field(content, SID, "label",            "MATERIAL PROFILE · OPC")
    hl_main  = _opc_field(content, SID, "headline_main",    _opc_field(content, SID, "headline", "MATERIAL")) or "MATERIAL"
    hl_em    = _opc_field(content, SID, "headline_italic",  "the South Florida pick")
    f_best   = _opc_field(content, SID, "best_for",         "—")
    f_not    = _opc_field(content, SID, "not_ideal",        "—")
    f_dur    = _opc_field(content, SID, "durability",       "—")
    f_inst   = _opc_field(content, SID, "install_notes",    "—")
    f_cost   = _opc_field(content, SID, "cost_range",       "—")
    f_style  = _opc_field(content, SID, "style_fit",        "—")
    factors  = _opc_field(content, SID, "decision_factors", []) or []
    if not isinstance(factors, list):
        factors = [str(factors)]
    pills_html = "".join(
        f'<div class="profile-pill">{p}</div>' for p in factors[:8]
    ) or '<div class="profile-pill">DECIDE</div>'

    return (
        f'<div class="slide opc-mp {v_class}">\n'
        f'  <div class="profile-label">{label}</div>\n'
        f'  <div class="profile-headline">{hl_main}<br><em class="profile-name">{hl_em}</em></div>\n'
        f'  <div class="profile-rule"><div class="profile-rule-line"></div><div class="profile-rule-gem"></div><div class="profile-rule-line"></div></div>\n'
        f'  <div class="profile-grid">\n'
        f'    <div class="profile-field"><div class="profile-field-label">BEST FOR</div><div class="profile-field-value">{f_best}</div></div>\n'
        f'    <div class="profile-field"><div class="profile-field-label">NOT IDEAL FOR</div><div class="profile-field-value">{f_not}</div></div>\n'
        f'    <div class="profile-field"><div class="profile-field-label">DURABILITY</div><div class="profile-field-value">{f_dur}</div></div>\n'
        f'    <div class="profile-field"><div class="profile-field-label">INSTALL NOTES</div><div class="profile-field-value">{f_inst}</div></div>\n'
        f'    <div class="profile-field"><div class="profile-field-label">COST RANGE</div><div class="profile-field-value">{f_cost}</div></div>\n'
        f'    <div class="profile-field"><div class="profile-field-label">STYLE FIT</div><div class="profile-field-value">{f_style}</div></div>\n'
        f'  </div>\n'
        f'  <div class="profile-tags-wrap">\n'
        f'    <div class="profile-tags-label">&#9670; DECISION FACTORS</div>\n'
        f'    <div class="profile-tags">{pills_html}</div>\n'
        f'  </div>\n'
        f'  <div class="license">OPC · LIC {_opc_license_code(content)}</div>\n'
        f'</div>'
    )


def render_opc_four_card_grid(content, v_class, *, slide_num, media_paths=None):
    """opc_four_card_grid — 4-card comparison grid (compare options/products/decisions)."""
    SID = "opc_four_card_grid"
    eyebrow  = _opc_field(content, SID, "eyebrow",         "Project breakdown · OPC")
    hl_main  = _opc_field(content, SID, "headline_main",   "Four")
    hl_em    = _opc_field(content, SID, "headline_italic", "options.")
    subhead  = _opc_field(content, SID, "subhead",         _opc_field(content, SID, "subhead", ""))
    badges   = _opc_field(content, SID, "badges",          []) or []
    titles   = _opc_field(content, SID, "card_titles",     []) or []
    copies   = _opc_field(content, SID, "card_copies",     []) or []

    # Pad lists to length 4 with sensible defaults so the grid always fills.
    while len(badges) < 4: badges.append("OPTION")
    while len(titles) < 4: titles.append(f"Option {len(titles)+1}")
    while len(copies) < 4: copies.append("—")

    # Image lookup: card image keys can live in media_paths["cards"][1..4] or fall back to slide image.
    card_imgs = ((media_paths or {}).get("cards") or {})
    cards_html = ""
    for i in range(4):
        card_img = card_imgs.get(i+1) or card_imgs.get(str(i+1)) or ""
        if card_img:
            media_block = f'<div class="media"><img src="{card_img}" alt="card {i+1}"></div>'
        else:
            media_block = f'<div class="media"><div class="placeholder-label">{badges[i]}</div></div>'
        cards_html += (
            f'    <div class="card">\n'
            f'      <div class="badge">{badges[i]}</div>\n'
            f'      {media_block}\n'
            f'      <div class="num">{i+1:02d}</div>\n'
            f'      <div class="card-title">{titles[i]}</div>\n'
            f'      <div class="card-copy">{copies[i]}</div>\n'
            f'    </div>\n'
        )

    return (
        f'<div class="slide opc-fcg {v_class}">\n'
        f'  <span class="corner c1"></span><span class="corner c2"></span><span class="corner c3"></span><span class="corner c4"></span>\n'
        f'  <div class="eyebrow">{eyebrow}</div>\n'
        f'  <div class="headline">{hl_main} <em>{hl_em}</em></div>\n'
        f'  <div class="subhead">{subhead}</div>\n'
        f'  <div class="grid">\n'
        f'{cards_html}'
        f'  </div>\n'
        f'  <div class="foot"><span>Oak Park Construction · {_opc_license_code(content)}</span><span class="swipe">{_opc_swipe_label(content)}</span></div>\n'
        f'</div>'
    )


def render_opc_item_spotlight(content, v_class, *, slide_num, media_paths=None):
    """opc_item_spotlight — single item teaching slide with 4 fact bullets."""
    SID = "opc_item_spotlight"
    tag       = _opc_field(content, SID, "tag",             "Item spotlight · OPC")
    category  = _opc_field(content, SID, "category",        "PRODUCT · MATERIAL · TECHNIQUE")
    hl_main   = _opc_field(content, SID, "headline_main",   "THIS DETAIL")
    hl_em     = _opc_field(content, SID, "headline_italic", "Florida-built only")
    sub       = _opc_field(content, SID, "subhead",         "")
    fact1_t   = _opc_field(content, SID, "fact_1_title", "Fact one")
    fact1_d   = _opc_field(content, SID, "fact_1_desc",  "—")
    fact2_t   = _opc_field(content, SID, "fact_2_title", "Fact two")
    fact2_d   = _opc_field(content, SID, "fact_2_desc",  "—")
    fact3_t   = _opc_field(content, SID, "fact_3_title", "Fact three")
    fact3_d   = _opc_field(content, SID, "fact_3_desc",  "—")
    fact4_t   = _opc_field(content, SID, "fact_4_title", "Fact four")
    fact4_d   = _opc_field(content, SID, "fact_4_desc",  "—")

    img_path = ((media_paths or {}).get("slides", {}) or {}).get(slide_num, "") \
              or (media_paths or {}).get("cover", "")
    if img_path:
        thumb = f'<div class="thumb"><img src="{img_path}" alt="{category}"></div>'
    else:
        thumb = '<div class="thumb"><div class="placeholder-label">CLOSE-UP<br>OF ITEM</div></div>'

    return (
        f'<div class="slide opc-is {v_class}">\n'
        f'  <div class="bg-placeholder"></div>\n'
        f'  <div class="shade"></div>\n'
        f'  <span class="corner tl"></span><span class="corner tr"></span><span class="corner bl"></span><span class="corner br"></span>\n'
        f'  <div class="content">\n'
        f'    <div class="tag">{tag}</div>\n'
        f'    <div class="category">{category}</div>\n'
        f'    <div class="headline">{hl_main} <em>{hl_em}</em></div>\n'
        f'    <div class="sub">{sub}</div>\n'
        f'    <div class="body">\n'
        f'      {thumb}\n'
        f'      <ul class="fact-list">\n'
        f'        <li>{fact1_t}<span>{fact1_d}</span></li>\n'
        f'        <li>{fact2_t}<span>{fact2_d}</span></li>\n'
        f'        <li>{fact3_t}<span>{fact3_d}</span></li>\n'
        f'        <li>{fact4_t}<span>{fact4_d}</span></li>\n'
        f'      </ul>\n'
        f'    </div>\n'
        f'    <div class="bottom"><span>Oak Park Construction · {_opc_license_code(content)}</span><span class="swipe">{_opc_swipe_label(content)}</span></div>\n'
        f'  </div>\n'
        f'</div>'
    )


def render_opc_statement(content, v_class, *, slide_num, media_paths=None):
    """opc_statement — mid-carousel quote slide with B&W person photo (diagonal divider)."""
    SID = "opc_statement"
    tag           = _opc_field(content, SID, "tag",            "FROM THE FIELD")
    quote_opener  = _opc_field(content, SID, "quote_opener",   "Quality is in the framing.")
    quote_body    = _opc_field(content, SID, "quote_body",     "")
    attribution   = _opc_field(content, SID, "attribution",    "MIKE · OPC FOUNDER")
    slide_label   = _opc_field(content, SID, "slide_number",   f"{slide_num} / 5")

    person_img = ((media_paths or {}).get("slides", {}) or {}).get(slide_num, "") \
                 or (media_paths or {}).get("person") \
                 or (media_paths or {}).get("cover", "")
    if person_img:
        photo_block = f'<img src="{person_img}" alt="{attribution}">'
    else:
        photo_block = (
            '<div class="sticker-placeholder">'
            'PERSON PHOTO<br>SLOT — B&amp;W FILTER'
            '</div>'
        )

    return (
        f'<div class="slide opc-st {v_class}">\n'
        f'  <div class="corner tl"></div>\n'
        f'  <div class="corner bl"></div>\n'
        f'  <div class="photo-zone sticker-slot">{photo_block}</div>\n'
        f'  <div class="text-zone">\n'
        f'    <div class="tag">{tag}</div>\n'
        f'    <div class="quote-block">\n'
        f'      <div class="quote-opener">{quote_opener}</div>\n'
        f'      <div class="quote-body">{quote_body}</div>\n'
        f'      <div class="quote-attribution"><span class="attribution-dash">&mdash;</span>{attribution}</div>\n'
        f'    </div>\n'
        f'    <div class="cover-logo"><span class="logo-top">Oak Park</span>Construction<span class="logo-license">LIC · {_opc_license_code(content)}</span></div>\n'
        f'  </div>\n'
        f'  <div class="slide-counter">{slide_label}</div>\n'
        f'</div>'
    )


def render_opc_base(content, v_class, *, slide_num, media_paths=None):
    """opc_base — clean cover with bg photo + sticker portrait + 2-line title."""
    SID = "opc_base"
    tag        = _opc_field(content, SID, "tag",             "OPC / TIP #001")
    hl_main    = _opc_field(content, SID, "headline_main",   _opc_field(content, SID, "headline", "THE TIP"))
    hl_em      = _opc_field(content, SID, "headline_italic", "what inspectors flag")
    cover_hook = _opc_field(content, SID, "cover_hook",      _opc_field(content, SID, "subhead", "")) or ""
    stamp_text = _opc_field(content, SID, "stamp_text",      "TIP · #001")
    byline     = _opc_field(content, SID, "byline",          "MIKE · <em>OPC FOUNDER</em>")
    cta        = _opc_field(content, SID, "cta",             "SAVE THE TIP &#8594;")

    bg_img = (media_paths or {}).get("cover", "")
    if bg_img:
        bg_block = f'<div class="bg-photo" style="background-image:url(\'{bg_img}\');"></div>'
    else:
        bg_block = '<div class="bg-placeholder"></div>'

    sticker_img = ((media_paths or {}).get("slides", {}) or {}).get(slide_num, "")
    if sticker_img:
        sticker_block = f'<img src="{sticker_img}" alt="OPC project detail">'
    else:
        # Initials placeholder (2 letters) — surface intent even without a photo.
        ini_text = (content.get("opc_base_initials") or "OP").upper()[:2]
        sticker_block = (
            '<div class="sticker-placeholder">'
            f'<div class="ini">{ini_text}</div>'
            '<div class="iname">OAK PARK<br>CONSTRUCTION</div>'
            '</div>'
        )

    return (
        f'<div class="slide opc-bs {v_class}">\n'
        f'  <div class="corner tl"></div><div class="corner tr"></div>\n'
        f'  <div class="corner bl"></div><div class="corner br"></div>\n'
        f'  {bg_block}\n'
        f'  <div class="halftone"></div>\n'
        f'  <div class="dark-overlay"></div>\n'
        f'  <div class="sticker-slot">{sticker_block}</div>\n'
        f'  <div class="stamp-badge">{stamp_text}</div>\n'
        f'  <div class="tag">{tag}</div>\n'
        f'  <div class="cover-hl">{hl_main} <em>{hl_em}</em></div>\n'
        f'  <div class="cover-hook">{cover_hook}</div>\n'
        f'  <div class="person-pill">{byline}</div>\n'
        f'  <div class="swipe">{cta}</div>\n'
        f'</div>'
    )


def render_opc_progress_media(content, v_class, *, slide_num, media_paths=None):
    """opc_progress_media — jobsite/progress proof slide with 920×585 media frame."""
    SID = "opc_progress_media"
    tag           = _opc_field(content, SID, "tag",             "Project Progress · Field Update")
    eyebrow       = _opc_field(content, SID, "eyebrow",         "Oak Park Construction")
    title_main    = _opc_field(content, SID, "title_main",      _opc_field(content, SID, "title", "What changed"))
    title_em      = _opc_field(content, SID, "title_italic",    "on site?")
    desc_bold     = _opc_field(content, SID, "description_bold", "")
    desc_rest     = _opc_field(content, SID, "description_rest", _opc_field(content, SID, "description", ""))
    pills         = _opc_field(content, SID, "caption_pills",    ["BEFORE", "DURING", "AFTER"]) or []
    if not isinstance(pills, list):
        pills = [str(pills)]
    pills_html = "".join(f'<div class="pill">{p}</div>' for p in pills[:5]) or '<div class="pill">PROGRESS</div>'

    img_path = ((media_paths or {}).get("slides", {}) or {}).get(slide_num, "") \
              or (media_paths or {}).get("cover", "")
    if img_path:
        media_block = f'<img src="{img_path}" alt="progress">'
    else:
        media_block = (
            '<div class="bg-placeholder"></div>'
            '<div class="fallback">'
            '<div class="play"></div>'
            'Progress video or photo<br>before / during / after'
            '</div>'
        )

    return (
        f'<div class="slide opc-pm {v_class}">\n'
        f'  <div class="noise"></div>\n'
        f'  <div class="corners"><span></span><span></span><span></span><span></span></div>\n'
        f'  <div class="tag">{tag}</div>\n'
        f'  <div class="media-frame">{media_block}</div>\n'
        f'  <div class="detail-panel">\n'
        f'    <div class="rule-line"></div>\n'
        f'    <div class="eyebrow">{eyebrow}</div>\n'
        f'    <div class="pm-title">{title_main}<em>{title_em}</em></div>\n'
        f'    <div class="description"><strong>{desc_bold}</strong> {desc_rest}</div>\n'
        f'    <div class="meta-row">\n'
        f'      <div class="pill-row">{pills_html}</div>\n'
        f'      <div class="license">OPC · {_opc_license_code(content)}</div>\n'
        f'    </div>\n'
        f'  </div>\n'
        f'</div>'
    )


# Shared SVG duotone filter block used by opc_duotone. Returned ONCE per page,
# not per-slide — build_opc_from_slide_plan injects it inside the <body> only
# when at least one slide uses opc_duotone.
OPC_DUOTONE_SVG_FILTER = '''<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <!-- v1 — navy shadows → soft-lime highlights. Default. -->
    <filter id="duotone-opc-v1" color-interpolation-filters="sRGB">
      <feColorMatrix type="matrix" values="0.299 0.587 0.114 0 0  0.299 0.587 0.114 0 0  0.299 0.587 0.114 0 0  0 0 0 1 0"/>
      <feComponentTransfer>
        <feFuncR type="table" tableValues="0.027 0.796"/>
        <feFuncG type="table" tableValues="0.051 0.800"/>
        <feFuncB type="table" tableValues="0.133 0.063"/>
      </feComponentTransfer>
    </filter>
    <!-- v2 — navy shadows → yellow highlights. For financial-cost / risk topics. -->
    <filter id="duotone-opc-v2" color-interpolation-filters="sRGB">
      <feColorMatrix type="matrix" values="0.299 0.587 0.114 0 0  0.299 0.587 0.114 0 0  0.299 0.587 0.114 0 0  0 0 0 1 0"/>
      <feComponentTransfer>
        <feFuncR type="table" tableValues="0.027 0.996"/>
        <feFuncG type="table" tableValues="0.051 0.898"/>
        <feFuncB type="table" tableValues="0.133 0.000"/>
      </feComponentTransfer>
    </filter>
    <!-- v3 — teal shadows → soft-lime highlights. For success / proof topics. -->
    <filter id="duotone-opc-v3" color-interpolation-filters="sRGB">
      <feColorMatrix type="matrix" values="0.299 0.587 0.114 0 0  0.299 0.587 0.114 0 0  0.299 0.587 0.114 0 0  0 0 0 1 0"/>
      <feComponentTransfer>
        <feFuncR type="table" tableValues="0.027 0.796"/>
        <feFuncG type="table" tableValues="0.302 0.835"/>
        <feFuncB type="table" tableValues="0.298 0.169"/>
      </feComponentTransfer>
    </filter>
  </defs>
</svg>'''


def render_opc_duotone(content, v_class, *, slide_num, media_paths=None):
    """opc_duotone — bold warning/red-flag opener with duotone-filtered hero photo.
    Phase 8B: variant control. content['opc_duotone']['variant'] picks the SVG
    filter (v1/v2/v3). Default v1. Inline style overrides the CSS hardcoded
    filter:url(#duotone-opc-v1) so v2/v3 actually apply.
    """
    SID = "opc_duotone"
    variant = str(_opc_field(content, SID, "variant", "v1")).strip().lower()
    if variant not in ("v1", "v2", "v3"):
        variant = "v1"
    claim_main      = _opc_field(content, SID, "claim_main",      "Watch out:")
    claim_strong    = _opc_field(content, SID, "claim_strong",    "this can cost you")
    claim_rest      = _opc_field(content, SID, "claim_rest",      "")
    claim_underline = _opc_field(content, SID, "claim_underline", "")
    claim_final     = _opc_field(content, SID, "claim_final",     "")
    quote_text      = _opc_field(content, SID, "quote_text",      "")
    attribution     = _opc_field(content, SID, "attribution",     "Mike McFolling · GC")

    bg_img = ((media_paths or {}).get("slides", {}) or {}).get(slide_num, "") \
             or (media_paths or {}).get("cover", "")
    # Inline filter override beats the CSS hardcode regardless of variant.
    filter_decl = f"filter:url(#duotone-opc-{variant});"
    if bg_img:
        photo_style = f"background-image:url('{bg_img}');{filter_decl}"
    else:
        photo_style = f"background:#33330d;{filter_decl}"

    underline_block = f'<u><strong>{claim_underline}</strong></u>' if claim_underline else ''
    final_block     = f' {claim_final}' if claim_final else ''

    return (
        f'<div class="slide opc-dt {v_class}" data-duotone-variant="{variant}">\n'
        f'  <div class="claim">{claim_main} <strong>{claim_strong}</strong> {claim_rest} {underline_block}{final_block}.</div>\n'
        f'  <div class="photo-wrap"><div class="photo" style="{photo_style}"></div></div>\n'
        f'  <div class="quote-block">\n'
        f'    <div class="quote-mark">&ldquo;</div>\n'
        f'    <div class="quote-text">{quote_text}</div>\n'
        f'  </div>\n'
        f'  <div class="attr">\n'
        f'    <div class="logo-circle"><span>Oak Park<br>Construction</span></div>\n'
        f'    <div class="attr-name">{attribution}</div>\n'
        f'  </div>\n'
        f'</div>'
    )


# Standalone renderers — keyed by template_id. build_opc_from_slide_plan picks
# from this dict before falling back to STANDALONE_TO_TIP_FALLBACK.
OPC_STANDALONE_COMPONENT_RENDERERS = {
    "opc_material_profile": render_opc_material_profile,
    "opc_four_card_grid":   render_opc_four_card_grid,
    "opc_item_spotlight":   render_opc_item_spotlight,
    "opc_statement":        render_opc_statement,
    "opc_base":             render_opc_base,
    "opc_progress_media":   render_opc_progress_media,
    "opc_duotone":          render_opc_duotone,
}


# Map of OPC tip slide-component key → callable. Used by opc_slide_planner +
# build_opc_from_slide_plan when the planner picks an opc_tip_* component for a slide.
OPC_TIP_COMPONENT_RENDERERS = {
    "opc_tip_cover":     render_opc_tip_cover,
    "opc_tip_stat":      render_opc_tip_stat,
    "opc_tip_list":      render_opc_tip_list,
    "opc_tip_explainer": render_opc_tip_explainer,
    "opc_tip_sources":   render_opc_tip_sources,
}


def _build_opc_html(content, slug, work_dir, media_paths=None):
    hl = _opc_story_cover_headline(content, slug)
    content["headline"] = hl
    # Guardrail: keep cover subhead short enough to avoid colliding with the bottom HUD lane.
    raw_subhead = str(content.get("subhead", "")).strip()
    if len(raw_subhead) > 110:
        cut = raw_subhead[:107].rsplit(" ", 1)[0].strip() or raw_subhead[:107].strip()
        raw_subhead = f"{cut}..."
    content["subhead"] = raw_subhead
    accent = content.get("accent_word", hl.split()[-1])
    hl_html = hl.replace(accent, f'<span class="accent">{accent}</span>')

    s2_hl = content.get("slide2_headline", "THE NUMBERS")
    s2_accent = s2_hl.split()[-1] if s2_hl else "NUMBERS"
    s2_html = s2_hl.replace(s2_accent, f'<span class="accent">{s2_accent}</span>')

    items_html = ""
    for i, item in enumerate(content.get("slide3_items", []), 1):
        items_html += f'''    <div class="list-item"><span class="list-num">{i:02d}</span><div><div class="list-text">{_cap34(item["title"])}</div><div class="list-sub">{item["sub"]}</div></div></div>\n'''

    sources_html = ""
    for i, src in enumerate(content.get("sources", []), 1):
        sources_html += f'    <div class="src-row"><span class="src-num">{i:02d}</span><span>{src}</span></div>\n'

    s4_hl = _opc_story_slide4_headline(content)
    s4_accent = s4_hl.split()[-1] if s4_hl else "MOVE"
    opc_slides_meta = content.get("slides", []) if isinstance(content.get("slides", []), list) else []
    cta = content.get("cta", "SAVE THIS.")

    cover_img = (media_paths or {}).get("cover", "")
    bg_photo_el = (
        f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
        if cover_img else '<div class="bg-photo"></div>'
    )
    # Last slide now follows cover visual language: full background + dark overlay.
    last_img = (
        ((media_paths or {}).get("slides", {}) or {}).get(5)
        or ((media_paths or {}).get("slides", {}) or {}).get(4)
        or ((media_paths or {}).get("slides", {}) or {}).get(2)
        or cover_img
    )
    sources_bg_el = (
        f'<div class="bg-photo" style="background-image:url(\'{last_img}\');"></div>'
        if last_img else '<div class="bg-photo"></div>'
    )

    def variant_block(v_class, cover_accent_style, s4_accent_style, src_accent_style):
        # Each tip slide is rendered by its own component function. Output stays
        # byte-identical to the pre-split version because the joins below preserve
        # the original f-string layout (\n<!-- V2 -->\n + 5 slides separated by \n\n + trailing \n).
        s2_ctx = _opc_tip_context_slot(2, "STAT CONTEXT IMAGE", opc_slides_meta, media_paths)
        s3_ctx = _opc_tip_context_slot(3, "PROCESS IMAGE",      opc_slides_meta, media_paths)
        s4_ctx = _opc_tip_context_slot(4, "TIP IN ACTION IMAGE", opc_slides_meta, media_paths)
        cover_html   = render_opc_tip_cover(content, v_class, hl_html=hl_html, bg_photo_el=bg_photo_el)
        stat_html    = render_opc_tip_stat(content, v_class, context_slot=s2_ctx)
        list_html    = render_opc_tip_list(content, v_class, items_html=items_html, context_slot=s3_ctx)
        explain_html = render_opc_tip_explainer(content, v_class, s4_hl=s4_hl, s4_accent=s4_accent,
                                                s4_accent_style=s4_accent_style, context_slot=s4_ctx)
        sources_block = render_opc_tip_sources(content, v_class, sources_html=sources_html,
                                               src_accent_style=src_accent_style,
                                               sources_bg_el=sources_bg_el, cta=cta)
        return (
            f"\n<!-- {v_class.upper()} -->\n"
            + cover_html + "\n\n"
            + stat_html + "\n\n"
            + list_html + "\n\n"
            + explain_html + "\n\n"
            + sources_block + "\n"
        )

    # SH-154 (legacy path mirror): single-variant lock — same logic as build_opc_from_slide_plan.
    # Default MANUAL_TEMPLATE_SET=single emits ONE variant for a clean 5-slide review email.
    # Non-single runs emit all 3 (v1 lime-bg + v2 black-bg + v3 cream-bg) = 15 PNGs.
    _tset = os.environ.get("MANUAL_TEMPLATE_SET", "").strip().lower()
    _proof_variant = os.environ.get("OPC_PROOF_VARIANT", "v2").strip().lower()
    if _proof_variant not in ("v1", "v2", "v3"):
        _proof_variant = "v2"
    _emit_v1 = _tset != "single" or _proof_variant == "v1"
    _emit_v2 = _tset != "single" or _proof_variant == "v2"
    _emit_v3 = _tset != "single" or _proof_variant == "v3"
    if _tset == "single":
        print(f"  [SH-154-legacy] single-variant lock — emitting {_proof_variant} only")
    v1 = variant_block("v1", "#CBCC10", "#CBCC10", "#CBCC10") if _emit_v1 else ""
    v2 = variant_block("v2", "#0A0A0A", "#CBCC10", "#CBCC10") if _emit_v2 else ""
    v3 = variant_block("v3", "#F0EBE3", "#F0EBE3", "#F0EBE3") if _emit_v3 else ""

    html_path = Path(work_dir) / "cover.html"

    with open(Path(__file__).parent / "opc_tip_base.css") as f:
        base_css = f.read()

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OPC — Tip — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@300;400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
{_local_font_face_css()}{base_css}
</style>
</head>
<body>
{v1}
{v2}
{v3}
</body>
</html>"""

    html_path.write_text(full_html)
    return str(html_path)


# Banned legacy template keys — must never appear in a slide plan or content dict.
# Phase 5 reviewer gates also enforce this; the renderer raises immediately if seen.
OPC_BANNED_TEMPLATE_KEYS = {"cutout", "illustrated", "opc_cutout", "opc_illustrated"}


def build_opc_from_slide_plan(content, slug, work_dir, media_paths=None):
    """Phase 4 — render a 5-slide OPC carousel from a per-slide template plan
    (content["_slide_plan"]). Each slide picks its own component renderer.

    The plan's status must be "passed" and its slides list must contain 5
    entries each with template_id + role. Templates that are not yet
    production-safe (Phase 6 standalones) automatically substitute their
    fallback_template_id so the band-aid stays renderable.

    Output is a complete <!DOCTYPE html>...</html> document written to
    cover.html in work_dir, same as _build_opc_html. Renders v2 + v3 variants
    so the existing export_variants.js cream/lime extraction still works.
    """
    plan = (content or {}).get("_slide_plan") or {}
    slides = plan.get("slides") or []
    if plan.get("status") != "passed" or len(slides) != 5:
        # Defensive: planner returned no usable plan — fall back to legacy tip.
        return _build_opc_html(content, slug, work_dir, media_paths=media_paths)

    # Hard-fail if any banned legacy key sneaks in (cutout/illustrated).
    for s in slides:
        tid = s.get("template_id", "")
        if tid in OPC_BANNED_TEMPLATE_KEYS:
            raise ValueError(
                f"build_opc_from_slide_plan: banned template key '{tid}' "
                f"in slide plan (slide {s.get('slide')}). "
                "cutout/illustrated were disabled — see commit 72ff06c."
            )

    # Pre-compute the same shared values _build_opc_html computes — so each
    # tip-component renderer has the data it needs regardless of which slide
    # role it ends up filling.
    hl = content.get("headline") or re.sub(r"[^a-zA-Z0-9 ]", " ", slug or "").upper().strip() or "THE GUIDE"
    raw_subhead = str(content.get("subhead", "")).strip()
    if len(raw_subhead) > 110:
        cut = raw_subhead[:107].rsplit(" ", 1)[0].strip() or raw_subhead[:107].strip()
        raw_subhead = f"{cut}..."
    content["subhead"] = raw_subhead
    accent = content.get("accent_word", hl.split()[-1])
    hl_html = hl.replace(accent, f'<span class="accent">{accent}</span>')

    items_html = ""
    for i, item in enumerate(content.get("slide3_items", []), 1):
        items_html += f'''    <div class="list-item"><span class="list-num">{i:02d}</span><div><div class="list-text">{_cap34(item["title"])}</div><div class="list-sub">{item["sub"]}</div></div></div>\n'''

    sources_html = ""
    for i, src in enumerate(content.get("sources", []), 1):
        sources_html += f'    <div class="src-row"><span class="src-num">{i:02d}</span><span>{src}</span></div>\n'

    s4_hl = content.get("slide4_headline", "THE PRO MOVE")
    s4_accent = s4_hl.split()[-1] if s4_hl else "MOVE"
    opc_slides_meta = content.get("slides", []) if isinstance(content.get("slides", []), list) else []
    cta = content.get("cta", "SAVE THIS.")

    cover_img = (media_paths or {}).get("cover", "")
    bg_photo_el = (
        f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
        if cover_img else '<div class="bg-photo"></div>'
    )
    last_img = (
        ((media_paths or {}).get("slides", {}) or {}).get(5)
        or ((media_paths or {}).get("slides", {}) or {}).get(4)
        or ((media_paths or {}).get("slides", {}) or {}).get(2)
        or cover_img
    )
    sources_bg_el = (
        f'<div class="bg-photo" style="background-image:url(\'{last_img}\');"></div>'
        if last_img else '<div class="bg-photo"></div>'
    )

    # Phase 6 — resolve each slide's RENDERER. Three buckets:
    #   1. Standalone (opc_material_profile, opc_duotone, ...) → use the ported
    #      Python builder from OPC_STANDALONE_COMPONENT_RENDERERS.
    #   2. Tip component (opc_tip_cover, opc_tip_stat, ...) → use the tip renderer.
    #   3. Anything else → fall back to fallback_template_id (must resolve to one
    #      of the above; reviewer flags otherwise).
    # Track fallbacks_used so reviewers + email preview can call out when ideal
    # design substituted for a tip equivalent.
    resolved_slides = []   # list of dicts: {slide, role, effective_id, kind, fell_back_from}
    fallbacks_used = []
    for s in slides:
        tid = s.get("template_id", "")
        if tid in OPC_STANDALONE_COMPONENT_RENDERERS:
            resolved_slides.append({"slide": s.get("slide"), "role": s.get("role"),
                                    "effective_id": tid, "kind": "standalone",
                                    "fell_back_from": None})
        elif tid in OPC_TIP_COMPONENT_RENDERERS:
            resolved_slides.append({"slide": s.get("slide"), "role": s.get("role"),
                                    "effective_id": tid, "kind": "tip",
                                    "fell_back_from": None})
        else:
            fb = s.get("fallback_template_id") or "opc_tip_explainer"
            if fb not in OPC_TIP_COMPONENT_RENDERERS and fb not in OPC_STANDALONE_COMPONENT_RENDERERS:
                fb = "opc_tip_explainer"
            kind = "standalone" if fb in OPC_STANDALONE_COMPONENT_RENDERERS else "tip"
            resolved_slides.append({"slide": s.get("slide"), "role": s.get("role"),
                                    "effective_id": fb, "kind": kind,
                                    "fell_back_from": tid or "(missing)"})
            fallbacks_used.append((s.get("slide"), tid, fb))

    # Stash the resolved plan + media_paths back onto content so the reviewer
    # can introspect what actually rendered (no need to re-pass kwargs).
    content.setdefault("_slide_plan", {})["_resolved_slides"] = resolved_slides
    content["_slide_plan"]["_fallbacks_used"] = fallbacks_used
    content["_media_paths"] = media_paths or {}
    needs_duotone_filter = any(r["effective_id"] == "opc_duotone" for r in resolved_slides)

    def _render_slide_for_variant(rs, v_class, s4_accent_style, src_accent_style):
        """Dispatch to the right renderer based on resolved kind + effective_id."""
        eff_key = rs["effective_id"]
        slide_num = rs["slide"]
        if rs["kind"] == "standalone":
            renderer = OPC_STANDALONE_COMPONENT_RENDERERS[eff_key]
            return renderer(content, v_class, slide_num=slide_num, media_paths=media_paths)
        # Tip components — kwargs differ per slide.
        if eff_key == "opc_tip_cover":
            return render_opc_tip_cover(content, v_class, hl_html=hl_html, bg_photo_el=bg_photo_el)
        if eff_key == "opc_tip_stat":
            ctx = _opc_tip_context_slot(2, "STAT CONTEXT IMAGE", opc_slides_meta, media_paths)
            return render_opc_tip_stat(content, v_class, context_slot=ctx)
        if eff_key == "opc_tip_list":
            ctx = _opc_tip_context_slot(3, "PROCESS IMAGE", opc_slides_meta, media_paths)
            return render_opc_tip_list(content, v_class, items_html=items_html, context_slot=ctx)
        if eff_key == "opc_tip_explainer":
            ctx = _opc_tip_context_slot(4, "TIP IN ACTION IMAGE", opc_slides_meta, media_paths)
            return render_opc_tip_explainer(
                content, v_class, s4_hl=s4_hl, s4_accent=s4_accent,
                s4_accent_style=s4_accent_style, context_slot=ctx,
            )
        if eff_key == "opc_tip_sources":
            return render_opc_tip_sources(
                content, v_class, sources_html=sources_html,
                src_accent_style=src_accent_style, sources_bg_el=sources_bg_el, cta=cta,
            )
        raise ValueError(f"build_opc_from_slide_plan: unknown effective key {eff_key!r}")

    def variant_block(v_class, s4_accent_style, src_accent_style):
        rendered = [
            _render_slide_for_variant(rs, v_class, s4_accent_style, src_accent_style)
            for rs in resolved_slides
        ]
        return f"\n<!-- {v_class.upper()} -->\n" + "\n\n".join(rendered) + "\n"

    # SH-154: single-variant lock — when MANUAL_TEMPLATE_SET=single (or unset in
    # manual mode), emit ONE variant family so the review email is one cohesive
    # 5-slide deck, not 10 mixed PNGs. Default OPC family = v2 (cream) — the
    # production-proven family. Override with OPC_PROOF_VARIANT=v3 if needed.
    _tset = os.environ.get("MANUAL_TEMPLATE_SET", "").strip().lower()
    _proof_variant = os.environ.get("OPC_PROOF_VARIANT", "v2").strip().lower()
    if _proof_variant not in ("v2", "v3"):
        if _proof_variant == "v1":
            print(f"  [SH-154] OPC_PROOF_VARIANT=v1 not supported in smart-plan path — using v2")
        _proof_variant = "v2"
    _emit_v2 = _tset != "single" or _proof_variant == "v2"
    _emit_v3 = _tset != "single" or _proof_variant == "v3"
    v2 = variant_block("v2", "#CBCC10", "#CBCC10") if _emit_v2 else ""
    v3 = variant_block("v3", "#F0EBE3", "#F0EBE3") if _emit_v3 else ""
    if _tset == "single":
        print(f"  [SH-154] single-variant lock — emitting {_proof_variant} only")

    html_path = Path(work_dir) / "cover.html"
    with open(Path(__file__).parent / "opc_tip_base.css") as f:
        base_css = f.read()
    # Phase 6 — load scoped CSS for any standalone slide that ships in this build.
    standalones_css_path = Path(__file__).parent / "opc_standalones.css"
    standalones_css = standalones_css_path.read_text() if standalones_css_path.exists() else ""
    # Inject duotone SVG filter ONCE per page if any slide uses opc_duotone.
    svg_filter_block = OPC_DUOTONE_SVG_FILTER if needs_duotone_filter else ""
    title_suffix = " · Plan" if plan.get("status") == "passed" else ""
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OPC — Smart Plan — {slug}{title_suffix}</title>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@300;400;700&family=JetBrains+Mono:wght@400;700&family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700;1,900&family=Cormorant+Garamond:wght@300;400;600&family=Barlow:ital,wght@0,400;0,700;1,700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
{_local_font_face_css()}{base_css}
{standalones_css}
</style>
</head>
<body>
{svg_filter_block}
{v2}
{v3}
</body>
</html>"""
    html_path.write_text(full_html)
    return str(html_path)


def _build_opc_progress_html(content, slug, work_dir, media_paths=None):
    """Progress post builder — cover + stage + what's done + what's next + credits.
    Each photo slide has a caption overlay explaining what's in the image."""
    project_name = content.get("project_name", slug.replace("-", " ").upper())
    project_address = content.get("project_address", "")
    stage = content.get("stage", "")
    stage_date = content.get("stage_date", "")
    whats_done = content.get("whats_done", "")
    whats_done_caption = content.get("whats_done_caption", "")
    whats_next = content.get("whats_next", "")
    whats_next_caption = content.get("whats_next_caption", "")
    project_id = content.get("project_id", "")
    workers = content.get("workers", [])

    def _prog_img(slide_key, fallback_label):
        img_path = ((media_paths or {}).get(slide_key, ""))
        if img_path:
            return f'<div class="prog-photo" style="background-image:url(\'{img_path}\');"></div>'
        return f'<div class="prog-photo prog-photo-empty"><span>{fallback_label}</span></div>'

    cover_img = (media_paths or {}).get("cover", "")
    cover_bg = (
        f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
        if cover_img else '<div class="bg-photo"></div>'
    )

    workers_html = ""
    for w in workers:
        name = w.get("name", "")
        role = w.get("role", "")
        if name:
            workers_html += f'<div class="cred-row"><span class="cred-name">{name}</span><span class="cred-role">{role}</span></div>'

    def variant_block(v_class):
        return f"""
<!-- {v_class.upper()} PROGRESS -->
<div class="slide slide-cover slide-progress-cover {v_class}">
  {cover_bg}
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Progress · Oak Park Construction</div>
  <div class="headline">{project_name}</div>
  <div class="prog-address">{project_address}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-stage {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Current Stage</div>
  <div class="headline prog-stage-hl">{stage}</div>
  <div class="prog-date">{stage_date}</div>
  {_prog_img("stage", "STAGE PHOTO")}
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-done {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">What&#39;s Done</div>
  {_prog_img("done", "COMPLETED WORK PHOTO")}
  <div class="prog-caption">{whats_done_caption}</div>
  <div class="prog-body">{whats_done}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-next {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">What&#39;s Next</div>
  {_prog_img("next", "UPCOMING WORK PHOTO")}
  <div class="prog-caption">{whats_next_caption}</div>
  <div class="prog-body">{whats_next}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-credits {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">The Team</div>
  <div class="headline prog-cred-hl">THE <span class="accent">CREW.</span></div>
  <div class="cred-list">{workers_html}</div>
  <div class="prog-project-id">{project_id}</div>
  <div class="footer">
    <span class="handle">@oakparkconstruction</span>
    <span class="license">LIC · CBC1263425</span>
  </div>
</div>
"""

    v2 = variant_block("v2")
    v3 = variant_block("v3")

    html_path = Path(work_dir) / "cover.html"
    with open(Path(__file__).parent / "opc_tip_base.css") as f:
        base_css = f.read()

    progress_extra_css = """
/* === Progress post additions === */
.prog-address {
  font-family:'JetBrains Mono', monospace; font-size:22px; font-weight:400;
  letter-spacing:0.12em; text-transform:uppercase; color:var(--c-body);
  margin-top:18px; max-width:820px;
}
.prog-date {
  font-family:'JetBrains Mono', monospace; font-size:20px; font-weight:700;
  letter-spacing:0.15em; text-transform:uppercase; color:#CBCC10;
  margin-bottom:22px;
}
.prog-stage-hl { font-size:96px; line-height:0.96; margin-bottom:12px; }
.prog-photo {
  width:100%; flex:1; min-height:320px; max-height:560px;
  background-size:cover; background-position:center;
  border:2px solid var(--c-brk); border-radius:8px;
  overflow:hidden; margin:20px 0 14px;
}
.prog-photo-empty {
  background:repeating-linear-gradient(45deg, rgba(203,204,16,0.04), rgba(203,204,16,0.04) 2px, transparent 2px, transparent 14px);
  border-style:dashed;
  display:flex; align-items:center; justify-content:center;
  font-family:'JetBrains Mono', monospace; font-size:16px; font-weight:700;
  letter-spacing:0.18em; color:rgba(203,204,16,0.4); text-transform:uppercase;
}
.prog-caption {
  font-family:'Anton', sans-serif; font-size:34px; line-height:1.1;
  color:#CBCC10; text-transform:uppercase; letter-spacing:0.02em;
  margin-bottom:12px;
}
.prog-body {
  font-family:'Roboto Condensed', sans-serif; font-size:30px; line-height:1.38;
  color:var(--c-body); max-width:840px;
}
.prog-project-id {
  font-family:'JetBrains Mono', monospace; font-size:16px; font-weight:700;
  letter-spacing:0.2em; color:rgba(203,204,16,0.55); text-transform:uppercase;
  margin-bottom:18px;
}
.cred-list { margin:32px 0 auto; }
.cred-row {
  display:flex; gap:28px; align-items:baseline;
  padding:14px 0; border-bottom:1px solid var(--c-rule);
  font-family:'Roboto Condensed', sans-serif;
}
.cred-name { font-size:36px; font-weight:700; color:var(--c-head); }
.cred-role { font-size:22px; font-weight:400; color:var(--c-body); }
.prog-cred-hl { font-size:96px; margin-bottom:8px; }
.v2 .prog-date { color:#0A0A0A; }
.v2 .prog-caption { color:#0A0A0A; }
.v2 .prog-photo-empty { background:repeating-linear-gradient(45deg, rgba(10,10,10,0.04), rgba(10,10,10,0.04) 2px, transparent 2px, transparent 14px); border-style:dashed; color:rgba(10,10,10,0.3); }
"""

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OPC — Progress — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@300;400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
{_local_font_face_css()}{base_css}
{progress_extra_css}
</style>
</head>
<body>
{v2}
{v3}
</body>
</html>"""

    html_path.write_text(full_html)
    return str(html_path)


def _build_opc_illustrated_html(content, slug, work_dir, media_paths=None):
    """Illustrated editorial variant:
    keeps OPC typography/colors, adds topic-related image blocks with sketch/line treatment."""
    hl = content.get("headline") or re.sub(r"[^a-zA-Z0-9 ]", " ", slug or "").upper().strip() or "THE GUIDE"
    accent = content.get("accent_word", hl.split()[-1] if hl else "")
    hl_html = hl.replace(accent, f'<span class="accent">{accent}</span>') if accent else hl

    s2_hl = content.get("slide2_headline", "THE NUMBERS")
    s2_accent = s2_hl.split()[-1] if s2_hl else "NUMBERS"
    s2_html = s2_hl.replace(s2_accent, f'<span class="accent">{s2_accent}</span>')

    items_html = ""
    for i, item in enumerate(content.get("slide3_items", []), 1):
        items_html += f'''    <div class="list-item"><span class="list-num">{i:02d}</span><div><div class="list-text">{_cap34(item["title"])}</div><div class="list-sub">{item["sub"]}</div></div></div>\n'''

    sources_html = ""
    for i, src in enumerate(content.get("sources", []), 1):
        sources_html += f'    <div class="src-row"><span class="src-num">{i:02d}</span><span>{src}</span></div>\n'

    cover_img = (media_paths or {}).get("cover", "")
    slide3_img = ((media_paths or {}).get("slides", {}) or {}).get(3, "")
    slide4_img = ((media_paths or {}).get("slides", {}) or {}).get(4, "")
    source_img = (
        ((media_paths or {}).get("slides", {}) or {}).get(5)
        or ((media_paths or {}).get("slides", {}) or {}).get(2)
        or cover_img
    )

    def img_panel(src, label):
        if src:
            return (f'<div class="ill-panel-wrap">'
                    f'<div class="ill-panel"><img src="{src}" alt="{label}" class="ill-photo"></div>'
                    f'<div class="ill-caption">&#x25B6; {label}</div>'
                    f'</div>')
        return f'<div class="ill-empty"><span>{label}</span></div>'

    s4_hl = content.get("slide4_headline", "THE PRO MOVE")
    s4_accent = s4_hl.split()[-1] if s4_hl else "MOVE"
    cta = content.get("cta", "SAVE THIS.")

    def variant_block(v_class):
        return f"""
<div class="slide slide-cover {v_class} ill-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="ill-bg" style="background-image:url('{cover_img}');"></div>
  <div class="ill-grain"></div>
  <div class="tag">Illustrated Tip · Oak Park Construction</div>
  <div class="headline">{hl_html}</div>
  <div class="body-text">{content.get("subhead","")}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-stat {v_class} ill-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">The Real Number</div>
  <div class="stat-big">{content.get("slide2_stat", "—")}</div>
  <div class="stat-label">{content.get("slide2_label", "")}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-list {v_class} ill-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Why It Happens</div>
  {img_panel(slide3_img, "TOPIC IMAGE")}
  <div class="list">
{items_html}  </div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-tip {v_class} ill-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Pro Tip</div>
  <div class="tip-label"><span class="tip-arrow">&#9658;</span> The Pro Move</div>
  <div class="tip-big">{s4_hl.replace(s4_accent, f'<span class="accent">{s4_accent}</span>')}</div>
  {img_panel(slide4_img, "DETAIL IMAGE")}
  <div class="tip-explain">{content.get("slide4_body", "")}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-sources {v_class} ill-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="ill-bg" style="background-image:url('{source_img}');"></div>
  <div class="ill-grain"></div>
  <div class="tag">Sources</div>
  <div class="src-head">WHERE THIS<br>COMES <span class="accent">FROM.</span></div>
  <div class="src-list">
{sources_html}  </div>
  <div class="save-cta">{cta}</div>
  <div class="footer">
    <span class="handle">@oakparkconstruction</span>
    <span class="license">LIC · CBC1263425</span>
  </div>
</div>
"""

    v2 = variant_block("v2")
    v3 = variant_block("v3")

    html_path = Path(work_dir) / "cover.html"
    with open(Path(__file__).parent / "opc_tip_base.css") as f:
        base_css = f.read()

    illustrated_css = """
.ill-shell { position:relative; overflow:hidden; }
.ill-bg {
  position:absolute; inset:0; background-size:cover; background-position:center;
  filter: grayscale(0.18) contrast(1.22) brightness(0.88) saturate(0.82);
  opacity:0.22; z-index:0;
}
.ill-grain {
  position:absolute; inset:0; z-index:1; pointer-events:none;
  background-image:
    repeating-linear-gradient(0deg, rgba(255,255,255,0.04) 0px, rgba(255,255,255,0.04) 1px, transparent 1px, transparent 3px),
    repeating-linear-gradient(90deg, rgba(255,255,255,0.025) 0px, rgba(255,255,255,0.025) 1px, transparent 1px, transparent 4px);
  mix-blend-mode: soft-light;
}
.ill-shell > *:not(.ill-bg):not(.ill-grain):not(.arrow):not(.slide-logo):not(.corner) { position:relative; z-index:2; }
/* Editorial image panel — no border, cinematic treatment */
.ill-panel-wrap { margin:14px 0 6px; }
.ill-panel {
  width:100%; min-height:240px; max-height:320px; overflow:hidden;
  border-radius:4px; box-shadow: 0 8px 32px rgba(0,0,0,.58);
}
.ill-photo {
  width:100%; height:100%; object-fit:cover;
  filter: contrast(1.2) saturate(0.72) brightness(0.94) sepia(0.06);
}
.ill-caption {
  font-family:'JetBrains Mono', monospace; font-size:13px; font-weight:400;
  letter-spacing:.14em; text-transform:uppercase; color:var(--c-tag);
  margin-top:9px; padding:0 2px;
}
.v2 .ill-bg { opacity:.18; filter: grayscale(0.06) contrast(1.1) brightness(1.04) saturate(1.05); }
.v2 .ill-grain { background-image:
  repeating-linear-gradient(0deg, rgba(0,0,0,0.032) 0px, rgba(0,0,0,0.032) 1px, transparent 1px, transparent 3px),
  repeating-linear-gradient(90deg, rgba(0,0,0,0.018) 0px, rgba(0,0,0,0.018) 1px, transparent 1px, transparent 4px); }
.v2 .ill-panel { box-shadow: 0 6px 22px rgba(0,0,0,.22); }
.v2 .ill-photo { filter: contrast(1.12) saturate(0.88) brightness(1.03); }
.v2 .ill-empty { border-color:rgba(10,10,10,.32); color:rgba(10,10,10,.32); }
"""

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OPC — Illustrated — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@300;400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
{_local_font_face_css()}{base_css}
{illustrated_css}
</style>
</head>
<body>
{v2}
{v3}
</body>
</html>"""
    html_path.write_text(full_html)
    return str(html_path)


def _build_opc_cutout_html(content, slug, work_dir, media_paths=None):
    """Cutout sticker editorial variant:
    designed for background-removed PNGs when available, with graceful fallback."""
    hl = content.get("headline") or re.sub(r"[^a-zA-Z0-9 ]", " ", slug or "").upper().strip() or "THE GUIDE"
    accent = content.get("accent_word", hl.split()[-1] if hl else "")
    hl_html = hl.replace(accent, f'<span class="accent">{accent}</span>') if accent else hl

    s2_hl = content.get("slide2_headline", "THE NUMBERS")
    s2_accent = s2_hl.split()[-1] if s2_hl else "NUMBERS"
    s2_html = s2_hl.replace(s2_accent, f'<span class="accent">{s2_accent}</span>')

    items_html = ""
    for i, item in enumerate(content.get("slide3_items", []), 1):
        items_html += f'''    <div class="list-item"><span class="list-num">{i:02d}</span><div><div class="list-text">{_cap34(item["title"])}</div><div class="list-sub">{item["sub"]}</div></div></div>\n'''

    sources_html = ""
    for i, src in enumerate(content.get("sources", []), 1):
        sources_html += f'    <div class="src-row"><span class="src-num">{i:02d}</span><span>{src}</span></div>\n'

    cover_img_raw = (media_paths or {}).get("cover", "")
    slide3_img_raw = ((media_paths or {}).get("slides", {}) or {}).get(3, "")
    slide4_img_raw = ((media_paths or {}).get("slides", {}) or {}).get(4, "")
    source_img = (
        ((media_paths or {}).get("slides", {}) or {}).get(5)
        or ((media_paths or {}).get("slides", {}) or {}).get(2)
        or cover_img_raw
    )
    # Cutout style: remove bg from product images so they float on brand background
    cover_img  = _remove_background(cover_img_raw, work_dir) if cover_img_raw else ""
    slide3_img = _remove_background(slide3_img_raw, work_dir) if slide3_img_raw else ""
    slide4_img = _remove_background(slide4_img_raw, work_dir) if slide4_img_raw else ""

    work = Path(work_dir)
    cut3 = work / "resources" / "cutouts" / "slide_3.png"
    cut4 = work / "resources" / "cutouts" / "slide_4.png"
    cut5 = work / "resources" / "cutouts" / "slide_5.png"

    def _sticker_tag(img_src, label, cutout_src=""):
        src = cutout_src or img_src
        if src:
            return f'<div class="cutout-wrap"><img src="{src}" alt="{label}" class="cutout-img"></div>'
        return f'<div class="cutout-wrap cutout-empty"><span>{label}</span></div>'

    s4_hl = content.get("slide4_headline", "THE PRO MOVE")
    s4_accent = s4_hl.split()[-1] if s4_hl else "MOVE"
    cta = content.get("cta", "SAVE THIS.")

    def variant_block(v_class):
        cut3_src = "resources/cutouts/slide_3.png" if cut3.exists() else ""
        cut4_src = "resources/cutouts/slide_4.png" if cut4.exists() else ""

        # Slide 3: museum artifact centered above list
        art3_src = cut3_src or slide3_img
        if art3_src:
            art3 = (f'<div class="cut-artifact">'
                    f'<img src="{art3_src}" alt="material" class="cut-artifact-img">'
                    f'<div class="cut-artifact-label">&#x25BC; detail</div>'
                    f'</div>')
        else:
            art3 = '<div class="cut-artifact"><div class="cut-artifact-empty">MATERIAL PHOTO</div></div>'

        # Slide 4: split layout — artifact left, tip text right; fall back to stacked if no image
        art4_src = cut4_src or slide4_img
        s4_hl_html = s4_hl.replace(s4_accent, f'<span class="accent">{s4_accent}</span>')
        if art4_src:
            slide4_inner = (f'<div class="cut-split-grid">'
                            f'<div class="cut-split-art"><img src="{art4_src}" alt="pro tip detail"></div>'
                            f'<div><div class="tip-big">{s4_hl_html}</div>'
                            f'<div class="tip-explain">{content.get("slide4_body", "")}</div></div>'
                            f'</div>')
        else:
            slide4_inner = (f'<div class="tip-big">{s4_hl_html}</div>'
                            f'<div class="tip-explain">{content.get("slide4_body", "")}</div>')

        cover_float = (
            f'<img class="cut-cover-float" src="{cover_img}" alt="product">'
            if cover_img else ""
        )
        return f"""
<div class="slide slide-cover {v_class} cut-shell {'has-cover-float' if cover_img else ''}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  {cover_float}
  <div class="cut-cover-text">
    <div class="tag">Cutout Tip · Oak Park Construction</div>
    <div class="headline">{hl_html}</div>
    <div class="body-text">{content.get("subhead","")}</div>
  </div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-stat {v_class} cut-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">The Real Number</div>
  <div class="stat-big">{content.get("slide2_stat", "—")}</div>
  <div class="stat-label">{content.get("slide2_label", "")}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-list {v_class} cut-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Why It Happens</div>
  {art3}
  <div class="list">
{items_html}  </div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-tip {v_class} cut-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Pro Tip</div>
  <div class="tip-label"><span class="tip-arrow">&#9658;</span> The Pro Move</div>
  {slide4_inner}
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-sources {v_class} cut-shell">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="cut-bg" style="background-image:url('{source_img}');"></div>
  <div class="tag">Sources</div>
  <div class="src-head">WHERE THIS<br>COMES <span class="accent">FROM.</span></div>
  <div class="src-list">
{sources_html}  </div>
  <div class="save-cta">{cta}</div>
  <div class="footer">
    <span class="handle">@oakparkconstruction</span>
    <span class="license">LIC · CBC1263425</span>
  </div>
</div>
"""

    v2 = variant_block("v2")
    v3 = variant_block("v3")

    html_path = Path(work_dir) / "cover.html"
    with open(Path(__file__).parent / "opc_tip_base.css") as f:
        base_css = f.read()

    cutout_css = """
.cut-shell { position:relative; overflow:visible; }
.cut-bg {
  position:absolute; inset:0; background-size:cover; background-position:center;
  filter: grayscale(0.1) contrast(1.15) brightness(0.85);
  opacity:0.16; z-index:0;
}
.cut-shell > *:not(.cut-bg):not(.arrow):not(.slide-logo):not(.corner) { position:relative; z-index:2; }
/* Museum artifact hero — large centered floating object */
.cut-artifact {
  width:100%; display:flex; flex-direction:column; align-items:center;
  margin:10px 0 4px;
}
.cut-artifact-img {
  max-height:390px; min-height:110%; max-width:68%; object-fit:contain; object-position:top center;
  filter: drop-shadow(0 16px 40px rgba(0,0,0,.44)) contrast(1.1) saturate(1.06);
}
.cut-artifact-label {
  font-family:'JetBrains Mono', monospace; font-size:13px; font-weight:700;
  letter-spacing:.2em; text-transform:uppercase; color:rgba(203,204,16,.52);
  margin-top:9px; text-align:center;
}
.cut-artifact-empty {
  min-height:180px; width:70%; border:2px dashed rgba(203,204,16,.36);
  border-radius:4px; display:flex; align-items:center; justify-content:center;
  font-family:'JetBrains Mono',monospace; font-size:13px;
  letter-spacing:.16em; color:rgba(203,204,16,.34);
}
/* Side-by-side: artifact left + tip text right */
.cut-split-grid {
  display:grid; grid-template-columns:1fr 1.25fr; gap:24px;
  align-items:center; margin:14px 0;
}
.cut-split-art {
  display:flex; justify-content:center; align-items:center;
}
.cut-split-art img {
  max-height:300px; min-height:110%; max-width:100%; object-fit:contain; object-position:top center;
  filter: drop-shadow(0 10px 26px rgba(0,0,0,.40)) contrast(1.08);
}
/* Cover slide — floating bg-removed product image */
.cut-cover-text {
  display:flex; flex-direction:column; justify-content:center;
  flex:1; position:relative; z-index:2;
  max-width:580px;
}
.cut-cover-float {
  position:absolute; right:-30px; top:50%; transform:translateY(-50%);
  width:440px; height:auto; object-fit:contain;
  filter: drop-shadow(0 24px 56px rgba(0,0,0,.55)) contrast(1.08) saturate(1.04);
  z-index:1; pointer-events:none;
}
.has-cover-float .cut-cover-text { max-width:560px; }
.v2 .cut-cover-float { filter: drop-shadow(0 18px 40px rgba(0,0,0,.28)) contrast(1.06) saturate(1.04); }
/* v2 variant overrides */
.v2 .cut-bg { opacity:.12; filter: grayscale(0.04) contrast(1.06) brightness(1.04); }
.v2 .cut-artifact-img { filter: drop-shadow(0 14px 34px rgba(0,0,0,.22)) contrast(1.08) saturate(1.04); }
.v2 .cut-artifact-label { color:rgba(10,10,10,.38); }
.v2 .cut-artifact-empty { border-color:rgba(10,10,10,.3); color:rgba(10,10,10,.3); }
.v2 .cut-split-art img { filter: drop-shadow(0 8px 20px rgba(0,0,0,.18)) contrast(1.06); }
"""

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OPC — Cutout — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@300;400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
{_local_font_face_css()}{base_css}
{cutout_css}
</style>
</head>
<body>
{v2}
{v3}
</body>
</html>"""
    html_path.write_text(full_html)
    return str(html_path)


def _build_news_shared_template_html(content, slug, work_dir, style, handle="@HANDLE_PLACEHOLDER", media_paths=None, niche="brazil"):
    """Shared cross-niche renderer (Brazil/USA) with brand colors.
    style: illustrated | cutout
    Uses existing Brazil/USA generated structure, but with shared layout language."""

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # Brand tokens per niche (shared layout, different colors)
    if niche == "usa":
        brand = {
            "obsidian": "#0E0D0B",
            "paper": "#F2ECE0",
            "accent": "#C84040",
            "muted": "#8E8478",
            "tag": "The Chain",
        }
    else:
        brand = {
            "obsidian": "#0E0D0B",
            "paper": "#F2ECE0",
            "accent": "#C9A84C",
            "muted": "#7A7267",
            "tag": "Quem decidiu isso?",
        }

    cover_pt = esc(content.get("cover_pt", "TÍTULO AQUI"))
    cover_en = esc(content.get("cover_en", "TITLE HERE"))
    cta_pt = esc(content.get("cta_pt", "Salva pra não esquecer."))
    cta_en = esc(content.get("cta_en", "Save this."))
    sources = content.get("sources", [])
    cover_img = (media_paths or {}).get("cover", "")
    slides = content.get("slides", [])

    work = Path(work_dir)
    cut3 = work / "resources" / "cutouts" / "slide_3.png"
    cut4 = work / "resources" / "cutouts" / "slide_4.png"
    cut5 = work / "resources" / "cutouts" / "slide_5.png"

    def slot_img(slide_i, label, use_cutout=False):
        img = (media_paths or {}).get("slides", {}).get(slide_i, "")
        cut = ""
        if use_cutout:
            if slide_i == 3 and cut3.exists():
                cut = str(cut3)
            elif slide_i == 4 and cut4.exists():
                cut = str(cut4)
            elif slide_i == 5 and cut5.exists():
                cut = str(cut5)
        src = cut or img
        if src:
            cls = "cutout-img" if use_cutout else "ill-photo"
            wrap = "cutout-wrap" if use_cutout else "ill-panel"
            return f'<div class="{wrap}"><img src="{src}" alt="{label}" class="{cls}"></div>'
        return ""  # No image — render nothing, never a placeholder box

    html = []
    html.append(f"""
<div class="slide slide-cover shared-shell">
  <div class="shared-bg" style="background-image:url('{cover_img}');"></div>
  <div class="tag">{brand['tag']}</div>
  <div class="cover-hl">{cover_pt}</div>
  <div class="cover-en">{cover_en}</div>
  <div class="swipe">SWIPE &#8594;</div>
  <div class="footer-handle">{handle}</div>
</div>
""")

    for i, s in enumerate(slides, start=2):
        h_pt = esc(s.get("heading_pt", ""))
        h_en = esc(s.get("heading_en", ""))
        stype = s.get("type", "list")

        if stype == "profile":
            facts = "".join(f"<li>{esc(x)}</li>" for x in s.get("facts_pt", []))
            visual_block = slot_img(i, "PROFILE", use_cutout=(style == "cutout"))
            html.append(f"""
<div class="slide shared-shell">
  <div class="tag">Perfil</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {visual_block}
  <ul class="item-list">{facts}</ul>
  <div class="swipe">SWIPE &#8594;</div>
</div>
""")
        elif stype == "data":
            nums = "".join(
                f"<div class='num-block'><div class='num-val'>{esc(n.get('value','—'))}</div><div class='num-label'>{esc(n.get('label_pt',''))}</div></div>"
                for n in s.get("numbers", [])[:4]
            )
            visual_block = slot_img(i, "DATA VISUAL", use_cutout=(style == "cutout"))
            html.append(f"""
<div class="slide shared-shell">
  <div class="tag">Números</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {visual_block}
  <div class="nums-grid">{nums}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
""")
        elif stype == "quote":
            quote = esc(s.get("quote", ""))
            source = esc(s.get("source", ""))
            visual_block = slot_img(i, "QUOTE VISUAL", use_cutout=(style == "cutout"))
            html.append(f"""
<div class="slide shared-shell">
  <div class="tag">Contexto</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {visual_block}
  <div class="quote-block">
    <div class="quote-text">"{quote}"</div>
    <div class="quote-source">— {source}</div>
  </div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
""")
        else:
            items = "".join(f"<li>{esc(x)}</li>" for x in s.get("items_pt", []))
            visual_block = slot_img(i, "TOPIC VISUAL", use_cutout=(style == "cutout"))
            html.append(f"""
<div class="slide shared-shell">
  <div class="tag">Segue o fio</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {visual_block}
  <ul class="item-list">{items}</ul>
  <div class="swipe">SWIPE &#8594;</div>
</div>
""")

    src_rows = "".join(
        f'<div class="src-row"><span class="src-num">{idx:02d}</span><span>{esc(src)}</span></div>'
        for idx, src in enumerate(sources, 1)
    )
    html.append(f"""
<div class="slide slide-sources shared-shell">
  <div class="tag">Fontes</div>
  <div class="src-head">A FONTE É <span class="accent">ESTA.</span></div>
  {slot_img(5, "SOURCE VISUAL", use_cutout=(style == "cutout"))}
  <div class="src-list">{src_rows}</div>
  <div class="cta-pt">{cta_pt}</div>
  <div class="cta-en">{cta_en}</div>
  <div class="footer-handle">{handle}</div>
</div>
""")

    shared_css = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ob:{brand['obsidian']};--pa:{brand['paper']};--ac:{brand['accent']};--mu:{brand['muted']};--W:1080px;--H:1350px;--P:{SLIDE_INSET_PX}px}}
body{{background:#111;display:flex;flex-wrap:wrap;gap:24px;padding:24px;font-family:'Inter',sans-serif}}
.slide{{width:var(--W);height:var(--H);background:var(--ob);color:var(--pa);padding:var(--P);position:relative;overflow:hidden;display:flex;flex-direction:column}}
.shared-shell{{position:relative}}
.shared-bg{{position:absolute;inset:0;background-size:cover;background-position:center;opacity:.24;filter:grayscale(.08) contrast(1.14) brightness(.9)}}
.shared-shell > *:not(.shared-bg):not(.swipe):not(.footer-handle){{position:relative;z-index:2}}
.tag{{font-family:'JetBrains Mono',monospace;font-size:24px;color:var(--mu);margin-bottom:22px;text-transform:uppercase}}
.accent{{color:var(--ac)}}
.cover-hl{{font-family:'Fraunces',serif;font-size:96px;line-height:1.02;text-transform:uppercase;margin-bottom:18px}}
.cover-claim{{font-family:'Roboto Condensed',sans-serif;font-size:36px;font-weight:400;color:var(--pa);font-style:italic;line-height:1.3;margin-bottom:18px;border-left:3px solid var(--ac);padding-left:16px}}
.cover-en,.slide-en,.cta-en{{font-style:italic;color:var(--mu)}}
.slide-hl{{font-family:'Fraunces',serif;font-size:64px;line-height:1.08;text-transform:uppercase;margin-bottom:8px}}
.item-list{{list-style:none;flex:1}}
.item-list li{{font-size:32px;line-height:1.3;padding:14px 0;border-bottom:1px solid rgba(242,236,224,.12)}}
.item-list li::before{{content:"→ ";color:var(--ac)}}
.nums-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
.num-block{{background:rgba(244,196,48,.08);border:1px solid rgba(244,196,48,.2);padding:18px;border-radius:8px}}
.num-val{{font-family:'Fraunces',serif;font-size:58px;color:var(--ac)}}
.num-label{{font-size:22px}}
.quote-block{{background:rgba(0,0,0,.32);border-left:4px solid var(--ac);padding:24px 26px;margin-top:8px}}
.quote-text{{font-size:34px;line-height:1.35}}
.quote-source{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--mu);margin-top:10px}}
.ill-panel{{width:100%;min-height:220px;max-height:320px;margin:14px 0 16px;border:2px solid rgba(244,196,48,.45);border-radius:8px;overflow:hidden;background:#151515;display:flex;align-items:center;justify-content:center}}
.ill-photo{{width:100%;height:100%;object-fit:cover;filter:contrast(1.2) saturate(1.08)}}
.ill-empty{{border-style:dashed;color:rgba(244,196,48,.55);font-family:'JetBrains Mono',monospace;font-size:14px;letter-spacing:.12em}}
.cutout-wrap{{width:100%;min-height:220px;max-height:320px;margin:14px 0 16px;display:flex;align-items:flex-start;justify-content:center;overflow:visible}}
.cutout-img{{max-width:100%;min-height:110%;object-fit:contain;object-position:top center;filter:drop-shadow(0 10px 26px rgba(0,0,0,.38)) contrast(1.1) saturate(1.05)}}
.cutout-empty{{border:2px dashed rgba(244,196,48,.45);border-radius:8px;align-items:center;color:rgba(244,196,48,.52);font-family:'JetBrains Mono',monospace;font-size:14px;letter-spacing:.12em}}
.src-head{{font-family:'Fraunces',serif;font-size:68px;line-height:1.0;text-transform:uppercase;margin-bottom:22px}}
.src-list{{flex:1;overflow:hidden}}
.src-row{{display:flex;gap:14px;padding:8px 0;border-bottom:1px solid rgba(242,236,224,.09);font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--mu)}}
.src-num{{color:var(--ac);width:32px;flex-shrink:0}}
.cta-pt{{font-size:34px;font-weight:700;margin-top:18px}}
.swipe{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--mu);position:absolute;right:var(--P);bottom:var(--P)}}
.footer-handle{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--mu);position:absolute;left:var(--P);bottom:var(--P)}}
"""

    full = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>News Shared Template — {style} — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;1,9..144,700&family=Inter:wght@400;500;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>{shared_css}</style>
</head>
<body>
{''.join(html)}
</body>
</html>"""

    html_path = Path(work_dir) / "cover.html"
    html_path.write_text(full)
    return str(html_path)


def _build_brazil_html(content, slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    """Generate Brazil News 1080x1350 carousel HTML — dark + Canário brand spec v1.1.
    handle: footer handle shown on slides — defaults to @HANDLE_PLACEHOLDER for non-Brazil niches."""

    # Avoid double-docstring (was a copy-paste artifact)

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    cover_pt           = esc(content.get("cover_pt", "TÍTULO AQUI"))
    cover_en           = esc(content.get("cover_en", "TITLE HERE"))
    cover_accent       = esc(content.get("cover_accent", ""))
    cover_date         = esc(content.get("cover_date", ""))
    cover_stamp        = esc(content.get("cover_stamp", ""))        # e.g. "ARQUIVADO · 2024"
    person_attribution = esc(content.get("person_attribution", "")) # e.g. "Flávio Bolsonaro · PL"
    cta_pt             = esc(content.get("cta_pt", "Salva pra não esquecer."))
    cta_en             = esc(content.get("cta_en", "Save this."))
    sources     = content.get("sources", [])

    raw_cover = content.get("cover_pt", "")
    if cover_accent and cover_accent in raw_cover:
        # em = italic + canário yellow (matches original Rachadinha EP001 design)
        cover_hl = cover_pt.replace(cover_accent, f'<em>{cover_accent}</em>', 1)
    else:
        cover_hl = cover_pt

    clips = (media_paths or {}).get("clips", {})
    cover_img = (media_paths or {}).get("cover", "")
    # slide_photos: per-slide CC photos fetched by _fetch_slide_photos_brazil()
    # Keys are slide_i (int, 1-based matching the enumerate below starting at 2)
    slide_photos = (media_paths or {}).get("slide_photos", {})
    # Cover is always a motion slide — full-bleed photo/clip background
    # Cover background: full-bleed grayscale photo + halftone (v1 Rachadinha treatment)
    cover_bg_el = (
        f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
        f'<div class="halftone"></div>'
    ) if cover_img else ""
    # Cover sticker-slot: portrait photo absolutely positioned at right, same photo.
    # When no AI image available, fall back to bio-initials so cover is never faceless.
    if cover_img:
        cover_sticker_el = f'<div class="sticker-slot"><img src="{cover_img}" alt="cover portrait"></div>'
        cover_sticker_class = "cover-with-sticker"
    else:
        # Bio-initials fallback — extract name from clip_suggestions[0].person_or_topic
        _cs = content.get("clip_suggestions", [{}])[0] if content.get("clip_suggestions") else {}
        _pot = _cs.get("person_or_topic", "")
        # Extract only consecutive capitalized words (proper name) — stop at first lowercase word
        _raw = _pot.split("+")[0].strip().split("—")[0].strip() if _pot else ""
        _name_words = []
        for _w in _raw.split():
            if _w and _w[0].isupper():
                _name_words.append(_w)
            else:
                break
        _cname = " ".join(_name_words[:3]) or _raw[:30]  # max 3 name words; last-resort: first 30 chars
        _cini = "".join(w[0].upper() for w in _cname.split() if w)[:2] if _cname else ""
        if _cini:
            cover_sticker_el = (
                f'<div class="sticker-slot sticker-initials">'
                f'<div class="bio-initials">{_cini}</div>'
                f'<div class="bio-init-name">{esc(_cname)}</div>'
                f'</div>'
            )
            cover_sticker_class = "cover-with-sticker"
        else:
            cover_sticker_el = ""
            cover_sticker_class = ""
    # Credibility badge (dados-ou-agenda: ALTA/MÉDIA/BAIXA CREDIBILIDADE)
    _cred_raw = esc(content.get("cover_credibility_badge", ""))
    if _cred_raw:
        _cred_cls = "cred-alta" if "ALTA" in _cred_raw.upper() else ("cred-baixa" if "BAIXA" in _cred_raw.upper() else "cred-media")
        _cred_el = f'<div class="cred-badge {_cred_cls}">{_cred_raw}</div>'
    else:
        _cred_el = ""
    # Series tag — route by _template_key so each series shows its own label on the cover
    _tkey = (content.get("_template_key") or "").lower()
    _series_tag_map = {
        "dados-ou-agenda": "Dados vs Opinião",
        "verificamos": "Verificamos",
        "verificamos_clip": "Verificamos",
        "arquivo-aberto": "Arquivo Aberto",
        "a-conta": "A Conta que Ninguém Pagou",
        "verdade-pela-metade": "Verdade Pela Metade",
    }
    cover_series_tag = _series_tag_map.get(_tkey, "Quem decidiu isso?")
    # cover_claim: the hook claim shown on slide 1 — the opinion/statement being fact-checked
    _cover_claim_raw = content.get("cover_claim", "")
    cover_claim_el = f'<div class="cover-claim">"{esc(_cover_claim_raw)}"</div>' if _cover_claim_raw else ""
    slides_html = f"""
<div class="slide slide-cover slide-motion {cover_sticker_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  {cover_bg_el}
  {cover_sticker_el}
  {f'<div class="stamp-badge">{cover_stamp}</div>' if cover_stamp else ""}
  <div class="tag">{cover_series_tag}</div>
  <div class="cover-date">{cover_date}</div>
  <div class="cover-hl">{cover_hl}</div>
  {cover_claim_el}
  <div class="cover-en">{cover_en}</div>
  {_cred_el}
  <div class="swipe">SEGUE O FIO &#8594;</div>
  {f'<div class="person-pill">{person_attribution}</div>' if person_attribution else f'<div class="footer-handle">{handle}</div>'}
</div>
"""

    for slide_i, slide in enumerate(content.get("slides", []), start=2):
        stype = slide.get("type", "list")
        h_pt  = esc(slide.get("heading_pt", ""))
        h_en  = esc(slide.get("heading_en", ""))
        # Heading accent — highlight one word/phrase yellow (same mechanic as cover_accent)
        h_accent = esc(slide.get("heading_accent", ""))
        if h_accent and h_accent in h_pt:
            h_pt = h_pt.replace(h_accent, f'<span class="accent">{h_accent}</span>', 1)
        # Alternating motion pattern: odd slide_i (3, 5, 7) = motion (grayscale photo + halftone)
        # even (2, 4, 6) = static (no bg, clean dark slide)
        is_motion_slide = (slide_i % 2 == 1)
        motion_class = "slide-motion" if is_motion_slide else ""
        # Priority: dedicated slide photo → clip (video poster) → cover photo reuse
        slide_photo = (
            slide_photos.get(slide_i, "")
            or clips.get(slide_i, "")
            or (media_paths or {}).get("slides", {}).get(slide_i, "")
        )
        v_hint_raw = str(slide.get("visual_hint", "none")).strip().lower()
        if is_motion_slide and slide_photo:
            # FIX 10: add explicit dark overlay when context-image bg is present so text stays white
            _ctx_overlay = (
                '<div style="position:absolute;inset:0;background:rgba(0,0,0,0.55);'
                'border-radius:inherit;z-index:2;pointer-events:none;"></div>'
                if v_hint_raw == "context-image" else ""
            )
            clip_el = (
                f'<div class="bg-photo" style="background-image:url(\'{slide_photo}\');"></div>'
                f'<div class="halftone"></div>'
                f'{_ctx_overlay}'
            )
        else:
            clip_el = ""
        corners = '<div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>'

        if stype == "profile":
            party    = esc(slide.get("party_tag", ""))
            facts    = slide.get("facts_pt", [])
            sticker  = esc(slide.get("sticker_name", "PESSOA"))
            facts_li = "".join(f"<li>{esc(f)}</li>" for f in facts)

            # Try to source a real CC photo for the main subject sticker-slot.
            # Priority: mentioned_people[0].image_hint → cover_visual option_a query → sticker_name.
            people_list = slide.get("mentioned_people", [])
            photo_query = ""
            if people_list:
                photo_query = people_list[0].get("image_hint", "") or people_list[0].get("name", "")
            if not photo_query:
                cv = content.get("cover_visual", {})
                photo_query = cv.get("option_a", {}).get("search_query", "")
            if not photo_query:
                photo_query = slide.get("sticker_name", "")

            safe_filename = re.sub(r"[^\w]", "_", (sticker or "subject").lower())[:30] + ".jpg"
            photo_path = _fetch_person_photo(photo_query, work_dir, safe_filename) if photo_query else ""

            if photo_path:
                sticker_el = (
                    f'<div class="sticker-slot sticker-photo" '
                    f'style="background-image:url(\'{photo_path}\');background-size:cover;'
                    f'background-position:center top;border:none;border-radius:4px;"></div>'
                )
            else:
                # Route D: .bio-initials fallback — always looks intentional, never a raw placeholder
                initials = "".join(w[0].upper() for w in sticker.replace("_", " ").split() if w)[:2] or "??"
                sticker_el = (
                    f'<div class="sticker-slot sticker-initials">'
                    f'<div class="bio-initials">{initials}</div>'
                    f'<div class="bio-init-name">{sticker.replace("_", " ").title()}</div>'
                    f'</div>'
                )

            slides_html += f"""
<div class="slide slide-profile {motion_class}">
  {corners}{clip_el}
  <div class="tag">Quem é</div>
  <div class="party-tag">{party}</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="profile-layout">
    {sticker_el}
    <ul class="fact-list">{facts_li}</ul>
  </div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "data":
            nums_html = ""
            for n in slide.get("numbers", [])[:4]:
                nums_html += f'<div class="num-block"><div class="num-val">{esc(n.get("value","—"))}</div><div class="num-label">{esc(n.get("label_pt",""))}</div><div class="num-en">{esc(n.get("label_en",""))}</div></div>\n'
            v_hint = slide.get("visual_hint", "none")
            ctx_q = esc(slide.get("context_image_query", ""))
            _slide_img = (media_paths or {}).get("slides", {}).get(slide_i, "")
            if v_hint == "context-image":
                if _slide_img:
                    ctx_slot = f'\n  <div class="context-img-slot"><img src="{_slide_img}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:4px;"></div>'
                elif ctx_q:
                    ctx_slot = f'\n  <div class="context-img-slot"><span class="ctx-query">[ IMG: {ctx_q} ]</span></div>'
                else:
                    ctx_slot = ""
            else:
                ctx_slot = ""
            slides_html += f"""
<div class="slide slide-data {motion_class}">
  {corners}{clip_el}
  <div class="tag">Os Números</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>{ctx_slot}
  <div class="nums-grid">{nums_html}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "list":
            items_li = "".join(f"<li>{esc(i)}</li>" for i in slide.get("items_pt", []))
            v_hint = slide.get("visual_hint", "none")
            ctx_q = esc(slide.get("context_image_query", ""))
            _slide_img = (media_paths or {}).get("slides", {}).get(slide_i, "")
            if v_hint == "context-image":
                if _slide_img:
                    ctx_slot = f'\n  <div class="context-img-slot"><img src="{_slide_img}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:4px;"></div>'
                elif ctx_q:
                    ctx_slot = f'\n  <div class="context-img-slot"><span class="ctx-query">[ IMG: {ctx_q} ]</span></div>'
                else:
                    ctx_slot = ""
            else:
                ctx_slot = ""
            slides_html += f"""
<div class="slide slide-list {motion_class}">
  {corners}{clip_el}
  <div class="tag">Segue o fio</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>{ctx_slot}
  <ul class="item-list">{items_li}</ul>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "quote":
            v_hint = slide.get("visual_hint", "none")
            ctx_q = esc(slide.get("context_image_query", ""))
            _slide_img = (media_paths or {}).get("slides", {}).get(slide_i, "")
            if v_hint == "context-image":
                if _slide_img:
                    ctx_slot = f'\n  <div class="context-img-slot"><img src="{_slide_img}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:4px;"></div>'
                elif ctx_q:
                    ctx_slot = f'\n  <div class="context-img-slot"><span class="ctx-query">[ IMG: {ctx_q} ]</span></div>'
                else:
                    ctx_slot = ""
            elif v_hint == "bio-card":
                # Bio-card: render influencer face + name + role alongside the quote.
                # NAMED-PERSON → FACE RULE: every named person must have a visual anchor.
                bio_cards = []
                for _p in slide.get("mentioned_people", [])[:2]:
                    _name = (_p.get("name", "") if isinstance(_p, dict) else str(_p)) or ""
                    _role = (_p.get("role_pt", "") if isinstance(_p, dict) else "") or ""
                    _hint = (_p.get("image_hint", "") if isinstance(_p, dict) else "") or _name
                    _bfn = re.sub(r"[^\w]", "_", _hint.lower())[:30] + f"_s{slide_i}.jpg"
                    _bpath = _fetch_person_photo(_hint, work_dir, _bfn) if _hint else ""
                    if _bpath:
                        _card_img = f'<img class="bio-photo" src="{_bpath}" alt="{esc(_name)}" style="object-position:center top;">'
                    else:
                        _ini = "".join(w[0].upper() for w in _name.split() if w)[:2] or "?"
                        _card_img = f'<div class="bio-initials">{_ini}</div>'
                    bio_cards.append(
                        f'<div class="bio-card">{_card_img}'
                        f'<div class="bio-name">{esc(_name)}</div>'
                        f'<div class="bio-role">{esc(_role[:50])}</div>'
                        f'</div>'
                    )
                ctx_slot = f'\n  <div class="bio-grid">{"".join(bio_cards)}</div>' if bio_cards else ""
            else:
                ctx_slot = ""
            slides_html += f"""
<div class="slide slide-quote {motion_class}">
  {corners}{clip_el}
  <div class="tag">Não é opinião</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>{ctx_slot}
  <div class="quote-block">
    <div class="quote-mark">"</div>
    <div class="quote-text">{esc(slide.get("quote",""))}</div>
    <div class="quote-source">— {esc(slide.get("source",""))}</div>
  </div>
  <div class="quote-context">{esc(slide.get("context_pt",""))}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""
        elif stype == "comparison":
            left_label = esc(slide.get("left_label", "Brasil"))
            right_label = esc(slide.get("right_label", "Outros países"))
            rows_html = ""
            for it in slide.get("items", []):
                rows_html += (
                    f'<div class="comp-row">'
                    f'<div class="comp-aspect">{esc(it.get("aspect",""))}</div>'
                    f'<div class="comp-cell comp-left">{esc(it.get("left",""))}</div>'
                    f'<div class="comp-cell comp-right">{esc(it.get("right",""))}</div>'
                    f'</div>'
                )
            slides_html += f"""
<div class="slide slide-data {motion_class}">
  {corners}{clip_el}
  <div class="tag">Na prática</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="comp-header">
    <div class="comp-aspect-hdr"></div>
    <div class="comp-col-hdr comp-col-hdr-left">{left_label}</div>
    <div class="comp-col-hdr comp-col-hdr-right">{right_label}</div>
  </div>
  <div class="comp-grid">{rows_html}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""
        elif stype == "verdict":
            verdicts_html = ""
            for v in slide.get("verdicts", []):
                result_raw = v.get("result", "")
                label_raw = v.get("label", "").upper()
                # Color by VERDADEIRO/FALSO keywords first; fall back to label meaning
                # (dados-ou-agenda uses percentage result strings like "45%" — label determines color)
                if "VERDADEIRO" in result_raw.upper() or "DADOS" in label_raw or "CORRETO" in label_raw:
                    result_class = "verdict-true"
                elif "FALSO" in result_raw.upper() or "INTERESSE" in label_raw or "COMERCIAL" in label_raw:
                    result_class = "verdict-false"
                else:
                    result_class = "verdict-partial"  # Viés Ideológico and PARCIALMENTE CORRETO → yellow
                verdicts_html += (
                    f'<div class="verdict-row">'
                    f'<div class="verdict-label">{esc(v.get("label",""))}</div>'
                    f'<div class="verdict-badge {result_class}">{esc(result_raw)}</div>'
                    f'<div class="verdict-detail">{esc(v.get("detail_pt",""))}</div>'
                    f'</div>'
                )
            slides_html += f"""
<div class="slide slide-quote {motion_class}">
  {corners}{clip_el}
  <div class="tag">Veredicto</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="verdict-grid">{verdicts_html}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""
        elif stype == "timeline":
            events_html = ""
            for ev in slide.get("events", []):
                d = esc(ev.get("date", ""))
                t = esc(ev.get("text_pt", ev.get("text", "")))
                events_html += (
                    f'<div class="tl-row">'
                    f'<div class="tl-date">{d}</div>'
                    f'<div class="tl-text">{t}</div>'
                    f'</div>'
                )
            tag_label = esc(slide.get("tag_label", "A Linha do Tempo"))
            slides_html += f"""
<div class="slide slide-timeline {motion_class}">
  {corners}{clip_el}
  <div class="tag">{tag_label}</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="timeline-grid">{events_html}</div>
  <div class="swipe">SEGUE O FIO &#8594;</div>
</div>
"""

        elif stype == "network":
            bio_cards = []
            for _idx, _p in enumerate(slide.get("people", [])[:4]):
                _name = esc(_p.get("name", "") if isinstance(_p, dict) else str(_p))
                _role = esc((_p.get("role_pt", "") if isinstance(_p, dict) else "")[:50])
                _fact = esc((_p.get("fact_pt", "") if isinstance(_p, dict) else "")[:80])
                _hint = (_p.get("image_hint", "") if isinstance(_p, dict) else "") or _name
                _bfn  = re.sub(r"[^\w]", "_", str(_hint).lower())[:30] + f"_s{slide_i}_{_idx}.jpg"
                _bpath = _fetch_person_photo(_hint, work_dir, _bfn) if _hint else ""
                if _bpath:
                    _card_img = f'<img class="bio-photo" src="{_bpath}" alt="{_name}" style="object-position:center top;">'
                else:
                    _ini = "".join(w[0].upper() for w in _name.split() if w)[:2] or "?"
                    _card_img = f'<div class="bio-initials">{_ini}</div>'
                bio_cards.append(
                    f'<div class="bio-card">{_card_img}'
                    f'<div class="bio-name">{_name}</div>'
                    f'<div class="bio-role">{_role}</div>'
                    f'<div class="bio-fact">{_fact}</div>'
                    f'</div>'
                )
            net_html = f'<div class="bio-grid net-grid-2col">{"".join(bio_cards)}</div>'
            tag_label = esc(slide.get("tag_label", "Conecta os Pontos"))
            slides_html += f"""
<div class="slide slide-network {motion_class}">
  {corners}{clip_el}
  <div class="tag">{tag_label}</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {net_html}
  <div class="swipe">SEGUE O FIO &#8594;</div>
</div>
"""

        else:
            # Unknown slide type — fall back to list rendering so the slide is never silently dropped.
            items = slide.get("items_pt", slide.get("facts_pt", []))
            items_li = "".join(f"<li>{esc(it)}</li>" for it in items)
            print(f"  _build_brazil_html: unknown slide type '{stype}' — rendering as list fallback")
            slides_html += f"""
<div class="slide slide-list {motion_class}">
  {corners}{clip_el}
  <div class="tag">Saiba mais</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <ul class="item-list">{items_li}</ul>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

    src_rows = "".join(
        f'<div class="src-row"><span class="src-num">{i:02d}</span><span>{esc(s)}</span></div>\n'
        for i, s in enumerate(sources, 1)
    )
    slides_html += f"""
<div class="slide slide-sources">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Fontes</div>
  <div class="src-head">A FONTE<br>É <span class="accent">ESTA.</span></div>
  <div class="src-list">{src_rows}</div>
  <div class="cta-pt">{cta_pt}</div>
  <div class="cta-en">{cta_en}</div>
  <div class="footer-handle">{handle}</div>
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Brazil News — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,700;1,900&family=Roboto+Condensed:wght@400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
/* ── Rachadinha v2 brand spec — canonical native Brazil template ── */
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ob:#0A0A0A;--pa:#F0EBE3;--ca:#C9A84C;--gr:rgba(240,235,227,0.45);--rule:rgba(240,235,227,0.12);--W:1080px;--H:1350px;--P:{SLIDE_INSET_PX}px}}
body{{background:#111;display:flex;flex-wrap:wrap;gap:24px;padding:24px}}
.slide{{width:var(--W);height:var(--H);background:var(--ob);color:var(--pa);padding:var(--P);position:relative;overflow:hidden;flex-shrink:0;display:flex;flex-direction:column}}
/* Corner brackets */
.corner{{position:absolute;width:28px;height:28px;z-index:10}}
.corner.tl{{top:40px;left:40px;border-top:2px solid rgba(201,168,76,.6);border-left:2px solid rgba(201,168,76,.6)}}
.corner.tr{{top:40px;right:40px;border-top:2px solid rgba(201,168,76,.6);border-right:2px solid rgba(201,168,76,.6)}}
.corner.bl{{bottom:40px;left:40px;border-bottom:2px solid rgba(201,168,76,.6);border-left:2px solid rgba(201,168,76,.6)}}
.corner.br{{bottom:40px;right:40px;border-bottom:2px solid rgba(201,168,76,.6);border-right:2px solid rgba(201,168,76,.6)}}
/* Full-bleed photo background — v1 Rachadinha treatment */
.bg-photo{{position:absolute;inset:0;background-size:cover;background-position:center 20%;z-index:1;filter:grayscale(1) contrast(1.1) brightness(.55)}}
.bg-photo::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,10,10,.18) 0%,rgba(10,10,10,.78) 100%)}}
/* Halftone newspaper dot overlay */
.halftone{{position:absolute;inset:0;z-index:2;pointer-events:none;background-image:radial-gradient(circle,rgba(10,10,10,.55) 1px,transparent 1px);background-size:6px 6px}}
.slide-motion > *:not(.bg-photo):not(.halftone):not(.corner):not(.swipe):not(.footer-handle):not(.sticker-slot){{position:relative;z-index:3}}
/* Cover sticker-slot — portrait card, absolutely positioned right side */
.sticker-slot{{position:absolute;right:7%;top:18%;width:320px;height:420px;z-index:4;border:3px solid var(--ca);border-radius:4px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.7)}}
.sticker-slot img{{width:100%;height:100%;object-fit:cover;object-position:center top;filter:grayscale(1) contrast(1.15) brightness(.95)}}
/* Cover text constrained so it doesn't collide with sticker */
.cover-with-sticker .cover-hl{{max-width:54%}}
.cover-with-sticker .cover-en{{max-width:54%}}
.cover-with-sticker .cover-date{{max-width:54%}}
/* Typography */
.tag{{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--gr);margin-bottom:32px}}
.accent{{color:var(--ca)}}
.swipe{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);right:var(--P);z-index:10;letter-spacing:.08em}}
.footer-handle{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);left:var(--P);z-index:10;letter-spacing:.06em}}
/* COVER */
.slide-cover .cover-date{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);margin-bottom:44px;letter-spacing:.1em}}
.slide-cover .cover-hl{{font-family:'Playfair Display',serif;font-size:104px;font-weight:900;line-height:.93;letter-spacing:-.01em;margin-bottom:28px}}
.slide-cover .cover-hl em{{font-style:italic;color:var(--ca)}}
.slide-cover .cover-en{{font-family:'Roboto Condensed',sans-serif;font-size:32px;color:var(--gr);font-weight:400;line-height:1.35}}
/* Stamp badge — overlaps top of sticker-slot */
.stamp-badge{{position:absolute;right:calc(7% - 4px);top:calc(18% - 20px);z-index:9;background:var(--ca);color:var(--ob);font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;padding:6px 14px;border:2px solid var(--ob);transform:rotate(-1.5deg)}}
/* Person attribution pill — bottom left, replaces plain handle on cover */
.person-pill{{position:absolute;bottom:var(--P);left:var(--P);z-index:10;border:1px solid var(--ca);padding:7px 16px;font-family:'JetBrains Mono',monospace;font-size:19px;letter-spacing:.07em;text-transform:uppercase;color:var(--pa);background:rgba(10,10,10,.55)}}
.person-pill em{{color:var(--gr);font-style:normal}}
/* INNER SLIDE HEADINGS */
.slide-hl{{font-family:'Playfair Display',serif;font-size:72px;font-weight:900;line-height:.96;letter-spacing:-.01em;margin-bottom:12px}}
.slide-hl em{{font-style:italic;color:var(--ca)}}
.slide-en{{font-family:'Roboto Condensed',sans-serif;font-size:24px;color:var(--gr);font-weight:400;margin-bottom:32px;line-height:1.3}}
/* PROFILE */
.party-tag{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--ca);background:rgba(201,168,76,.08);padding:6px 14px;display:inline-block;margin-bottom:18px;letter-spacing:.12em;text-transform:uppercase}}
.profile-layout{{display:flex;gap:36px;align-items:flex-start;flex:1}}
.sticker-slot{{width:260px;min-height:340px;border:2px solid rgba(201,168,76,.35);display:flex;align-items:center;justify-content:center;flex-shrink:0;border-radius:4px;overflow:hidden}}
.sticker-photo{{background-size:cover;background-position:center top;border:none;border-radius:4px}}
.sticker-initials{{flex-direction:column;background:rgba(201,168,76,.05)}}
.bio-initials{{font-family:'Anton',sans-serif;font-size:90px;color:var(--ca);letter-spacing:.02em;line-height:1}}
.bio-init-name{{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--gr);text-align:center;margin-top:12px;text-transform:uppercase;letter-spacing:.1em;padding:0 8px}}
.fact-list{{list-style:none;flex:1}}
.fact-list li{{font-family:'Roboto Condensed',sans-serif;font-size:34px;font-weight:400;padding:14px 0;border-bottom:1px solid var(--rule);line-height:1.3}}
.fact-list li::before{{content:"▸ ";color:var(--ca)}}
/* DATA */
.nums-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;flex:1}}
.num-block{{background:rgba(201,168,76,.05);border:1px solid rgba(201,168,76,.18);padding:24px 18px;border-radius:4px}}
.num-val{{font-family:'Anton',sans-serif;font-size:76px;color:var(--ca);line-height:1;margin-bottom:8px}}
.num-label{{font-family:'Roboto Condensed',sans-serif;font-size:28px;font-weight:700;margin-bottom:4px}}
.num-en{{font-family:'Roboto Condensed',sans-serif;font-size:20px;color:var(--gr)}}
/* LIST */
.item-list{{list-style:none;flex:1}}
.item-list li{{font-family:'Roboto Condensed',sans-serif;font-size:36px;font-weight:400;padding:16px 0;border-bottom:1px solid var(--rule);line-height:1.3}}
.item-list li::before{{content:"→ ";color:var(--ca)}}
/* QUOTE */
.quote-block{{border-left:4px solid var(--ca);padding:24px 28px;margin-bottom:24px;flex:1;background:rgba(201,168,76,.04)}}
.quote-mark{{font-family:'Anton',sans-serif;font-size:80px;color:var(--ca);line-height:.7;margin-bottom:10px}}
.quote-text{{font-family:'Roboto Condensed',sans-serif;font-size:34px;line-height:1.4;margin-bottom:18px}}
.quote-source{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--ca);letter-spacing:.08em}}
.quote-context{{font-family:'Roboto Condensed',sans-serif;font-size:26px;color:var(--gr);line-height:1.4}}
/* SOURCES */
.src-head{{font-family:'Anton',sans-serif;font-size:80px;line-height:.95;text-transform:uppercase;margin-bottom:32px}}
.src-list{{flex:1}}
.src-row{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--gr);display:flex;gap:16px;padding:10px 0;border-bottom:1px solid var(--rule);line-height:1.4}}
.src-num{{color:var(--ca);flex-shrink:0;width:30px;font-weight:700}}
.cta-pt{{font-family:'Anton',sans-serif;font-size:52px;line-height:1;color:var(--ca);text-transform:uppercase;margin-top:24px}}
.cta-en{{font-family:'Roboto Condensed',sans-serif;font-size:24px;color:var(--gr);margin-top:6px}}
/* CONTEXT IMAGE SLOT (static slides) */
.context-img-slot{{min-height:280px;max-height:380px;border:2px solid rgba(201,168,76,.25);border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;margin-bottom:18px;background:rgba(10,10,10,.3);flex-shrink:0}}
.context-img-slot img{{width:100%;height:100%;object-fit:cover;display:block;filter:grayscale(.08) contrast(1.03)}}
.ctx-query{{font-family:'JetBrains Mono',monospace;font-size:16px;color:var(--ca);text-align:center;padding:16px;opacity:.7}}
/* COMPARISON slide */
.comp-header{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px}}
.comp-col-hdr{{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;text-align:center;padding:8px 0}}
.comp-col-hdr-left{{color:var(--ca)}}
.comp-col-hdr-right{{color:var(--gr)}}
.comp-aspect-hdr{{font-size:18px;color:transparent}}
.comp-grid{{flex:1;display:flex;flex-direction:column;gap:0}}
.comp-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;border-bottom:1px solid var(--rule);padding:14px 0}}
.comp-aspect{{font-family:'JetBrains Mono',monospace;font-size:18px;color:var(--gr);display:flex;align-items:center;letter-spacing:.04em}}
.comp-cell{{font-family:'Roboto Condensed',sans-serif;font-size:28px;font-weight:700;text-align:center;display:flex;align-items:center;justify-content:center;line-height:1.2}}
.comp-left{{color:var(--ca)}}
.comp-right{{color:var(--pa)}}
/* VERDICT slide */
.verdict-grid{{flex:1;display:flex;flex-direction:column;gap:24px;justify-content:center}}
.verdict-row{{border-left:4px solid var(--ca);padding:16px 20px;background:rgba(201,168,76,.04)}}
.verdict-label{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--gr);letter-spacing:.06em;margin-bottom:8px}}
.verdict-badge{{font-family:'Anton',sans-serif;font-size:32px;letter-spacing:.04em;text-transform:uppercase;margin-bottom:8px}}
.verdict-true{{color:#4ade80}}
.verdict-partial{{color:var(--ca)}}
.verdict-false{{color:#f87171}}
.verdict-detail{{font-family:'Roboto Condensed',sans-serif;font-size:28px;color:var(--pa);line-height:1.35}}
/* BIO-CARD GRID (quote slides + multi-person slides) */
.bio-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;margin:12px 0 20px}}
.bio-card{{display:flex;flex-direction:column;align-items:center;gap:6px}}
.bio-photo{{width:110px;height:130px;border-radius:4px;object-fit:cover;object-position:center top;filter:grayscale(.15) contrast(1.05)}}
.bio-card .bio-initials{{width:110px;height:130px;font-size:48px;border-radius:4px;background:rgba(201,168,76,.08);display:flex;align-items:center;justify-content:center;border:1px solid rgba(201,168,76,.3)}}
.bio-name{{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--pa);text-align:center;text-transform:uppercase;letter-spacing:.06em;line-height:1.3}}
.bio-role{{font-family:'Roboto Condensed',sans-serif;font-size:12px;color:var(--gr);text-align:center;line-height:1.2}}
/* CREDIBILITY BADGE (dados-ou-agenda cover) */
.cred-badge{{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:5px 12px;border-radius:3px;display:inline-block;margin:8px 0}}
.cred-alta{{color:#4ade80;border:1px solid rgba(74,222,128,.35);background:rgba(74,222,128,.08)}}
.cred-media{{color:var(--ca);border:1px solid rgba(201,168,76,.35);background:rgba(201,168,76,.06)}}
.cred-baixa{{color:#f87171;border:1px solid rgba(248,113,113,.35);background:rgba(248,113,113,.08)}}
/* TIMELINE slide (FORMAT-002 addition — matches original Rachadinha EP001 A Linha do Tempo) */
.timeline-grid{{display:flex;flex-direction:column;gap:0;flex:1}}
.tl-row{{display:flex;gap:24px;padding:14px 0;border-bottom:1px solid var(--rule);align-items:flex-start}}
.tl-date{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--ca);min-width:110px;flex-shrink:0;letter-spacing:.04em;padding-top:3px}}
.tl-text{{font-family:'Roboto Condensed',sans-serif;font-size:30px;color:var(--pa);line-height:1.3}}
/* NETWORK slide (FORMAT-002 addition — matches original Rachadinha EP001 Conecta os Pontos) */
.net-grid-2col{{grid-template-columns:1fr 1fr!important}}
.bio-fact{{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--ca);text-align:center;line-height:1.3;margin-top:2px;opacity:.85}}
</style>
</head>
<body>
{slides_html}
</body>
</html>"""

    html_path = Path(work_dir) / "cover.html"
    html_path.write_text(html)
    return str(html_path)


def _build_who_is_html(content, slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    """FORMAT-020 — Who Is This Person? / Quem é essa pessoa?
    Matches original Mizrachi luxury editorial design:
    Playfair Display + Cormorant Garamond + Space Mono, warm dark brown + gold (#C9A84C) palette.
    Cover: video/photo frame top + serif name bottom.
    Bio: fact grid (2-col) + controversy pills.
    Network: hub-spoke SVG diagram (up to 8 nodes).
    Content slides: clip/photo box + topic tag + title + desc.
    Last slide: sources."""

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    subject_name     = content.get("subject_name", "")
    subject_title    = esc(content.get("subject_title", ""))
    series_label     = esc(content.get("series_label", "Quem É Essa Pessoa?"))
    hub_initials     = esc(content.get("hub_initials", "??"))
    bio_tag          = esc(content.get("bio_tag", "Biografia · Biography"))
    bio_heading      = esc(content.get("bio_heading", "Quem é"))
    _name_parts = subject_name.split()
    _default_em = (esc(_name_parts[-1]) + "?") if _name_parts else "?"
    bio_heading_em   = esc(content.get("bio_heading_em", _default_em))
    bio_facts        = content.get("bio_facts", [])
    controversy_tag  = esc(content.get("controversy_tag", "Tópicos Polêmicos"))
    controversies    = content.get("controversies", [])
    network_tag      = esc(content.get("network_tag", "Rede & Conexões"))
    network_title    = esc(content.get("network_title", "Pessoas Poderosas"))
    network_title_em = esc(content.get("network_title_em", "& Famosas"))
    network_nodes    = content.get("network", [])
    sources          = content.get("sources", [])
    cover_url        = esc(content.get("cover_url", ""))
    subject_name_esc = esc(subject_name)

    cover_img  = (media_paths or {}).get("cover", "")
    clips      = (media_paths or {}).get("clips", {})

    if cover_img:
        vframe_inner = (
            f'<div style="position:absolute;inset:0;background-image:url(\'{cover_img}\');'
            f'background-size:cover;background-position:center 15%;'
            f'filter:grayscale(.1) contrast(1.05);"></div>'
            f'<div style="position:absolute;inset:0;background:linear-gradient(180deg,'
            f'transparent 50%,rgba(0,0,0,0.65) 100%);pointer-events:none;"></div>'
        )
    else:
        vframe_inner = (
            f'<div class="play"></div>'
            f'<div class="vlabel">ASSISTIR REEL COMPLETO</div>'
            f'<div class="vurl">{cover_url}</div>'
        )

    name_parts = subject_name.split()
    if len(name_parts) >= 2:
        first = esc(" ".join(name_parts[:-1]))
        last  = esc(name_parts[-1])
        name_html = f'{first}<em>{last}</em>'
    else:
        name_html = f'<em>{subject_name_esc}</em>'

    bio_grid_html = ""
    for fact in bio_facts[:6]:
        lpt  = esc(fact.get("label_pt", ""))
        len_ = esc(fact.get("label_en", ""))
        val  = esc(fact.get("value", ""))
        lbl  = f"{lpt} · {len_}" if len_ else lpt
        bio_grid_html += (
            f'<div class="gitem">'
            f'<span class="glabel">{lbl}</span>'
            f'<span class="gval">{val}</span>'
            f'</div>'
        )

    pills_html = "".join(
        f'<div class="pill">{esc(c)}</div>' for c in controversies
    )

    NODE_POS = ["n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8"]
    ARROW_COORDS = [
        ("480", "400", "480", "220"),
        ("530", "420", "688", "298"),
        ("580", "500", "760", "500"),
        ("530", "580", "688", "708"),
        ("480", "600", "480", "760"),
        ("430", "580", "272", "708"),
        ("380", "500", "200", "500"),
        ("430", "420", "272", "298"),
    ]
    arrows_svg = ""
    nodes_html = ""
    for i, node in enumerate(network_nodes[:8]):
        x1, y1, x2, y2 = ARROW_COORDS[i]
        arrows_svg += (
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="rgba(201,168,76,0.38)" stroke-width="1.5" '
            f'stroke-dasharray="7,4" marker-end="url(#arr)"/>'
        )
        ni   = esc(node.get("initials", ""))
        nn   = esc(node.get("name", ""))
        nt   = esc(node.get("title", ""))
        pos  = NODE_POS[i]
        nodes_html += (
            f'<div class="node {pos}">'
            f'<div class="avatar">{ni}</div>'
            f'<div class="nname">{nn}</div>'
            f'<div class="ntitle">{nt}</div>'
            f'</div>'
        )

    BAR_GRADIENTS = [
        "linear-gradient(90deg,#C9A84C,#8B1A1A)",
        "linear-gradient(90deg,#1A3A5C,#C9A84C)",
        "linear-gradient(90deg,#C9A84C,#2A5A1A)",
        "linear-gradient(90deg,#5A1A5A,#C9A84C)",
        "linear-gradient(90deg,#8B1A1A,#C9A84C)",
        "linear-gradient(90deg,#C9A84C,#1A3A5C)",
    ]
    slide_list = content.get("slides", [])
    content_slides_html = ""
    for si, sl in enumerate(slide_list):
        slide_num   = si + 4
        bar_grad    = BAR_GRADIENTS[si % len(BAR_GRADIENTS)]
        count_label = f"{si + 1:02d}/{len(slide_list):02d}"
        topic       = esc(sl.get("topic", ""))
        title       = esc(sl.get("title", ""))
        title_em    = esc(sl.get("title_italic", sl.get("title_em", "")))
        desc        = esc(sl.get("desc", sl.get("desc_pt", "")))
        url         = esc(sl.get("url", ""))
        clip_img    = clips.get(slide_num, "")
        if clip_img:
            vbox_inner = (
                f'<div style="position:absolute;inset:0;background-image:url(\'{clip_img}\');'
                f'background-size:cover;background-position:center;'
                f'filter:grayscale(.05) contrast(1.05);"></div>'
            )
        else:
            vbox_inner = (
                f'<div class="vplay"></div>'
                f'<div class="vlbl">Assistir Reel</div>'
                f'<div class="vlink">{url}</div>'
            )
        title_html = f"{title}<br><em>{title_em}</em>" if title_em else title
        content_slides_html += f"""
<div class="slide vs">
  <div class="topbar" style="background:{bar_grad}"></div>
  <div class="bign">{slide_num:02d}</div>
  <div class="vtag">Reel <b>{count_label}</b></div>
  <div class="vbox">{vbox_inner}</div>
  <div class="vbot">
    <div class="vtopic">{topic}</div>
    <div class="vtitle">{title_html}</div>
    <div class="vdesc">{desc}</div>
  </div>
  <div class="vcorner"></div>
</div>
"""

    src_rows = "".join(
        f'<div class="src-item">'
        f'<span class="src-num">{i:02d}</span>'
        f'<span class="src-text">{esc(s)}</span>'
        f'</div>\n'
        for i, s in enumerate(sources, 1)
    )
    sources_slide = f"""
<div class="slide s2">
  <div class="lbar"></div>
  <div class="head">
    <span class="htag">Fontes · Sources</span>
    <div class="htitle">A Fonte<em>É Esta.</em></div>
  </div>
  <div class="src-grid">{src_rows}</div>
  <div class="vbot-src">
    <div class="pill" style="border-color:rgba(201,168,76,0.4);color:#C9A84C;">Salva · Save this</div>
  </div>
</div>
"""

    full_html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Quem É — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700&family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
/* FORMAT-020 — Who Is This Person? — matches original Mizrachi editorial design */
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--gold:#C9A84C;--gold-l:#E8CC7A;--cream:#F5EDD6;--dark:#0C0A07;--dark2:#141209;--dark3:#1C1810;--mid:#2A2318;--text:#E8E0CC;--muted:#8A7E68;--red:#8B1A1A;--blue:#1A3A5C}}
body{{background:#050505;display:flex;flex-direction:column;align-items:center;font-family:'Cormorant Garamond',serif}}
.slide{{width:1080px;height:1350px;position:relative;overflow:hidden;flex-shrink:0}}
.s1{{background:var(--dark)}}
.s1 .noise{{position:absolute;inset:0;background:radial-gradient(ellipse 100% 50% at 50% 0%,rgba(201,168,76,0.13) 0%,transparent 55%),repeating-linear-gradient(0deg,transparent 0px,transparent 79px,rgba(201,168,76,0.025) 79px,rgba(201,168,76,0.025) 80px)}}
.s1 .corners span{{position:absolute;width:100px;height:100px;border-color:rgba(201,168,76,0.45);border-style:solid;display:block}}
.s1 .corners span:nth-child(1){{top:44px;left:44px;border-width:2px 0 0 2px}}
.s1 .corners span:nth-child(2){{top:44px;right:44px;border-width:2px 2px 0 0}}
.s1 .corners span:nth-child(3){{bottom:44px;left:44px;border-width:0 0 2px 2px}}
.s1 .corners span:nth-child(4){{bottom:44px;right:44px;border-width:0 2px 2px 0}}
.s1 .tag{{position:absolute;top:84px;left:0;right:0;text-align:center;font-family:'Space Mono',monospace;font-size:11px;letter-spacing:0.38em;color:var(--gold);text-transform:uppercase;opacity:0.75}}
.s1 .vframe{{position:absolute;top:148px;left:80px;width:920px;height:750px;background:#080604;border:1.5px solid rgba(201,168,76,0.28);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;overflow:hidden}}
.s1 .vframe::after{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 50% 35% at 50% 25%,rgba(201,168,76,0.07) 0%,transparent 65%),linear-gradient(180deg,transparent 40%,rgba(0,0,0,0.7) 100%);pointer-events:none}}
.s1 .play{{width:88px;height:88px;border-radius:50%;border:1.5px solid var(--gold);background:rgba(12,10,7,0.72);display:flex;align-items:center;justify-content:center;position:relative;z-index:2}}
.s1 .play::after{{content:'\\25B6';color:var(--gold);font-size:26px;margin-left:5px}}
.s1 .vlabel{{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.32em;color:var(--gold-l);text-transform:uppercase;position:relative;z-index:2}}
.s1 .vurl{{font-family:'Space Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:0.12em;position:relative;z-index:2}}
.s1 .bottom{{position:absolute;bottom:72px;left:80px}}
.s1 .gline{{width:52px;height:1px;background:var(--gold);margin-bottom:24px}}
.s1 .title{{font-family:'Playfair Display',serif;font-size:68px;font-weight:900;color:var(--cream);line-height:1.0}}
.s1 .title em{{color:var(--gold);font-style:italic;display:block}}
.s1 .sub{{margin-top:14px;font-family:'Cormorant Garamond',serif;font-size:23px;font-weight:300;font-style:italic;color:var(--muted)}}
.s2{{background:var(--dark2)}}
.s2 .lbar{{position:absolute;top:0;left:0;width:7px;height:100%;background:linear-gradient(180deg,var(--gold) 0%,var(--red) 50%,var(--gold) 100%)}}
.s2 .head{{position:absolute;top:80px;left:80px}}
.s2 .htag{{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.42em;color:var(--gold);text-transform:uppercase;margin-bottom:18px;display:block}}
.s2 .htitle{{font-family:'Playfair Display',serif;font-size:76px;font-weight:900;color:var(--cream);line-height:0.92}}
.s2 .htitle em{{color:var(--gold);font-style:italic;font-size:82px;display:block}}
.s2 .divrow{{position:absolute;top:350px;left:80px;display:flex;align-items:center;gap:14px;width:480px}}
.s2 .divrow span{{flex:1;height:1px;background:rgba(201,168,76,0.25);display:block}}
.s2 .divrow i{{width:7px;height:7px;background:var(--gold);transform:rotate(45deg);flex-shrink:0;display:block}}
.s2 .grid{{position:absolute;top:400px;left:80px;width:920px;display:grid;grid-template-columns:1fr 1fr;gap:28px 56px}}
.s2 .glabel{{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:0.36em;color:var(--gold);text-transform:uppercase;opacity:0.8;margin-bottom:7px;display:block}}
.s2 .gval{{font-family:'Cormorant Garamond',serif;font-size:20px;color:var(--text);line-height:1.45}}
.s2 .polem{{position:absolute;bottom:72px;left:80px;right:80px}}
.s2 .ptag{{font-family:'Space Mono',monospace;font-size:9px;letter-spacing:0.4em;color:var(--red);text-transform:uppercase;margin-bottom:18px;display:flex;align-items:center;gap:10px}}
.s2 .ptag::before{{content:'\\26A0';font-size:13px}}
.s2 .pills{{display:flex;flex-wrap:wrap;gap:10px}}
.s2 .pill{{padding:9px 18px;border:1px solid rgba(139,26,26,0.45);font-family:'Cormorant Garamond',serif;font-size:17px;font-style:italic;color:#C87070;background:rgba(139,26,26,0.07);line-height:1}}
.src-grid{{position:absolute;top:400px;left:80px;width:920px;display:flex;flex-direction:column;gap:0}}
.src-item{{display:flex;gap:20px;padding:14px 0;border-bottom:1px solid rgba(201,168,76,0.12);align-items:start}}
.src-num{{font-family:'Space Mono',monospace;font-size:14px;color:var(--gold);font-weight:700;flex-shrink:0;width:28px}}
.src-text{{font-family:'Cormorant Garamond',serif;font-size:19px;color:var(--text);line-height:1.45}}
.vbot-src{{position:absolute;bottom:72px;left:80px}}
.s3{{background:var(--dark3)}}
.s3 .head3{{position:absolute;top:56px;left:80px}}
.s3 .h3tag{{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.4em;color:var(--gold);text-transform:uppercase;margin-bottom:12px;display:block}}
.s3 .h3title{{font-family:'Playfair Display',serif;font-size:54px;font-weight:900;color:var(--cream);line-height:1.0}}
.s3 .h3title em{{color:var(--gold);font-style:italic}}
.s3 .collage{{position:absolute;top:220px;left:60px;width:960px;height:1000px}}
.s3 .hub{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:200px;height:200px;border-radius:50%;border:2.5px solid var(--gold);background:var(--mid);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:10;box-shadow:0 0 0 10px rgba(201,168,76,0.08),0 0 50px rgba(201,168,76,0.12)}}
.s3 .hub-init{{font-family:'Playfair Display',serif;font-size:58px;font-weight:900;color:var(--gold);line-height:1}}
.s3 .hub-name{{font-family:'Cormorant Garamond',serif;font-size:12px;color:var(--muted);font-style:italic;text-align:center;padding:0 14px;margin-top:2px}}
.s3 .node{{position:absolute;display:flex;flex-direction:column;align-items:center;width:140px;margin-left:-70px;margin-top:-70px}}
.s3 .avatar{{width:110px;height:110px;border-radius:50%;border:2px solid rgba(201,168,76,0.35);background:var(--mid);display:flex;align-items:center;justify-content:center;font-family:'Playfair Display',serif;font-size:32px;font-weight:700;color:var(--gold)}}
.s3 .nname{{font-family:'Cormorant Garamond',serif;font-size:15px;font-weight:600;color:var(--cream);text-align:center;line-height:1.2;margin-top:8px}}
.s3 .ntitle{{font-family:'Space Mono',monospace;font-size:8px;letter-spacing:0.18em;color:var(--muted);text-align:center;margin-top:3px;text-transform:uppercase}}
.s3 .n1{{left:480px;top:70px}}.s3 .n2{{left:728px;top:148px}}.s3 .n3{{left:830px;top:430px}}
.s3 .n4{{left:728px;top:718px}}.s3 .n5{{left:480px;top:810px}}.s3 .n6{{left:232px;top:718px}}
.s3 .n7{{left:130px;top:430px}}.s3 .n8{{left:232px;top:148px}}
.s3 .arrows{{position:absolute;inset:0;width:960px;height:1000px;pointer-events:none;z-index:5}}
.vs{{position:relative;background:var(--dark)}}
.vs .topbar{{position:absolute;top:0;left:0;right:0;height:6px}}
.vs .bign{{position:absolute;top:20px;right:60px;font-family:'Playfair Display',serif;font-size:220px;font-weight:900;color:rgba(201,168,76,0.05);line-height:1}}
.vs .vtag{{position:absolute;top:64px;left:80px;font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.35em;color:var(--muted);text-transform:uppercase}}
.vs .vtag b{{color:var(--gold);font-weight:400}}
.vs .vbox{{position:absolute;top:140px;left:80px;width:920px;height:760px;border:1.5px solid rgba(201,168,76,0.22);background:#080604;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;overflow:hidden}}
.vs .vbox::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 50%,rgba(0,0,0,0.8) 100%)}}
.vs .vplay{{width:76px;height:76px;border-radius:50%;border:1.5px solid var(--gold);background:rgba(12,10,7,0.72);display:flex;align-items:center;justify-content:center;position:relative;z-index:2}}
.vs .vplay::after{{content:'\\25B6';color:var(--gold);font-size:22px;margin-left:4px}}
.vs .vlbl{{font-family:'Space Mono',monospace;font-size:10px;letter-spacing:0.3em;color:var(--gold-l);text-transform:uppercase;position:relative;z-index:2}}
.vs .vlink{{font-family:'Space Mono',monospace;font-size:9px;color:var(--muted);position:relative;z-index:2}}
.vs .vbot{{position:absolute;bottom:72px;left:80px;width:860px}}
.vs .vtopic{{display:inline-block;padding:6px 16px;border:1px solid rgba(201,168,76,0.38);font-family:'Space Mono',monospace;font-size:9px;letter-spacing:0.28em;color:var(--gold);text-transform:uppercase;margin-bottom:14px}}
.vs .vtitle{{font-family:'Playfair Display',serif;font-size:46px;font-weight:700;color:var(--cream);line-height:1.1;margin-bottom:10px}}
.vs .vtitle em{{color:var(--gold);font-style:italic}}
.vs .vdesc{{font-family:'Cormorant Garamond',serif;font-size:20px;font-weight:300;font-style:italic;color:var(--muted);line-height:1.45}}
.vs .vcorner{{position:absolute;bottom:72px;right:80px;width:52px;height:52px;border-right:1.5px solid rgba(201,168,76,0.28);border-bottom:1.5px solid rgba(201,168,76,0.28)}}
</style>
</head>
<body>
<div class="slide s1">
  <div class="noise"></div>
  <div class="corners"><span></span><span></span><span></span><span></span></div>
  <div class="tag">{series_label}</div>
  <div class="vframe">{vframe_inner}</div>
  <div class="bottom">
    <div class="gline"></div>
    <div class="title">{name_html}</div>
    <div class="sub">{subject_title}</div>
  </div>
</div>
<div class="slide s2">
  <div class="lbar"></div>
  <div class="head">
    <span class="htag">{bio_tag}</span>
    <div class="htitle">{bio_heading}<em>{bio_heading_em}</em></div>
  </div>
  <div class="divrow"><span></span><i></i><span></span></div>
  <div class="grid">{bio_grid_html}</div>
  <div class="polem">
    <div class="ptag">{controversy_tag}</div>
    <div class="pills">{pills_html}</div>
  </div>
</div>
<div class="slide s3">
  <div class="head3">
    <span class="h3tag">{network_tag}</span>
    <div class="h3title">{network_title} <em>{network_title_em}</em></div>
  </div>
  <div class="collage">
    <svg class="arrows" viewBox="0 0 960 1000" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arr" markerWidth="7" markerHeight="5" refX="7" refY="2.5" orient="auto"><polygon points="0 0,7 2.5,0 5" fill="rgba(201,168,76,0.55)"/></marker></defs>
      {arrows_svg}
    </svg>
    <div class="hub"><div class="hub-init">{hub_initials}</div><div class="hub-name">{subject_name_esc}</div></div>
    {nodes_html}
  </div>
</div>
{content_slides_html}{sources_slide}
</body>
</html>"""

    html_path = Path(work_dir) / "cover.html"
    html_path.write_text(full_html)
    return str(html_path)

def _build_the_case_html(content, slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    """FORMAT-021 — O Caso / The Case: topic/case-centric investigation carousel.
    The CASE is the subject. A key person appears as context, not the main subject.
    Cover: case title + status pill + person attribution + hook.
    Slides: timeline, responsible, person_bg, network, money, list, quote, sources."""

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    case_title   = esc(content.get("case_title", "O CASO"))
    case_title_en = esc(content.get("case_title_en", "THE CASE"))
    case_status  = esc(content.get("case_status", ""))
    person_name  = esc(content.get("person_name", ""))
    person_party = esc(content.get("person_party", ""))
    hook_pt      = esc(content.get("hook_pt", ""))
    hook_en      = esc(content.get("hook_en", ""))
    cta_pt       = esc(content.get("cta_pt", "Salva pra não esquecer."))
    cta_en       = esc(content.get("cta_en", "Save this."))
    sources      = content.get("sources", [])
    series_label = esc(content.get("series_label", "O Caso"))

    clips        = (media_paths or {}).get("clips", {})
    cover_img    = (media_paths or {}).get("cover", "")
    slide_photos = (media_paths or {}).get("slide_photos", {})

    cover_bg_el = (
        f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
        f'<div class="halftone"></div>'
    ) if cover_img else ""

    if cover_img:
        cover_sticker_el = f'<div class="sticker-slot"><img src="{cover_img}" alt="cover portrait"></div>'
        cover_sticker_class = "cover-with-sticker"
    elif person_name:
        _ini = "".join(w[0].upper() for w in person_name.split() if w)[:2]
        cover_sticker_el = (
            f'<div class="sticker-slot sticker-initials">'
            f'<div class="bio-initials">{_ini}</div>'
            f'<div class="bio-init-name">{person_name}</div>'
            f'</div>'
        )
        cover_sticker_class = "cover-with-sticker"
    else:
        cover_sticker_el = ""
        cover_sticker_class = ""

    case_status_el = (
        f'<div class="case-status-pill">{case_status}</div>'
    ) if case_status else ""

    person_attr_parts = [person_name] + ([person_party] if person_party else [])
    person_attr_el = (
        f'<div class="cover-person-attr">via {" · ".join(person_attr_parts)}</div>'
    ) if person_name else ""

    corners = '<div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>'

    slides_html = f"""
<div class="slide slide-cover slide-motion {cover_sticker_class}">
  {corners}
  {cover_bg_el}
  {cover_sticker_el}
  <div class="tag">{series_label}</div>
  {case_status_el}
  <div class="case-title">{case_title}</div>
  <div class="case-title-en">{case_title_en}</div>
  {person_attr_el}
  <div class="hook-pt">{hook_pt}</div>
  <div class="swipe">SEGUE O FIO &#8594;</div>
  <div class="footer-handle">{handle}</div>
</div>
"""

    for slide_i, slide in enumerate(content.get("slides", []), start=2):
        stype = slide.get("type", "list")
        h_pt  = esc(slide.get("heading_pt", ""))
        h_en  = esc(slide.get("heading_en", ""))
        is_motion_slide = (slide_i % 2 == 1)
        motion_class = "slide-motion" if is_motion_slide else ""
        slide_photo = (
            slide_photos.get(slide_i, "")
            or clips.get(slide_i, "")
            or (media_paths or {}).get("slides", {}).get(slide_i, "")
        )
        clip_el = ""
        if is_motion_slide and slide_photo:
            clip_el = (
                f'<div class="bg-photo" style="background-image:url(\'{slide_photo}\');"></div>'
                f'<div style="position:absolute;inset:0;background:rgba(0,0,0,0.55);'
                f'border-radius:inherit;z-index:2;pointer-events:none;"></div>'
                f'<div class="halftone"></div>'
            )

        if stype == "timeline":
            events_html = ""
            for ev in slide.get("events", []):
                d = esc(ev.get("date", ""))
                t = esc(ev.get("text_pt", ev.get("text", "")))
                events_html += f'<div class="tl-row"><div class="tl-date">{d}</div><div class="tl-text">{t}</div></div>'
            slides_html += f"""
<div class="slide slide-timeline {motion_class}">
  {corners}{clip_el}
  <div class="tag">A Linha do Tempo</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="timeline-grid">{events_html}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "responsible":
            institution = esc(slide.get("institution", ""))
            decision_date = esc(slide.get("decision_date", ""))
            impact_pt = esc(slide.get("impact_pt", ""))
            impact_en = esc(slide.get("impact_en", ""))
            ctx_q = esc(slide.get("context_image_query", ""))
            _simg = slide_photo
            if _simg:
                resp_slot = f'<div class="context-img-slot"><img src="{_simg}" alt=""></div>'
            elif ctx_q:
                resp_slot = f'<div class="context-img-slot"><span class="ctx-query">[ IMG: {ctx_q} ]</span></div>'
            else:
                resp_slot = ""
            slides_html += f"""
<div class="slide slide-responsible {motion_class}">
  {corners}{clip_el}
  <div class="tag">Quem Decidiu</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {resp_slot}
  <div class="resp-institution">{institution}</div>
  <div class="resp-date">{decision_date}</div>
  <div class="resp-impact">{impact_pt}</div>
  <div class="resp-impact-en">{impact_en}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "person_bg":
            bg_name  = esc(slide.get("name", content.get("person_name", "")))
            bg_role  = esc(slide.get("role_pt", ""))
            bg_party = esc(slide.get("party", content.get("person_party", "")))
            bg_facts = slide.get("facts_pt", [])
            facts_li = "".join(f"<li>{esc(f)}</li>" for f in bg_facts)
            _hint = slide.get("image_hint", "") or bg_name
            _bfn  = re.sub(r"[^\w]", "_", str(_hint).lower())[:30] + f"_s{slide_i}.jpg"
            _bpath = _fetch_person_photo(_hint, work_dir, _bfn) if _hint else ""
            if _bpath:
                _sticker_el = (
                    f'<div class="person-sticker" '
                    f'style="background-image:url(\'{_bpath}\')"></div>'
                )
            else:
                _ini = "".join(w[0].upper() for w in bg_name.split() if w)[:2] or "?"
                _sticker_el = (
                    f'<div class="person-sticker person-sticker-initials">'
                    f'<div class="bio-initials">{_ini}</div>'
                    f'<div class="bio-init-name">{bg_name}</div>'
                    f'</div>'
                )
            party_el = f'<div class="party-tag">{bg_party}</div>' if bg_party else ""
            slides_html += f"""
<div class="slide slide-person-bg {motion_class}">
  {corners}{clip_el}
  <div class="tag">Quem É</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {party_el}
  <div class="profile-layout">
    {_sticker_el}
    <div><div class="bio-role-lg">{bg_role}</div><ul class="fact-list">{facts_li}</ul></div>
  </div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "network":
            bio_cards = []
            for _idx, _p in enumerate(slide.get("people", [])[:6]):
                _name = esc(_p.get("name", "") if isinstance(_p, dict) else str(_p))
                _role = esc((_p.get("role_pt", "") if isinstance(_p, dict) else "")[:50])
                _conn = esc((_p.get("connection_pt", "") if isinstance(_p, dict) else "")[:60])
                _hint = (_p.get("image_hint", "") if isinstance(_p, dict) else "") or _name
                _bfn  = re.sub(r"[^\w]", "_", str(_hint).lower())[:30] + f"_s{slide_i}_{_idx}.jpg"
                _bpath = _fetch_person_photo(_hint, work_dir, _bfn) if _hint else ""
                if _bpath:
                    _card_img = f'<img class="bio-photo" src="{_bpath}" alt="{_name}" style="object-position:center top;">'
                else:
                    _ini = "".join(w[0].upper() for w in _name.split() if w)[:2] or "?"
                    _card_img = f'<div class="bio-initials">{_ini}</div>'
                bio_cards.append(
                    f'<div class="bio-card">{_card_img}'
                    f'<div class="bio-name">{_name}</div>'
                    f'<div class="bio-role">{_role}</div>'
                    f'<div class="bio-conn">{_conn}</div>'
                    f'</div>'
                )
            net_grid = f'<div class="bio-grid net-grid">{"".join(bio_cards)}</div>'
            slides_html += f"""
<div class="slide slide-network {motion_class}">
  {corners}{clip_el}
  <div class="tag">A Rede</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  {net_grid}
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "money":
            nums_html = ""
            for n in slide.get("numbers", [])[:4]:
                nums_html += (
                    f'<div class="num-block">'
                    f'<div class="num-val">{esc(n.get("value", ""))}</div>'
                    f'<div class="num-label">{esc(n.get("label_pt", ""))}</div>'
                    f'<div class="num-en">{esc(n.get("label_en", ""))}</div>'
                    f'</div>'
                )
            slides_html += f"""
<div class="slide slide-money {motion_class}">
  {corners}{clip_el}
  <div class="tag">O Rastro do Dinheiro</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="nums-grid">{nums_html}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        elif stype == "quote":
            slides_html += f"""
<div class="slide slide-quote {motion_class}">
  {corners}{clip_el}
  <div class="tag">Não é opinião</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="quote-block">
    <div class="quote-mark">"</div>
    <div class="quote-text">{esc(slide.get("quote", ""))}</div>
    <div class="quote-source">— {esc(slide.get("source", ""))}</div>
  </div>
  <div class="quote-context">{esc(slide.get("context_pt", ""))}</div>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

        else:
            items = slide.get("items_pt", slide.get("facts_pt", []))
            items_li = "".join(f"<li>{esc(it)}</li>" for it in items)
            if stype != "list":
                print(f"  _build_the_case_html: unknown slide type '{stype}' — rendering as list fallback")
            slides_html += f"""
<div class="slide slide-list {motion_class}">
  {corners}{clip_el}
  <div class="tag">Segue o fio</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <ul class="item-list">{items_li}</ul>
  <div class="swipe">SWIPE &#8594;</div>
</div>
"""

    src_rows = "".join(
        f'<div class="src-row"><span class="src-num">{i:02d}</span><span>{esc(s)}</span></div>\n'
        for i, s in enumerate(sources, 1)
    )
    slides_html += f"""
<div class="slide slide-sources">
  {corners}
  <div class="tag">Fontes</div>
  <div class="src-head">A FONTE<br>É <span class="accent">ESTA.</span></div>
  <div class="src-list">{src_rows}</div>
  <div class="cta-pt">{cta_pt}</div>
  <div class="cta-en">{cta_en}</div>
  <div class="footer-handle">{handle}</div>
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>O Caso — {slug}</title>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@400;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
/* FORMAT-021 O Caso — topic/case-centric investigation carousel */
/* Inherits Rachadinha v2 brand spec (Canário dark editorial palette) */
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ob:#0A0A0A;--pa:#F0EBE3;--ca:#C9A84C;--gr:rgba(240,235,227,0.45);--rule:rgba(240,235,227,0.12);--W:1080px;--H:1350px;--P:{SLIDE_INSET_PX}px}}
body{{background:#111;display:flex;flex-wrap:wrap;gap:24px;padding:24px}}
.slide{{width:var(--W);height:var(--H);background:var(--ob);color:var(--pa);padding:var(--P);position:relative;overflow:hidden;flex-shrink:0;display:flex;flex-direction:column}}
.corner{{position:absolute;width:28px;height:28px;z-index:10}}
.corner.tl{{top:40px;left:40px;border-top:2px solid rgba(201,168,76,.6);border-left:2px solid rgba(201,168,76,.6)}}
.corner.tr{{top:40px;right:40px;border-top:2px solid rgba(201,168,76,.6);border-right:2px solid rgba(201,168,76,.6)}}
.corner.bl{{bottom:40px;left:40px;border-bottom:2px solid rgba(201,168,76,.6);border-left:2px solid rgba(201,168,76,.6)}}
.corner.br{{bottom:40px;right:40px;border-bottom:2px solid rgba(201,168,76,.6);border-right:2px solid rgba(201,168,76,.6)}}
.bg-photo{{position:absolute;inset:0;background-size:cover;background-position:center 20%;z-index:1;filter:grayscale(1) contrast(1.1) brightness(.55)}}
.bg-photo::after{{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(10,10,10,.18) 0%,rgba(10,10,10,.78) 100%)}}
.halftone{{position:absolute;inset:0;z-index:2;pointer-events:none;background-image:radial-gradient(circle,rgba(10,10,10,.55) 1px,transparent 1px);background-size:6px 6px}}
.slide-motion > *:not(.bg-photo):not(.halftone):not(.corner):not(.swipe):not(.footer-handle):not(.sticker-slot){{position:relative;z-index:3}}
/* Cover sticker — absolutely positioned right side (cover slide only) */
.sticker-slot{{position:absolute;right:7%;top:18%;width:300px;height:390px;z-index:4;border:3px solid var(--ca);border-radius:4px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.7)}}
.sticker-slot img{{width:100%;height:100%;object-fit:cover;object-position:center top;filter:grayscale(1) contrast(1.15) brightness(.95)}}
.sticker-initials{{display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(201,168,76,.05)}}
.cover-with-sticker .case-title{{max-width:56%}}
.cover-with-sticker .case-title-en{{max-width:56%}}
.cover-with-sticker .hook-pt{{max-width:56%}}
.cover-with-sticker .cover-person-attr{{max-width:56%}}
/* Global typography */
.tag{{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--gr);margin-bottom:24px}}
.accent{{color:var(--ca)}}
.swipe{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);right:var(--P);z-index:10;letter-spacing:.08em}}
.footer-handle{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);left:var(--P);z-index:10;letter-spacing:.06em}}
/* COVER */
.case-status-pill{{font-family:'JetBrains Mono',monospace;font-size:18px;color:var(--ca);background:rgba(201,168,76,.08);border:1px solid rgba(201,168,76,.3);padding:5px 14px;display:inline-block;margin-bottom:20px;letter-spacing:.12em;text-transform:uppercase;border-radius:2px}}
.case-title{{font-family:'Anton',sans-serif;font-size:100px;line-height:.95;text-transform:uppercase;letter-spacing:-.01em;margin-bottom:16px}}
.case-title-en{{font-family:'Roboto Condensed',sans-serif;font-size:30px;color:var(--gr);font-weight:400;line-height:1.3;margin-bottom:20px}}
.cover-person-attr{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--ca);letter-spacing:.08em;margin-bottom:20px;text-transform:uppercase}}
.hook-pt{{font-family:'Roboto Condensed',sans-serif;font-size:36px;line-height:1.35;color:var(--pa);font-weight:400}}
/* INNER SLIDE HEADINGS */
.slide-hl{{font-family:'Anton',sans-serif;font-size:72px;line-height:.98;text-transform:uppercase;letter-spacing:-.01em;margin-bottom:12px}}
.slide-en{{font-family:'Roboto Condensed',sans-serif;font-size:24px;color:var(--gr);font-weight:400;margin-bottom:32px;line-height:1.3}}
/* TIMELINE */
.timeline-grid{{flex:1;display:flex;flex-direction:column;gap:0;overflow:hidden}}
.tl-row{{display:grid;grid-template-columns:160px 1fr;gap:20px;border-bottom:1px solid var(--rule);padding:18px 0;align-items:start}}
.tl-date{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--ca);font-weight:700;letter-spacing:.06em;flex-shrink:0}}
.tl-text{{font-family:'Roboto Condensed',sans-serif;font-size:34px;line-height:1.3;color:var(--pa)}}
/* RESPONSIBLE PARTY */
.resp-institution{{font-family:'Anton',sans-serif;font-size:52px;line-height:1;color:var(--ca);text-transform:uppercase;margin-bottom:14px}}
.resp-date{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);margin-bottom:22px;letter-spacing:.08em}}
.resp-impact{{font-family:'Roboto Condensed',sans-serif;font-size:38px;line-height:1.35;font-weight:700;margin-bottom:8px}}
.resp-impact-en{{font-family:'Roboto Condensed',sans-serif;font-size:24px;color:var(--gr)}}
/* PERSON BACKGROUND — inline sticker (not absolutely positioned) */
.profile-layout{{display:flex;gap:36px;align-items:flex-start;flex:1}}
.person-sticker{{width:240px;min-height:310px;flex-shrink:0;border:2px solid rgba(201,168,76,.35);border-radius:4px;overflow:hidden;background-size:cover;background-position:center top;filter:grayscale(.05) contrast(1.05)}}
.person-sticker-initials{{display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(201,168,76,.05)}}
.bio-role-lg{{font-family:'Roboto Condensed',sans-serif;font-size:30px;color:var(--ca);font-weight:700;margin-bottom:18px;line-height:1.2}}
.bio-initials{{font-family:'Anton',sans-serif;font-size:80px;color:var(--ca);letter-spacing:.02em;line-height:1}}
.bio-init-name{{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--gr);text-align:center;margin-top:10px;text-transform:uppercase;letter-spacing:.1em;padding:0 6px}}
.fact-list{{list-style:none;flex:1}}
.fact-list li{{font-family:'Roboto Condensed',sans-serif;font-size:34px;font-weight:400;padding:14px 0;border-bottom:1px solid var(--rule);line-height:1.3}}
.fact-list li::before{{content:"▸ ";color:var(--ca)}}
.party-tag{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--ca);background:rgba(201,168,76,.08);padding:6px 14px;display:inline-block;margin-bottom:18px;letter-spacing:.12em;text-transform:uppercase}}
/* NETWORK */
.bio-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;margin:12px 0 20px;flex:1;align-content:start}}
.net-grid{{grid-template-columns:repeat(3,1fr)}}
.bio-card{{display:flex;flex-direction:column;align-items:center;gap:4px}}
.bio-photo{{width:110px;height:130px;border-radius:4px;object-fit:cover;object-position:center top;filter:grayscale(.15) contrast(1.05)}}
.bio-card .bio-initials{{width:110px;height:130px;font-size:48px;border-radius:4px;background:rgba(201,168,76,.08);display:flex;align-items:center;justify-content:center;border:1px solid rgba(201,168,76,.3)}}
.bio-name{{font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--pa);text-align:center;text-transform:uppercase;letter-spacing:.06em;line-height:1.3}}
.bio-role{{font-family:'Roboto Condensed',sans-serif;font-size:12px;color:var(--gr);text-align:center;line-height:1.2}}
.bio-conn{{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ca);text-align:center;line-height:1.2;margin-top:2px}}
/* MONEY */
.nums-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;flex:1}}
.num-block{{background:rgba(201,168,76,.05);border:1px solid rgba(201,168,76,.18);padding:24px 18px;border-radius:4px}}
.num-val{{font-family:'Anton',sans-serif;font-size:76px;color:var(--ca);line-height:1;margin-bottom:8px}}
.num-label{{font-family:'Roboto Condensed',sans-serif;font-size:28px;font-weight:700;margin-bottom:4px}}
.num-en{{font-family:'Roboto Condensed',sans-serif;font-size:20px;color:var(--gr)}}
/* LIST */
.item-list{{list-style:none;flex:1}}
.item-list li{{font-family:'Roboto Condensed',sans-serif;font-size:36px;font-weight:400;padding:16px 0;border-bottom:1px solid var(--rule);line-height:1.3}}
.item-list li::before{{content:"→ ";color:var(--ca)}}
/* QUOTE */
.quote-block{{border-left:4px solid var(--ca);padding:24px 28px;margin-bottom:24px;flex:1;background:rgba(201,168,76,.04)}}
.quote-mark{{font-family:'Anton',sans-serif;font-size:80px;color:var(--ca);line-height:.7;margin-bottom:10px}}
.quote-text{{font-family:'Roboto Condensed',sans-serif;font-size:34px;line-height:1.4;margin-bottom:18px}}
.quote-source{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--ca);letter-spacing:.08em}}
.quote-context{{font-family:'Roboto Condensed',sans-serif;font-size:26px;color:var(--gr);line-height:1.4}}
/* SOURCES */
.src-head{{font-family:'Anton',sans-serif;font-size:80px;line-height:.95;text-transform:uppercase;margin-bottom:32px}}
.src-list{{flex:1}}
.src-row{{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--gr);display:flex;gap:16px;padding:10px 0;border-bottom:1px solid var(--rule);line-height:1.4}}
.src-num{{color:var(--ca);flex-shrink:0;width:30px;font-weight:700}}
.cta-pt{{font-family:'Anton',sans-serif;font-size:52px;line-height:1;color:var(--ca);text-transform:uppercase;margin-top:24px}}
.cta-en{{font-family:'Roboto Condensed',sans-serif;font-size:24px;color:var(--gr);margin-top:6px}}
/* CONTEXT IMAGE SLOT */
.context-img-slot{{min-height:220px;max-height:340px;border:2px solid rgba(201,168,76,.25);border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;margin-bottom:18px;background:rgba(10,10,10,.3);flex-shrink:0}}
.context-img-slot img{{width:100%;height:100%;object-fit:cover;display:block}}
.ctx-query{{font-family:'JetBrains Mono',monospace;font-size:16px;color:var(--ca);text-align:center;padding:16px;opacity:.7}}
</style>
</head>
<body>
{slides_html}
</body>
</html>"""

    html_path = Path(work_dir) / "cover.html"
    html_path.write_text(html)
    return str(html_path)


def render_pngs(html_path, output_dir):
    # Pre-export gate: block if any raw placeholder pattern is still in the HTML
    _html_text = Path(html_path).read_text()
    _bad = [p for p in ("_STICKER", "FACE STICKER", "bg-removed PNG") if p in _html_text]
    if _bad:
        raise ValueError(
            f"Export blocked — unresolved placeholder(s) in {html_path}: {_bad}. "
            "Source real photos or use .bio-initials fallback before exporting."
        )

    os.makedirs(output_dir, exist_ok=True)
    script = os.environ.get("EXPORT_SCRIPT", "export_variants.js")
    result = subprocess.run(
        ["node", script, html_path, output_dir],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"  Render error: {result.stderr[:200]}")
        return False
    print(f"  Rendered: {result.stdout.strip().split(chr(10))[-1]}")

    for f in Path(output_dir).glob("blue_*"):
        new_name = f.name.replace("blue_", "lime_")
        f.rename(f.parent / new_name)

    return True


def generate_image_suggestions(content, niche):
    """Build image_suggestions.txt content for the resources/ folder.
    Called after Haiku generates content. Lists every image need: cover, per-slide, clips, screenshots."""
    lines = [
        "IMAGE SUGGESTIONS",
        "=================",
        "Fill resources/ with these before publishing. Real photos first, then AI generation.",
        "",
        "COVER IMAGE",
        "-----------",
    ]
    cv = content.get("cover_visual", {})
    if cv:
        lines.append(f"Subject type: {cv.get('subject_type', 'unknown')}")
        lines.append(f"Recommended: Option {cv.get('recommended', 'A').upper()} — {cv.get('reason', '')}")
        lines.append("")
        a = cv.get("option_a", {})
        if a:
            lines.append(f"Option A (CC real photo): {a.get('search_query', '')}")
            lines.append(f"  Description: {a.get('description', '')}")
            lines.append(f"  Sources: agenciabrasil.ebc.com.br  |  commons.wikimedia.org")
        b = cv.get("option_b", {})
        if b:
            lines += ["", f"Option B (AI generation — {b.get('tool_hint', 'OpenAI / Seedream 4.5')}):",
                      f"  Concept: {b.get('concept', '')}",
                      f"  Prompt: {b.get('prompt', '')}"]
        c = cv.get("option_c", {})
        if c:
            lines += ["", f"Option C (graphic design): {c.get('concept', '')}"]
    else:
        lines.append("(no cover_visual in output — check Haiku response)")

    lines += ["", "MIDDLE SLIDES", "-------------"]
    for i, slide in enumerate(content.get("slides", []), start=2):
        vh = slide.get("visual_hint", "none")
        cq = slide.get("context_image_query", "")
        people = slide.get("mentioned_people", [])
        if vh == "context-image" and cq:
            lines += [f"Slide {i} ({slide.get('type', 'list')}) — CONTEXT IMAGE:",
                      f"  Search: {cq}",
                      f"  Sources: Agência Brasil or Wikimedia Commons CC BY"]
        elif vh == "bio-card" and people:
            for p in people:
                lines += [f"Slide {i} — FACE NEEDED: {p.get('name', '?')}",
                          f"  Search: {p.get('image_hint', p.get('name', ''))}",
                          f"  Sources: Wikipedia / Agência Brasil CC BY 3.0"]

    clips = content.get("clip_suggestions", [])
    if clips:
        lines += ["", "CLIP SUGGESTIONS (short video — 5-8 sec per slide)", "-------------------------------------------------"]
        for c in clips:
            lines += [f"Slide {c.get('slide', '?')} — {c.get('person_or_topic', '')}:",
                      f"  YouTube query: {c.get('youtube_query', '')}",
                      f"  Duration: {c.get('duration_hint', '5-8 sec')}",
                      f"  Why: {c.get('reason', '')}"]

    receipts = content.get("receipts_needed", [])
    lines += ["", "SCREENSHOT CROPS — MANDATORY for factual claims", "-----------------------------------------------"]
    if receipts:
        for i, r in enumerate(receipts, 1):
            lines.append(f"{i}. {r}")
    else:
        lines.append("Screenshot the primary sources listed below. Crop to: headline + outlet + date.")

    lines += ["", "SOURCES TO SCREENSHOT", "---------------------"]
    for i, src in enumerate(content.get("sources", []), 1):
        lines.append(f"{i}. {src}")

    return "\n".join(lines)


def visual_audit(content, niche):
    """Scan generated content for visual completeness issues.
    Returns (is_ok: bool, issues: list[str], summary: str).
    Called before emailing preview — flags boring/incomplete carousels."""
    issues = []
    slides = content.get("slides", [])

    # Consecutive none check
    run = 0
    for s in slides:
        if s.get("visual_hint", "none") == "none":
            run += 1
            if run > 1:
                issues.append(f"BORING: {run}+ consecutive text-only slides. Add context-image or bio-card.")
                break
        else:
            run = 0

    # Named person without bio-card visual_hint
    for i, s in enumerate(slides, start=2):
        if s.get("mentioned_people") and s.get("visual_hint") != "bio-card":
            for p in s.get("mentioned_people", []):
                name = p.get("name", "?") if isinstance(p, dict) else str(p)
                issues.append(f"Slide {i}: '{name}' named but visual_hint != bio-card — face won't render.")

    # context-image with empty query
    for i, s in enumerate(slides, start=2):
        if s.get("visual_hint") == "context-image" and not s.get("context_image_query", "").strip():
            issues.append(f"Slide {i}: visual_hint=context-image but context_image_query is empty.")

    # News visual floor: require at least 3 middle slides with context-image.
    if niche in ("brazil", "usa"):
        context_count = sum(1 for s in slides if s.get("visual_hint") == "context-image")
        if context_count < 3:
            issues.append(
                f"News visual floor miss: only {context_count} context-image slide(s); require >= 3."
            )
    if niche == "opc":
        context_count = sum(1 for s in slides if s.get("visual_hint") == "context-image")
        if context_count < 2:
            issues.append(
                f"OPC visual floor miss: only {context_count} context-image slide(s); require >= 2."
            )
        if not content.get("slide4_body", "").strip():
            issues.append("OPC explanation miss: slide4_body is empty.")
        if len(content.get("slide3_items", [])) < 3:
            issues.append("OPC detail miss: slide3_items has fewer than 3 points.")

    # Cover visual missing
    if not content.get("cover_visual"):
        issues.append("Cover: no cover_visual field — cover will be text-only.")

    is_ok = len(issues) == 0
    if is_ok:
        summary = "VISUAL AUDIT: PASSED — all slides have visual anchors."
    else:
        summary = f"VISUAL AUDIT: {len(issues)} ISSUE(S) FOUND:\n" + "\n".join(f"  - {x}" for x in issues)
    return is_ok, issues, summary


def generate_caption(topic, niche, slide_texts=None):
    """Generate Instagram caption + hashtags via Claude Haiku.

    Returns dict with keys:
      caption            — 150-200 char body (no hashtags)
      in_post_hashtags   — 5 hashtags for caption body
      first_comment_hashtags — 20-25 hashtags for first comment

    OPC rules: no promises, no superlatives, no outcome guarantees.
    Brazil/USA rules: attribution only, never hashtag political party names.
    """
    slide_context = ""
    if slide_texts:
        slide_context = "\n\nSlide text context:\n" + "\n".join(
            f"  Slide {i+1}: {str(t)[:120]}" for i, t in enumerate(slide_texts[:6])
        )

    if niche == "opc":
        copy_rules_caption = (
            "OPC caption rules: "
            "Write as Mike, a South Florida contractor. First person, conversational. "
            "NEVER promise outcomes, results, timelines, or guarantees. "
            "NEVER use superlatives (best, #1, guaranteed, always). "
            "Hook = first sentence visible in feed (question or surprising fact). "
            "Keep body 150-200 chars total. Educational, not sales-y."
        )
        hashtag_rules = (
            "In-post hashtags (5): use broad contractor/homeowner topics. "
            "e.g. #southfloridacontractor #oakparkbuilds #homeremodel #contractortips #floridahomeowner\n"
            "First-comment hashtags (20-25): mix of niche construction + local Florida tags. "
            "NEVER use superlative tags (#best, #top, #1contractor). "
            "Include: #generalcontractor #remodeling #construction #homeimp #floridarealestate "
            "#homeinspection #diy #homeowner #contractorlife #buildingpermit + 10-15 more specific ones."
        )
    elif niche == "brazil":
        copy_rules_caption = (
            "Brazil caption rules (PT-BR): "
            "Hook = first sentence stops the scroll — provocative question or number. "
            "Body: 150-200 chars total, factual, no editorial opinion, no accusation. "
            "Attribution without traffic: small attribution 'via @handle' if needed — never @-tag or hashtag them. "
            "End with: 'Salva pra não esquecer.' or similar PT CTA."
        )
        hashtag_rules = (
            "In-post hashtags (5): topic-only PT hashtags. "
            "e.g. #politicabrasileira #senadofederal #fiscalizacao #direitoshumanos #govbr\n"
            "First-comment hashtags (20-25): NEVER include party abbreviations (#PT, #PL, #PSDB, #MDB) "
            "or politician names as hashtags (#Bolsonaro, #Lula, #Moraes). "
            "Topic-only: #congresso #stf #senadofederal #politicabrasileira #democracia + 15-20 more."
        )
    else:  # usa
        copy_rules_caption = (
            "USA caption rules (English): "
            "Hook = first sentence stops the scroll — provocative question or number. "
            "Body: 150-200 chars total, factual, no editorial opinion. "
            "Attribution without traffic: small 'via @handle' if needed — never @-tag or hashtag them."
        )
        hashtag_rules = (
            "In-post hashtags (5): topic-only US news hashtags. "
            "e.g. #usnews #congress #factcheck #americanpolitics #mediabias\n"
            "First-comment hashtags (20-25): NEVER use politician names as hashtags. "
            "Topic-only: #usnews #congress #senate #factcheck #mediabias + 15-20 more."
        )

    prompt = f"""Generate an Instagram caption for this carousel post.

Topic: "{topic}"
Niche: {niche.upper()}
{copy_rules_caption}
{slide_context}

{hashtag_rules}

Return ONLY a valid JSON object:
{{
  "caption": "150-200 character caption body (NO hashtags in this field). Hook on first line.",
  "in_post_hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5",
  "first_comment_hashtags": "#tag1 #tag2 ... (20-25 tags, space-separated)"
}}"""

    for attempt in range(2):
        try:
            text = _claude_with_fallback(
                prompt, max_tokens=600, timeout=20,
                context=f"generate_caption({niche}, attempt {attempt+1})",
            )
        except Exception as e:
            print(f"  generate_caption LLM failed (attempt {attempt+1}): {e}")
            continue
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            print(f"  generate_caption: no JSON in response (attempt {attempt+1})")
            continue
        try:
            result = json.loads(m.group())
            # Basic validation
            if result.get("caption") and result.get("in_post_hashtags") and result.get("first_comment_hashtags"):
                print(f"  Caption generated ({len(result['caption'])} chars)")
                return result
        except json.JSONDecodeError as e:
            print(f"  generate_caption JSON parse error (attempt {attempt+1}): {e}")
            continue
    print("  generate_caption: failed after 2 attempts — returning empty")
    return {"caption": "", "in_post_hashtags": "", "first_comment_hashtags": ""}


def ensure_template_carousel_exists(series_folder_id: str, drive) -> str:
    """Return the _TEMPLATE_CAROUSEL folder ID under series_folder_id, creating it if missing.
    Prevents next_version_number() from running against the series root instead of _TEMPLATE_CAROUSEL.
    """
    resp = drive.files().list(
        q=f"'{series_folder_id}' in parents and name='_TEMPLATE_CAROUSEL' "
          f"and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        corpora="allDrives",
    ).execute()
    existing = resp.get("files", [])
    if existing:
        return existing[0]["id"]
    folder = drive.files().create(
        body={"name": "_TEMPLATE_CAROUSEL",
              "mimeType": "application/vnd.google-apps.folder",
              "parents": [series_folder_id]},
        supportsAllDrives=True, fields="id",
    ).execute()
    return folder["id"]


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "5 things your contractor won't tell you"
    content = generate_carousel_content(topic, "opc", "tip")
    if content:
        print(json.dumps(content, indent=2))
