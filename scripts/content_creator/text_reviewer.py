#!/usr/bin/env python3
"""
text_reviewer.py — Claude-driven text/fact-check review for carousel slides.

Goal 1B of the proactive auto-fixer. The reviewer's job:
  1. Pull plain text per slide from the rendered cover.html
  2. Send every slide to Claude with the SAME copy rules the original
     carousel_builder used (OPC_COPY_RULES / BRAZIL_COPY_RULES) plus the
     length and tone guardrails from carousel_reviewer's HTML linter.
  3. Ask Claude to flag — and minimally edit — only what is wrong:
       · factual error / unverified stat
       · suspicious / unsourced claim
       · competitor mention or banned brand
       · tone mismatch (sales-y on OPC, opinion on Brazil)
       · length overrun (cover headline > 42 chars, list item > 34, etc.)
       · average/generic hook or missing curiosity/payoff arc
  4. Return structured JSON the auto-fixer can apply with str.replace.

This module does NOT touch HTML or rerender. It only PROPOSES the edits.
auto_fixer.apply_text_edits() consumes the proposals and re-renders the PNGs.

The Claude call uses _claude_with_fallback (Claude → OpenAI → Gemini cascade
imported from carousel_builder), so the same key chain that powers the
content creator powers the reviewer — no new secret needed.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Reuse the same LLM cascade + copy rules the original creator uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from carousel_builder import (  # noqa: E402
    _claude_with_fallback,
    OPC_COPY_RULES,
    BRAZIL_COPY_RULES,
)
from prompt_builder import extract_slide_texts  # noqa: E402


# Per-slot length floors/ceilings that carousel_reviewer.check_html_placeholders
# already enforces. Mirrored here so Claude knows the same bounds when proposing
# rewrites — keeps proposals from re-introducing already-flagged length issues.
LENGTH_GUARDRAILS = {
    "cover_headline_max":   42,
    "cover_subhead_max":   110,
    "list_item_max":        34,
    "source_line_min":      12,
    "source_line_max":     120,
    "caption_sentences_max": 4,
}


# Brand mentions we never want surfacing in our content. Auto-fixer treats any
# match as severity=high. Add to this list when a new competitor pops up.
COMPETITOR_BANNED_TERMS = [
    # OPC competitors / commodity brands we don't endorse on-air
    "Home Depot", "Lowe's", "Lowes",
    # Roofing / contractor competitors that have shown up in stock photos
    # (extend as needed — Priscila adds via memory note)
]


HOOK_STORYTELLING_REVIEW_RULES = """
══════════════════════ HOOK + STORYTELLING QUALITY GATE ══════════════════════
This gate exists because hooks must be very good, not average.
Do NOT pass a carousel just because it is technically factual.

GENERIC HOOKS TO FLAG:
- "Here's what you need to know"
- "Let's talk about"
- "Important update"
- "You won't believe this"
- "Things homeowners should know"
- "What happened today"
- "Tips and tricks"
- "What to do"
- Any vague cover that does not create a claim, tension, number, risk,
  consequence, contradiction, or curiosity gap.

GOOD PROFESSIONAL HOOK PATTERNS:
1. Specific number that demands explanation.
2. Contradiction / tension.
3. Consequence hook.
4. Question with stakes.
5. Source/receipt hook.
6. Curiosity gap without deception.
7. Visual proof / before-after hook.

NICHE-SPECIFIC HOOK STANDARD:
- OPC hooks must be homeowner-facing: risk, cost, delay, hidden consequence,
  missing scope, permit/code issue, bad quote comparison, material/process
  misunderstanding, or what to ask before signing. They must NOT sound salesy,
  exaggerated, or like OPC promises an outcome.
- Brazil/USA News hooks must be journalistic and credible: exact claim,
  contradiction, vote/result, official source/document, legal consequence,
  institutional tension, missing context, or confirmed vs unproven. They must
  NOT become "how we verified this" unless the explicit format is a
  behind-the-scenes/process post.

STORY ARC CHECK:
The carousel should build a clear reward arc:
- Cover: strong hook / central tension.
- Early slides: why this matters and what the viewer is missing.
- Middle slides: evidence, context, or breakdown.
- Later slides: reward/payoff — clearest answer, verdict, practical takeaway,
  or reveal.
- Final slide: sources/CTA/final clarity.

RESEARCH / PROOF CHECK:
If a supporting point is weak, unsupported, or unclear, flag it. The fix should
soften it, remove it, or require more research. Do not allow the carousel to
fill a slide with a weak claim just because the template needs another point.
"""


def _niche_rules(niche: str) -> str:
    """Return the canonical copy-rules block for a niche."""
    n = (niche or "").lower()
    if n == "opc":
        return OPC_COPY_RULES
    if n in ("brazil", "usa"):
        return BRAZIL_COPY_RULES
    # Higashi / unknown — be conservative, use Brazil rules (factual, sourced)
    return BRAZIL_COPY_RULES


def _build_review_prompt(slide_texts: dict, niche: str, post_id: str = "") -> str:
    """Build the single Claude prompt that reviews every slide at once.

    Returning all slides in one call keeps token cost low and lets Claude see
    the full carousel context (a hook on slide 1 that contradicts slide 4 is
    only catchable with whole-carousel context).
    """
    rules = _niche_rules(niche)
    slides_block = "\n".join(
        f"SLIDE {n}:\n\"\"\"{text.strip()}\"\"\""
        for n, text in sorted(slide_texts.items())
    )

    banned = ", ".join(f'"{t}"' for t in COMPETITOR_BANNED_TERMS) or "(none)"

    return f"""You are the editorial reviewer for a published-quality Instagram carousel.

NICHE: {niche}
POST_ID: {post_id or "(unspecified)"}

These are the SAME rules the original writer was given. Apply them now to the
finished carousel and flag anything that violates them — but ONLY suggest the
minimum edit needed. Do not rewrite the slide. Do not change the voice.
Preserve everything that's already correct.

══════════════════════ MANDATORY COPY RULES ══════════════════════
{rules}

{HOOK_STORYTELLING_REVIEW_RULES}

══════════════════════ LENGTH GUARDRAILS ══════════════════════
- Cover headline: max {LENGTH_GUARDRAILS['cover_headline_max']} chars
- Cover subhead:  max {LENGTH_GUARDRAILS['cover_subhead_max']} chars
- List item:      max {LENGTH_GUARDRAILS['list_item_max']} chars
- Source line:    {LENGTH_GUARDRAILS['source_line_min']}–{LENGTH_GUARDRAILS['source_line_max']} chars

══════════════════════ BANNED MENTIONS ══════════════════════
{banned}

══════════════════════ THE CAROUSEL ══════════════════════
{slides_block}

══════════════════════ YOUR JOB ══════════════════════
For EACH issue you find, decide:
  · type: one of [factual, unsourced, suspicious, competitor, tone, length, promise, hook, story_arc, proof_gap]
  · severity: high | med | low
      - high  = factual error, unverified claim presented as fact, banned mention,
                explicit promise about OPC ("we always", "our guarantee"), opinion
                framed as fact on Brazil/USA niche, misleading/clickbait hook,
                or a hook that changes the meaning of the source.
      - med   = length overrun, weak source, sales-y tone, missing party affiliation,
                generic/average hook, missing payoff, weak story arc, or proof gap.
      - low   = stylistic — caption too long, exclamation overuse.
  · suggested: the minimal edit that fixes the issue. NULL if you cannot fix it
              without external research (then explain in `reason` what to verify).
              Keep the suggested text within the same length guardrail bracket.
  · reason: one short sentence explaining what's wrong and why.

Return ONLY valid JSON in this exact shape (no markdown, no commentary):
{{
  "overall_assessment": "pass | needs_minor | needs_major",
  "issues": [
    {{
      "slide": <int>,
      "type": "<one of: factual, unsourced, suspicious, competitor, tone, length, promise, hook, story_arc, proof_gap>",
      "severity": "<high|med|low>",
      "original": "<the exact substring from the slide that's wrong>",
      "suggested": "<the minimal-edit replacement, or null>",
      "reason": "<one sentence>"
    }}
  ]
}}

If the carousel is clean, return: {{"overall_assessment": "pass", "issues": []}}
"""


_JSON_BLOB = re.compile(r"\{[\s\S]+\}")


def _parse_review_json(raw: str) -> dict:
    """Claude sometimes wraps JSON in ```json ...``` fences. Strip and parse."""
    if not raw:
        return {"overall_assessment": "error", "issues": [], "raw": ""}
    # Try direct parse first
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Pull the first {...} block
    m = _JSON_BLOB.search(raw)
    if not m:
        return {"overall_assessment": "error", "issues": [], "raw": raw[:500]}
    try:
        return json.loads(m.group(0))
    except Exception as e:
        return {
            "overall_assessment": "error",
            "issues": [],
            "raw": raw[:500],
            "parse_error": str(e),
        }


def review_slide_texts(
    slide_texts: dict,
    niche: str,
    post_id: str = "",
    *,
    max_tokens: int = 3000,
) -> dict:
    """Send slide_texts to Claude and parse the structured review back.

    Returns:
      {
        "overall_assessment": "pass" | "needs_minor" | "needs_major" | "error",
        "issues": [
          {"slide": int, "type": str, "severity": str,
           "original": str, "suggested": str|None, "reason": str},
          ...
        ],
        "raw": str (only on parse error),
      }

    Never raises — failure modes return overall_assessment="error" so the
    auto-fixer can log it and skip text edits without blocking image fixes.
    """
    if not slide_texts:
        return {"overall_assessment": "pass", "issues": [],
                "note": "no slide texts extracted"}

    prompt = _build_review_prompt(slide_texts, niche, post_id)
    try:
        raw = _claude_with_fallback(
            prompt, max_tokens=max_tokens, timeout=90,
            context="text_reviewer",
        )
    except Exception as e:
        return {
            "overall_assessment": "error",
            "issues": [],
            "error": f"{type(e).__name__}: {e}",
        }

    return _parse_review_json(raw)


def review_carousel_html(
    html: str,
    niche: str,
    post_id: str = "",
) -> dict:
    """Convenience wrapper — extract slide texts from html string and review."""
    slide_texts = extract_slide_texts(html)
    result = review_slide_texts(slide_texts, niche, post_id)
    result["slide_count"] = len(slide_texts)
    return result


# ── Apply edits to HTML (consumed by auto_fixer for the re-render step) ─────

def apply_edits_to_html(html: str, issues: list[dict]) -> tuple[str, list[dict]]:
    """Apply minimal-edit suggestions to the HTML string.

    For each issue with non-null `suggested` and `original` substring found in
    the HTML, do a single str.replace. Records what was applied vs skipped so
    the email change log shows exactly what changed.

    Skip rules:
      · suggested is null/empty → SKIP (Claude couldn't fix without research)
      · severity == "low" → SKIP for now (cosmetic; avoids overzealous edits)
      · original substring not found in HTML verbatim → SKIP (Claude paraphrased)
    """
    applied = []
    skipped = []
    new_html = html

    for issue in issues:
        original = (issue.get("original") or "").strip()
        suggested = (issue.get("suggested") or "")
        severity = (issue.get("severity") or "").lower()

        if not original:
            skipped.append({**issue, "skip_reason": "no original text"})
            continue
        if severity == "low":
            skipped.append({**issue, "skip_reason": "low-severity cosmetic"})
            continue
        if not suggested:
            skipped.append({**issue, "skip_reason": "needs human research"})
            continue
        if original not in new_html:
            skipped.append({**issue, "skip_reason": "original text not found verbatim"})
            continue

        new_html = new_html.replace(original, suggested, 1)
        applied.append({
            **issue,
            "applied": True,
        })

    return new_html, applied + skipped


# ── HTML render for change log emails ───────────────────────────────────────

def render_text_change_log_html(review: dict, edit_log: list[dict] | None = None) -> str:
    """Email-friendly HTML summary of text issues + which were applied."""
    issues = review.get("issues", [])
    if not issues:
        return ("<p style='color:#888'>No text issues found "
                f"(assessment: {review.get('overall_assessment','?')})</p>")

    th = "padding:6px 10px;background:#1a1a1a;color:#cbcc10;text-align:left;font-size:12px"
    td = "padding:6px 10px;border-bottom:1px solid #222;font-size:12px;vertical-align:top"

    applied_by_orig = {
        e.get("original", ""): e for e in (edit_log or []) if e.get("applied")
    }

    rows_html = []
    for i in issues:
        orig = i.get("original", "")
        sug = i.get("suggested") or "—"
        sev = i.get("severity", "")
        sev_color = {"high": "#ff5555", "med": "#cbcc10", "low": "#888"}.get(sev, "#888")
        applied = "✅ applied" if orig in applied_by_orig else "⏸ flagged"

        rows_html.append(
            "<tr>"
            f"<td style='{td}'>{i.get('slide','?')}</td>"
            f"<td style='{td}'>{i.get('type','')}</td>"
            f"<td style='{td};color:{sev_color}'>{sev}</td>"
            f"<td style='{td}'><code style='color:#888'>{orig[:120]}</code></td>"
            f"<td style='{td}'><code style='color:#cbcc10'>{sug[:120]}</code></td>"
            f"<td style='{td}'>{applied}</td>"
            f"<td style='{td};color:#888'>{i.get('reason','')[:100]}</td>"
            "</tr>"
        )

    return (
        f"<h3 style='color:#cbcc10;margin:16px 0 8px 0'>Text review — "
        f"{review.get('overall_assessment','?')}</h3>"
        f"<p style='color:#888;font-size:11px;margin:0 0 8px 0'>"
        f"{len(issues)} issue(s) | applied: {len(applied_by_orig)}</p>"
        "<table style='border-collapse:collapse;width:100%;background:#0a0a0a;color:#f0ebe3'>"
        "<tr>"
        f"<th style='{th}'>slide</th><th style='{th}'>type</th><th style='{th}'>sev</th>"
        f"<th style='{th}'>before</th><th style='{th}'>after</th>"
        f"<th style='{th}'>status</th><th style='{th}'>reason</th>"
        "</tr>"
        + "\n".join(rows_html) +
        "</table>"
    )


# ── CLI for quick local testing ─────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python text_reviewer.py <cover.html> <niche> [post_id]")
        sys.exit(1)
    html_path = sys.argv[1]
    niche = sys.argv[2]
    post_id = sys.argv[3] if len(sys.argv) > 3 else ""

    html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    review = review_carousel_html(html, niche, post_id)
    print(json.dumps(review, indent=2, ensure_ascii=False))
