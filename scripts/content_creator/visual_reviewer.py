#!/usr/bin/env python3
"""
visual_reviewer.py — Claude-vision review of rendered carousel PNGs.

Goal 1C of the proactive auto-fixer. Catches problems no text linter can see:

  · face_cropped       — face cut at top/bottom of slide
  · feature_cut        — eyes / mouth / forehead chopped (zoomed-in face)
  · text_overlap       — copy collides with subject's face / sticker
  · text_overflow      — body text exits the safe area
  · low_contrast       — text disappears into background photo
  · missing_sources    — last slide has no visible source citations (news only)
  · wrong_aspect       — non 1080×1350 (caught locally, but vision confirms framing)
  · brand_inconsistency — accent color / typography drift from template

Returns structured JSON the auto-fixer can route:
  · face_cropped / feature_cut / text_overlap → re-fetch image with skip_providers
                                                (reuses goal 1A cascade logic)
  · text_overflow / low_contrast → CSS adjustment + re-render via render_pngs
  · missing_sources → flag as [manual] — auto-add requires research
  · wrong_aspect → flag (renderer config issue, not content)

Uses Anthropic's vision API directly (claude-haiku-4-5 with image input). The
existing _llm_text_cascade does NOT support vision — vision needs the raw API
call with base64 image content. Keeps OpenAI/Gemini as fallback if Claude fails.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ANTHROPIC_KEY = os.environ.get("CLAUDE_KEY_4_CONTENT", "") or os.environ.get(
    "ANTHROPIC_API_KEY", ""
)
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

VISION_MODEL = "claude-haiku-4-5-20251001"
OPENAI_VISION_MODEL = "gpt-4o-mini"


# Issue catalog — auto_fixer reads these to decide which fix path to invoke.
ISSUE_TAXONOMY = {
    "face_cropped":    {"fix_via": "image_refetch",  "severity_default": "high"},
    "feature_cut":     {"fix_via": "image_refetch",  "severity_default": "high"},
    "text_overlap":    {"fix_via": "image_refetch",  "severity_default": "high"},
    "text_overflow":   {"fix_via": "css_adjust",     "severity_default": "med"},
    "low_contrast":    {"fix_via": "css_adjust",     "severity_default": "med"},
    "missing_sources": {"fix_via": "manual",         "severity_default": "high"},
    "wrong_aspect":    {"fix_via": "manual",         "severity_default": "high"},
    "brand_drift":     {"fix_via": "manual",         "severity_default": "low"},
    "ok":              {"fix_via": "none",           "severity_default": "low"},
}


def _slide_role_hint(slide_index: int, slide_count: int, niche: str) -> str:
    """Tell Claude what this slide is supposed to be — improves issue detection."""
    if slide_index == 1:
        return "COVER (hook + headline + cover photo)"
    if slide_index == slide_count:
        return ("SOURCES SLIDE — should list 3+ citations" if niche != "opc"
                else "SOURCES SLIDE — should list 3+ citations and Oak Park licensee line")
    if slide_count >= 4 and slide_index == slide_count - 1:
        return "TIP / CTA SLIDE"
    return f"BODY SLIDE {slide_index}"


def _build_vision_prompt(slide_index: int, slide_count: int, niche: str) -> str:
    role = _slide_role_hint(slide_index, slide_count, niche)
    return f"""You are reviewing one slide of an Instagram carousel (1080×1350).

NICHE: {niche}
SLIDE {slide_index} of {slide_count} — role: {role}

Inspect the image and flag ONLY real visual problems. Do NOT comment on
content/copy quality (a separate text reviewer handles that).

Issue types to detect:
  · face_cropped     — a person's face is partially cut off by the slide edge
  · feature_cut      — eyes, mouth, or forehead are clipped (over-zoomed face)
  · text_overlap     — body/headline text collides with subject (face, sticker, logo)
  · text_overflow    — text runs off the safe area or wraps awkwardly
  · low_contrast     — text is hard to read against its background
  · missing_sources  — only check on SOURCES role: are there visible citations?
  · wrong_aspect     — image looks stretched/squished (not 1080×1350)
  · brand_drift      — accent color or typography looks off-brand vs typical templates

Return ONLY valid JSON (no markdown, no commentary):
{{
  "verdict": "ok | issues",
  "issues": [
    {{"type": "<one of the types above>",
      "severity": "high|med|low",
      "where": "short location hint — e.g. 'top-right of cover', 'last source line'",
      "description": "one sentence of what's wrong"}}
  ]
}}

If the slide is clean, return: {{"verdict": "ok", "issues": []}}
"""


def _claude_vision_call(prompt: str, image_b64: str, *, timeout: int = 60) -> str:
    """Direct Anthropic vision API call (no cascade — vision is Claude-specific here)."""
    if not ANTHROPIC_KEY:
        raise RuntimeError("CLAUDE_KEY_4_CONTENT / ANTHROPIC_API_KEY not set")

    payload = json.dumps({
        "model": VISION_MODEL,
        "max_tokens": 700,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": prompt},
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
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return resp["content"][0]["text"]


def _openai_vision_call(prompt: str, image_b64: str, *, timeout: int = 60) -> str:
    """Fallback: GPT-4o-mini vision when Claude vision is rate-limited or down."""
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set (vision fallback)")

    payload = json.dumps({
        "model": OPENAI_VISION_MODEL,
        "max_tokens": 700,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{image_b64}"
                }},
            ],
        }],
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "content-type": "application/json",
        },
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return resp["choices"][0]["message"]["content"]


_JSON_BLOB = re.compile(r"\{[\s\S]+\}")


def _parse_vision_json(raw: str) -> dict:
    if not raw:
        return {"verdict": "error", "issues": [], "raw": ""}
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = _JSON_BLOB.search(raw)
    if not m:
        return {"verdict": "error", "issues": [], "raw": raw[:500]}
    try:
        return json.loads(m.group(0))
    except Exception as e:
        return {"verdict": "error", "issues": [], "raw": raw[:500], "parse_error": str(e)}


def review_png_bytes(
    png_bytes: bytes,
    slide_index: int,
    slide_count: int,
    niche: str,
    *,
    png_name: str = "",
) -> dict:
    """Send one PNG to Claude vision (with OpenAI fallback) and return verdict."""
    image_b64 = base64.b64encode(png_bytes).decode("ascii")
    prompt = _build_vision_prompt(slide_index, slide_count, niche)

    raw = ""
    try:
        raw = _claude_vision_call(prompt, image_b64)
    except Exception as ce:
        # Fallback to OpenAI vision before giving up
        try:
            raw = _openai_vision_call(prompt, image_b64)
        except Exception as oe:
            return {
                "verdict": "error",
                "issues": [],
                "error": f"claude={type(ce).__name__}:{ce} | openai={type(oe).__name__}:{oe}",
                "png": png_name,
                "slide": slide_index,
            }

    parsed = _parse_vision_json(raw)
    parsed["png"] = png_name
    parsed["slide"] = slide_index
    # Decorate each issue with the fix_via routing hint from the taxonomy.
    for issue in parsed.get("issues", []):
        meta = ISSUE_TAXONOMY.get(issue.get("type", ""), {})
        issue["fix_via"] = meta.get("fix_via", "manual")
    return parsed


def review_png_folder(
    png_paths: list[Path],
    niche: str,
    *,
    slide_count_override: int | None = None,
) -> dict:
    """Review every PNG in a folder. Returns aggregate {slide_results, summary}."""
    paths = sorted(png_paths)
    slide_count = slide_count_override or len(paths)

    results = []
    for i, p in enumerate(paths, start=1):
        try:
            png_bytes = p.read_bytes()
        except Exception as e:
            results.append({
                "slide": i, "png": p.name, "verdict": "error",
                "error": f"read failed: {e}", "issues": [],
            })
            continue
        results.append(
            review_png_bytes(png_bytes, i, slide_count, niche, png_name=p.name)
        )

    total_issues = sum(len(r.get("issues", [])) for r in results)
    refetch_count = sum(
        1 for r in results for i in r.get("issues", [])
        if i.get("fix_via") == "image_refetch"
    )
    css_count = sum(
        1 for r in results for i in r.get("issues", [])
        if i.get("fix_via") == "css_adjust"
    )
    return {
        "slide_results": results,
        "summary": {
            "total_slides": len(results),
            "total_issues": total_issues,
            "refetch_count": refetch_count,
            "css_count": css_count,
        },
    }


# ── HTML render for change log emails ───────────────────────────────────────

def render_visual_change_log_html(visual_review: dict) -> str:
    """Email-friendly HTML summary of vision findings."""
    summary = visual_review.get("summary", {})
    results = visual_review.get("slide_results", [])
    total_issues = summary.get("total_issues", 0)

    if total_issues == 0:
        return "<p style='color:#888'>No visual issues found.</p>"

    th = "padding:6px 10px;background:#1a1a1a;color:#cbcc10;text-align:left;font-size:12px"
    td = "padding:6px 10px;border-bottom:1px solid #222;font-size:12px;vertical-align:top"

    rows = []
    for r in results:
        for issue in r.get("issues", []):
            sev = issue.get("severity", "")
            sev_color = {"high": "#ff5555", "med": "#cbcc10", "low": "#888"}.get(sev, "#888")
            fix_label = issue.get("fix_via", "manual")
            rows.append(
                "<tr>"
                f"<td style='{td}'>{r.get('slide','?')}</td>"
                f"<td style='{td}'>{issue.get('type','')}</td>"
                f"<td style='{td};color:{sev_color}'>{sev}</td>"
                f"<td style='{td}'>{issue.get('where','')[:80]}</td>"
                f"<td style='{td};color:#888'>{issue.get('description','')[:140]}</td>"
                f"<td style='{td}'><code style='color:#cbcc10'>{fix_label}</code></td>"
                "</tr>"
            )

    return (
        f"<h3 style='color:#cbcc10;margin:16px 0 8px 0'>Visual review — {total_issues} issue(s)</h3>"
        f"<p style='color:#888;font-size:11px;margin:0 0 8px 0'>"
        f"refetch needed: {summary.get('refetch_count',0)} | "
        f"css adjust: {summary.get('css_count',0)}</p>"
        "<table style='border-collapse:collapse;width:100%;background:#0a0a0a;color:#f0ebe3'>"
        "<tr>"
        f"<th style='{th}'>slide</th><th style='{th}'>type</th><th style='{th}'>sev</th>"
        f"<th style='{th}'>where</th><th style='{th}'>description</th>"
        f"<th style='{th}'>route</th>"
        "</tr>"
        + "\n".join(rows) +
        "</table>"
    )


# ── CLI for quick testing ───────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python visual_reviewer.py <png_folder> <niche>")
        sys.exit(1)
    png_dir = Path(sys.argv[1])
    niche = sys.argv[2]
    pngs = sorted(png_dir.glob("*.png"))
    if not pngs:
        print(f"no PNGs found in {png_dir}")
        sys.exit(1)
    result = review_png_folder(pngs, niche)
    print(json.dumps(result, indent=2, ensure_ascii=False))
