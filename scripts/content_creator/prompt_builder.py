#!/usr/bin/env python3
"""
prompt_builder.py — Shared AI image prompt generator for the carousel pipeline.

Imported by: fix_existing_images.py, carousel_builder.py, carousel_reviewer.py

Takes slide content (text, context_image_query, niche, slide role) and produces
a specific, photorealistic AI generation prompt using Claude Haiku.

Rules baked in from IMAGE_QUALITY_RULES.md:
  - Prompts must match the slide's specific claim — not the overall topic
  - Must include: subject + action/material + location context
  - Style: ultra photorealistic documentary photo — no illustrations, no 3D renders
  - Named persons: never generate a face — return '' and let bio-initials handle it
  - 4+ word specificity minimum

Output:
  - Returns prompt string
  - Optionally saves as prompt_slide{N}_{content_word}.txt in resources/prompts/
    (named same slug as the image it will generate, so everything stays linked)

Usage:
  from prompt_builder import build_image_prompt

  prompt = build_image_prompt(
      slide_text="Our crew installed 3,200 sq ft of GAF Timberline HDZ shingles last week",
      context_image_query="GAF timberline HDZ shingles installation aerial residential oak park",
      niche="opc",
      slide_num=3,
      work_dir="/tmp/carousel_v2_walnut",
      save=True,          # writes resources/prompts/prompt_slide3_shingles.txt
  )
"""
import json, os, re, sys, urllib.request
from pathlib import Path
from typing import Optional

# ── LLM client ────────────────────────────────────────────────────────────────
ANTHROPIC_KEY = (
    os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    or os.environ.get("ANTHROPIC_API_KEY", "")
)

_SYSTEM_PROMPT = """\
You are a professional photography art director generating AI image generation prompts
for Instagram carousel slides. Your prompts must produce photorealistic results that
pass strict editorial quality gates.

RULES (from IMAGE_QUALITY_RULES.md):
1. SUBJECT MATCH — the prompt must show exactly what the slide claims. If the slide is
   about a specific kitchen remodel in Oak Park, the prompt must be about that kitchen,
   not a generic kitchen.
2. PHOTOREALISTIC STYLE — always: "ultra photorealistic documentary photograph",
   "real natural lighting", "no illustration, no 3D render, no cartoon, no plastic
   surfaces, no CGI, no studio backdrop, no text in image".
3. LOCATION CONTEXT — include the real location whenever known (Oak Park Illinois,
   South Florida, Brasília, Budapest, etc.). Generic = bad.
4. MATERIAL/ACTION SPECIFICITY — name the material (GAF Timberline HDZ shingles,
   frameless glass shower door, shiplap wood panels) and the action (installation,
   framing, pouring concrete, signing legislation).
5. MINIMUM DETAIL — at least 4 specific descriptors beyond subject + location.
6. PERSON RULE — NEVER prompt a face for a named politician/public figure/private person.
   For person slides, return the exact string: SKIP_NAMED_PERSON

Output ONLY the raw prompt text. No explanation, no quotes, no markdown.
If the slide is a named-person face card, output only: SKIP_NAMED_PERSON
"""

_NICHE_CONTEXT = {
    "opc": (
        "Niche: Oak Park Construction (roofing, concrete, kitchen/bathroom remodels, "
        "additions, framing). Real job sites in Oak Park IL and surrounding suburbs. "
        "Workers are real, materials are real, no stock-photo aesthetics."
    ),
    "brazil": (
        "Niche: Brazil News carousel (politics, legislation, institutions). "
        "Locations: Brasília, São Paulo, Rio de Janeiro, Congresso Nacional, STF, Palácio do Planalto. "
        "Style: editorial documentary, black-and-white or colour press photo aesthetic."
    ),
    "usa": (
        "Niche: USA News carousel (accountability journalism, federal cases, institutions). "
        "Locations: Washington DC, federal courthouses, Capitol building, specific states. "
        "Style: editorial documentary press photo."
    ),
    "higashi": (
        "Niche: Higashi Imobiliária — Brazilian real estate in São José dos Campos SP. "
        "Urban/suburban residential, modern apartments and houses. "
        "NOT mountains, NOT forests unless explicitly in the slide."
    ),
}


def _call_haiku(user_message: str) -> str:
    """Call Claude Haiku via direct API. Returns text or empty string on failure."""
    if not ANTHROPIC_KEY:
        return ""
    try:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message}],
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
        print(f"  prompt_builder Haiku call failed: {e}")
        return ""


def _fallback_prompt(context_image_query: str, niche: str) -> str:
    """Build a rule-compliant prompt without LLM when key is missing."""
    niche_style = {
        "opc": "real construction job site, workers in safety gear, raw materials visible",
        "brazil": "editorial documentary press photo, Brasília or relevant location",
        "usa": "editorial documentary press photo, federal building or relevant US location",
        "higashi": "São José dos Campos SP Brazil residential exterior or interior",
    }.get(niche, "editorial documentary photograph")
    return (
        f"Ultra photorealistic documentary photograph of {context_image_query}, "
        f"{niche_style}, natural daylight or on-site lighting, "
        f"no illustration, no 3D render, no cartoon, no plastic surfaces, "
        f"no CGI, no studio backdrop, no text in image, shot on professional DSLR."
    )


def _save_prompt(prompt: str, slide_num, content_word: str, work_dir: str) -> None:
    """Save prompt text to resources/prompts/prompt_slide{N}_{word}.txt."""
    out = Path(work_dir) / "resources" / "prompts" / f"prompt_slide{slide_num}_{content_word}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")


def build_image_prompt(
    slide_text: str,
    context_image_query: str,
    niche: str = "opc",
    slide_num: int = 0,
    subject_type: str = "place",
    work_dir: Optional[str] = None,
    save: bool = False,
) -> str:
    """Generate a specific, photorealistic AI image prompt for one slide.

    Args:
        slide_text:           The slide's visible body copy (what it says).
        context_image_query:  The existing search query string for this slide.
        niche:                'opc' | 'brazil' | 'usa' | 'higashi'
        slide_num:            Slide number (used in saved filename).
        subject_type:         'person' returns '' immediately (bio-initials rule).
        work_dir:             If provided with save=True, writes prompt to disk.
        save:                 Whether to persist prompt to resources/prompts/.

    Returns:
        Prompt string, or '' if the slide is a named person (use bio-initials instead).
    """
    # Named-person rule: never AI-generate a face
    if subject_type == "person":
        return ""

    niche_ctx = _NICHE_CONTEXT.get(niche, "")
    user_msg = f"""{niche_ctx}

Slide {slide_num} content:
\"\"\"{slide_text.strip()[:600]}\"\"\"

Current search query (may be too generic — improve on it):
{context_image_query}

Generate a specific AI image generation prompt for this slide that will produce
a photorealistic photo matching EXACTLY what this slide is about."""

    prompt = _call_haiku(user_msg)

    # Fallback if Haiku unavailable or returns skip signal
    if not prompt or prompt == "SKIP_NAMED_PERSON":
        if prompt == "SKIP_NAMED_PERSON":
            return ""
        prompt = _fallback_prompt(context_image_query, niche)

    # Enforce photorealistic suffix if Haiku omitted it
    if "photorealistic" not in prompt.lower() and "documentary" not in prompt.lower():
        prompt += (
            " Ultra photorealistic documentary photograph style, "
            "no illustration, no 3D render, no cartoon, no plastic surfaces."
        )

    if save and work_dir:
        content_word = re.sub(r"[^a-z0-9]+", "_",
                              context_image_query.lower()).strip("_")[:20] or "image"
        _save_prompt(prompt, slide_num, content_word, work_dir)

    return prompt


def extract_slide_texts(html: str) -> dict:
    """Return {slide_num: plain_text} for carousel HTML.

    Strategy (in order):
    1. Pull text from .content / .body / .tip-body / .stat-body divs — these hold actual copy
    2. Fall back to <h1>/<h2>/<p> tags
    3. Last resort: .slide wrappers (original approach)
    Deduplicates to handle v1/v2/v3 variants of the same slide.
    """
    def _strip(s: str) -> str:
        t = re.sub(r"<[^>]+>", " ", s)
        return re.sub(r"\s+", " ", t).strip()

    # Strategy 1: .content and related copy-bearing divs
    content_blocks = re.findall(
        r'<div[^>]*class="[^"]*(?:content|body|tip-body|stat-body|list-body)[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE,
    )
    texts = []
    for block in content_blocks:
        t = _strip(block)
        if len(t) > 15:
            texts.append(t)

    # Deduplicate while preserving order (v1/v2/v3 produce identical copies)
    seen = set()
    unique_texts = []
    for t in texts:
        key = t[:60]
        if key not in seen:
            seen.add(key)
            unique_texts.append(t)

    if unique_texts:
        return {i: t for i, t in enumerate(unique_texts, start=1)}

    # Strategy 2: heading + paragraph tags
    headings_paras = re.findall(r'<(?:h[1-3]|p)[^>]*>(.*?)</(?:h[1-3]|p)>', html, re.DOTALL | re.IGNORECASE)
    texts2 = []
    seen2 = set()
    for block in headings_paras:
        t = _strip(block)
        if len(t) > 15:
            key = t[:60]
            if key not in seen2:
                seen2.add(key)
                texts2.append(t)
    if texts2:
        return {i: t for i, t in enumerate(texts2, start=1)}

    # Strategy 3: .slide wrappers (original fallback)
    blocks = re.findall(
        r'<(?:div|section)[^>]*class="[^"]*slide[^"]*"[^>]*>(.*?)</(?:div|section)>',
        html, re.DOTALL | re.IGNORECASE,
    )
    result = {}
    seen3 = set()
    idx = 1
    for block in blocks:
        t = _strip(block)
        if len(t) > 10:
            key = t[:60]
            if key not in seen3:
                seen3.add(key)
                result[idx] = t
                idx += 1
    return result


def rebuild_prompts_from_html(html_path: str, niche: str, work_dir: str) -> dict:
    """Extract slide texts from a rendered carousel HTML and rebuild all prompts.

    Reads slide divs, extracts text content, calls build_image_prompt for each,
    saves to resources/prompts/. Returns {slide_num: prompt_text}.

    Used by fix_existing_images.py to regenerate prompts before re-fetching images.
    """
    html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    results = {}

    # Find all .slide divs (simple regex — good enough for our templates)
    slide_blocks = re.findall(
        r'<div[^>]*class="[^"]*slide[^"]*"[^>]*>(.*?)</div\s*>',
        html, re.DOTALL | re.IGNORECASE
    )
    # Also try section tags
    if not slide_blocks:
        slide_blocks = re.findall(
            r'<section[^>]*>(.*?)</section>',
            html, re.DOTALL | re.IGNORECASE
        )

    for i, block in enumerate(slide_blocks, start=1):
        # Strip HTML tags to get plain text
        text = re.sub(r"<[^>]+>", " ", block)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 20:
            continue
        # Use first 80 chars of text as a rough context_image_query
        ciq = text[:80].lower()
        prompt = build_image_prompt(
            slide_text=text,
            context_image_query=ciq,
            niche=niche,
            slide_num=i,
            work_dir=work_dir,
            save=True,
        )
        if prompt:
            results[i] = prompt
            print(f"  Prompt rebuilt for slide {i}: {prompt[:80]}...")

    return results
