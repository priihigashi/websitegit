#!/usr/bin/env python3
"""
carousel_builder.py — Generates carousel HTML from template + topic, renders PNGs.
Uses Claude Haiku for content generation, Playwright for rendering.
Also generates Instagram caption following Priscila's copy rules.
"""
import datetime, gzip, json, os, re, subprocess, sys, time, urllib.request, urllib.parse
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
APIFY_KEY      = os.environ.get("APIFY_API_KEY", "")

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


def _vision_accept(local_path, query, label):
    """Return True if Vision says image matches query. Logs the verdict.
    Empty path or empty query short-circuits to True so we never block on
    missing inputs."""
    if not local_path or not query:
        return True
    try:
        ok, reason = _vision_validate(local_path, query)
        if ok:
            print(f"  Vision OK ({label}): {reason[:120]}")
        else:
            print(f"  Vision REJECT ({label}): {reason[:120]}")
            try:
                __import__("os").unlink(local_path)
            except Exception:
                pass
        return ok
    except Exception as e:
        print(f"  Vision check error ({label}, non-fatal): {e}")
        return True


def _claude_with_fallback(prompt, *, max_tokens, timeout=60, context=""):
    """Try the Claude→OpenAI→Gemini cascade; if the shared module is unavailable,
    fall back to the raw HTTP call this script originally used."""
    if _llm_text_cascade:
        try:
            return _llm_text_cascade(prompt, model_tier="haiku",
                                     max_tokens=max_tokens, context=context)
        except Exception as e:
            print(f"  [carousel_builder] cascade failed ({e}) — trying raw Claude HTTP")
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
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
MANDATORY RULES — follow these exactly:

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
   - Industry data must cite the org (Houzz, NAHB, Remodeling Magazine).
   - Use "according to [source]" or put the source on the slide itself.
   - OPC's own job data can be cited as "South Florida contractor data, 2023-2025"

6. Tone: Direct, matter-of-fact, no jargon, no hype.
   - Write like a contractor explaining something to a homeowner over coffee.
   - No exclamation marks in slide text. One max in caption.
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
11. Caption is written AFTER the carousel slides are finalized — never before.
    Caption complements the slides, it does not summarize them.
12. CAPTION HASHTAG RULES (shadow-ban prevention):
    - NEVER use party abbreviations as hashtags: no #PT, #PL, #PSDB, #MDB, #Bolsonaro, #Lula.
    - NEVER @-tag or hashtag politicians by name.
    - Use ONLY topic hashtags: #politicabrasileira, #senadofederal, #fiscalizacao, #direitoshumanos.
    - Reason: party hashtags trigger shadow-ban on Instagram. Attribution-without-traffic rule.
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


def generate_carousel_content(topic, niche, template_key=None, brief=""):
    # Special templates checked BEFORE the generic niche short-circuit
    if template_key == "dados-ou-agenda":
        return generate_dados_content(topic, brief)
    if niche in ("brazil", "usa"):
        return generate_brazil_content(topic, brief)
    if not template_key:
        template_key = OPC_TEMPLATE if niche == "opc" else BRAZIL_TEMPLATE

    tmpl = TEMPLATES.get(niche, {}).get(template_key)
    if not tmpl:
        print(f"  No template for {niche}/{template_key}")
        return None

    lang = "Portuguese (Brazilian)" if niche == "brazil" else "English"
    copy_rules = OPC_COPY_RULES if niche == "opc" else BRAZIL_COPY_RULES

    prompt = f"""You are a content writer for an Instagram carousel.
Generate content for a {tmpl['slides']}-slide carousel about: "{topic}"

Series: {tmpl['series']}
Structure: {tmpl['structure']}
Language: {lang}

{copy_rules}

Return ONLY a JSON object with these fields:
{{
  "headline": "3-4 word cover headline (ALL CAPS, punchy) — prefer a number, cost, or named risk when possible. GOOD: '3 COSTLY MISTAKES', '$20K TRAP', 'AVOID THIS COST'. BAD: 'THINGS TO KNOW', 'TIPS AND TRICKS', 'WHAT TO DO'.",
  "accent_word": "1 word from headline to highlight in accent color",
  "subhead": "1 sentence under the headline — MUST contain at least one of: a specific number, a dollar amount, or a named consequence/fear. BANNED: generic phrases like 'what to look for', 'things you should know', 'tips for'. Good: '$20K mistake most homeowners make before signing' | '3 red flags contractors hope you miss'",
  "slide2_headline": "3-4 word headline for slide 2",
  "slide2_stat": "a big number or stat WITH QUALIFIER (e.g. 'UP TO $15K' not '$12K') — stat_number MUST be 40 characters or fewer including spaces",
  "slide2_label": "1 line explaining the stat — include source name",
  "slide3_items": [
    {{"title": "Item 1 title", "sub": "1 line detail with cost range if applicable"}},
    {{"title": "Item 2 title", "sub": "1 line detail"}},
    {{"title": "Item 3 title", "sub": "1 line detail"}}
  ],
  "slide4_headline": "3-4 word tip/action headline — if the slide content is about risks, warnings, red flags, mistakes to avoid, or things that can go wrong, the label MUST be one of: RED FLAG, WATCH OUT, or AVOID THIS. NEVER use THE PRO MOVE, PRO TIP, or EXPERT ADVICE on warning slides",
  "slide4_body": "2-3 sentences explaining the tip — educational, no promises",
  "mentioned_people": [
    {{"name": "Full Name", "role_en": "role / why they're named", "slide": 4, "image_hint": "Wikipedia or editorial headshot search term"}}
  ],
  "sources": [
    "Source 1 — description",
    "Source 2 — description",
    "Source 3 — description",
    "Oak Park Construction — South Florida contractor data, 2023-2025"
  ],
  "cta": "2-3 word call to action (e.g. SAVE THIS.)",
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
      "context_image_query": "Pexels/Wikimedia search for the STAT on SLIDE 2 (slide2_stat field) — must show the material or process the number is about. MINIMUM 4 words. GOOD: 'concrete driveway residential pour south florida', 'bathroom tile frameless shower door installation', 'shiplap wood accent wall interior residential'. BAD (banned): 'construction work', 'house', 'renovation', 'contractor', 'kitchen', 'bathroom', 'home improvement'. Must be specific to the stat subject, not the overall topic."
    }},
    {{
      "slide": 3,
      "visual_hint": "context-image or none — use context-image for at least 1 of the 3 list items in slide3_items",
      "context_image_query": "Pexels/Wikimedia search for the LIST items on SLIDE 3 (slide3_items field) — different subject from slide 2 query. Must include material/action + location. GOOD: 'roof shingles GAF installation aerial residential', 'framing wood stud wall addition oak park illinois'. BAD: 'construction', 'building', 'outdoor work'. Query MUST differ from slide 2 query."
    }},
    {{
      "slide": 4,
      "visual_hint": "context-image or none — use context-image when slide4_body describes a specific tool, material, or technique",
      "context_image_query": "Pexels/Wikimedia search for the TIP on SLIDE 4 (slide4_body field) — show the solution or tool being described. Different subject from slides 2 and 3. GOOD: 'contractor measuring kitchen cabinet installation south florida', 'outdoor kitchen pergola concrete patio residential'. BAD: 'contractor', 'renovation', 'home project'. Query MUST differ from both slide 2 and slide 3 queries."
    }}
  ],
  "clip_suggestions": [
    {{
      "slide": 1,
      "youtube_query": "YouTube search for COVER — construction tutorial or timelapse matching the topic. GOOD: 'roof shingles installation timelapse 2024', 'concrete driveway pour residential how to', 'kitchen cabinet install frameless tutorial'. Use 4-6 words. Avoid brand names unless they are the subject.",
      "instagram_query": "Instagram/hashtag phrasing for the same subject — lowercase, no hashtag symbol. e.g. 'residential roofing installation'",
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
      "youtube_query": "YouTube search matching SLIDE 3 subject — different query from cover. e.g. 'how to inspect roof before buying home', 'permit process residential addition explained'",
      "instagram_query": "Instagram phrasing for slide 3 subject — lowercase, no hashtag symbol",
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
- Caption hook = first line visible in feed — make it a question or surprising fact
- NEVER promise what OPC does for clients
- slides[]: emit context-image for at least 2 of the 3 middle slides — never all none
- slide4_body must describe what is happening in the visual (not generic advice)
- context_image_query: BANNED words that make queries too generic and WILL fail stock search — never use alone or as the whole query: "construction", "house", "home", "building", "renovation", "contractor", "kitchen", "bathroom", "outdoor", "indoor", "work", "project". Always combine with material type + location (e.g. "oak park illinois", "south florida", "residential") + action verb (installation, pour, framing, remodel). Minimum 4 words per query. A generic query means the pipeline falls back to AI images — avoid this.
- context_image_query UNIQUENESS: Each slide's context_image_query MUST describe a DIFFERENT visual subject. Slides 2, 3, and 4 must each have a distinct query. NEVER reuse or rephrase a query from another slide. If you find yourself writing the same query twice, stop and change one of them to show a different material, location, or action."""

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
                context=f"carousel_builder.opc(attempt {attempt+1})",
            )
        except Exception as e:
            print(f"  LLM cascade failed (OPC, attempt {attempt+1}): {e}")
            continue

        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            print(f"  Failed to parse OPC carousel content (attempt {attempt+1})")
            continue

        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            print(f"  OPC JSON parse error (attempt {attempt+1}): {e}")
            continue

    print(f"  OPC content generation failed after 2 attempts for: {topic}")
    return None


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


def generate_brazil_content(topic, brief=""):
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
      "visual_hint": "bio-card|context-image|place|event|product-photo|none"
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
  "none" → text-only, max 1 consecutive allowed
First choice for Brazilian institutions: Agência Brasil CC BY 3.0 search terms. International subjects: English search terms.
BANNED context_image_query patterns: NEVER copy words from heading_pt or heading_en into the query. The query is a stock photo search term — it must be a place/institution/person name, NOT a phrase from the slide copy. BAD: "Se comparar igual com igual" or "O ponto que importa". GOOD: "Conselho Nacional de Justiça STF fachada" or "STF Brasília Supremo Tribunal Federal". For comparison slides about judicial spending: always reference a specific court or government body (STF, CNJ, Câmara, TCU).

CLIP SUGGESTIONS + MOTION PROMPTS RULE (non-negotiable):
The motion pipeline runs an 8-tier source cascade per clip: YouTube (Apify) → Instagram (Apify) → Pexels → Pixabay → Archive.org → Wikimedia Commons → stock scrapers → Ken Burns zoom (last resort). You must write DIFFERENT phrasing per tier so each tier can succeed even if the others fail.

QUERY QUALITY RULE: Every query must be specific enough that a researcher could find the RIGHT clip — not just any clip. A good youtube_query for a slide about "Flávio Bolsonaro CPI 2021" is "Flávio Bolsonaro CPI senado 2021 depoimento" not "Bolsonaro corruption". For a Congress scene: "Câmara dos Deputados votação sessão 2023" not just "congress". Include: full name (if person) + year + context keyword (hearing/speech/vote/signing). Generic queries produce unrelated clips that don't match the story.

For every slide that would benefit from motion (cover + any slide naming a speech, law, institution, event, leader, or iconic moment):
  - youtube_query   → SPECIFIC: full name + year + event type. Best for speeches, press conferences, hearings. e.g. "Viktor Orbán concede derrota eleição Hungria 2026"
  - instagram_query → lowercase, hashtag-friendly, creator-reel phrasing. e.g. "hungria eleicao 2026 orbán perdeu"
  - pexels_query    → stock-safe: place/institution/event, NO proper names, NO party names. e.g. "parliament building Budapest exterior"
  - pixabay_query   → different wording than pexels_query (avoid duplicate failure). e.g. "European parliament vote session"
  - archive_query   → public-domain / archival phrasing (vintage footage, historical film). e.g. "Hungary Budapest 1990 democratic transition archival"
  - wikimedia_query → CC-licensed historical or institutional footage. e.g. "Hungarian National Assembly Budapest"
  - motion_prompt   → 5-second directorial note: camera move + mood + framing (e.g. "slow push-in on Brasília facade, dusk, cinematic, 24mm", "archival grain, slight zoom on signing ceremony"). This drives Remotion animation + serves as AI-video prompt if we escalate to Runway/Kling.
  - photo_query     → Wikipedia/Wikimedia search term for a CC still photo used as slide background. For people: English full name. For places: landmark name. This is the PRIMARY source — always populate.
  - photo_bg_position → CSS background-position for the crop (default "center 20%"). Use "center top" for portraits, "50% 40%" for buildings.
  - motion_renderer → always "kenburns" for Brazil native template. "kenburns" = Playwright records CSS KB zoom animation on the `.kb-bg` background layer only (text stays static). NOT ffmpeg on the full PNG. Never omit.
  - visual_hint     → same values as slides.visual_hint. Determines whether stock tiers are allowed (stock skips for bio-card).

If NO tier could plausibly succeed (hyper-local story, no public footage, no place to film) return an empty clip_suggestions array — do not invent false queries. Ken Burns floor will still animate the poster image, so every cover gets motion regardless.

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
                context=f"carousel_builder.brazil(attempt {attempt+1})",
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
    if not OPENAI_KEY or not prompt:
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
            continue
        ok = _remove_background_with_inference_sh(str(src_abs), str(out_abs))
        if not ok:
            ok = _remove_background_with_replicate(str(src_abs), str(out_abs))
        if ok:
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
    """Download a photo from a Drive viewer URL to dest_path. Returns dest_path or ''."""
    try:
        import re as _re
        m = _re.search(r"/d/([A-Za-z0-9_-]+)", drive_url)
        if not m:
            m = _re.search(r"[?&]id=([A-Za-z0-9_-]+)", drive_url)
        if not m:
            return ""
        file_id = m.group(1)
        download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
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


def fetch_all_media(content, niche, work_dir, brief=""):
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
            match = match_opc_photo(topic_text)
            if match and match.get("drive_url"):
                dest = str(img_dir / "opc_catalog_cover.jpg")
                dl = _download_drive_photo(match["drive_url"], dest)
                if dl:
                    paths["cover"] = dl
                    paths["provenance"]["cover"] = {
                        "path": dl, "provider": "opc_catalog",
                        "source_type": "real_photo",
                        "query": match.get("description", ""),
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

    def _set_slide(slide_idx, rel_path, provider, source_type, query="", prompt=""):
        paths["slides"][slide_idx] = rel_path
        paths["provenance"]["slides"][str(slide_idx)] = {
            "path": rel_path,
            "provider": provider,
            "source_type": source_type,
            "query": query,
            "prompt": prompt,
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
                    if rel and _vision_accept(rel, search_q, "cover/library"):
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
        if not paths["cover"] and subject_type != "person":
            if _IMAGE_PROVIDERS_AVAILABLE:
                fresh_prompt = _build_img_prompt(
                    slide_text=search_q, context_image_query=search_q,
                    niche=niche, slide_num=1, subject_type=subject_type,
                    work_dir=work_dir, save=True, brief=brief,
                ) or ai_prompt
                cover_fname = _make_img_filename(search_q, "ai", 1)
                c, used_prov = _gen_ai_image(fresh_prompt, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, f"cover/{used_prov}"):
                    _set_cover(c, used_prov, "ai", query=search_q, prompt=fresh_prompt)
            elif ai_prompt:
                # Legacy fallback when image_providers not available
                c = _generate_gemini_image(ai_prompt, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, "cover/gemini"):
                    _set_cover(c, "gemini", "ai", query=search_q, prompt=ai_prompt)
                if not paths["cover"]:
                    c = _generate_seedream_image(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/seedream"):
                        _set_cover(c, "seedream", "ai", query=search_q, prompt=ai_prompt)
                if not paths["cover"]:
                    c = _generate_ai_cover(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/dall-e-3"):
                        _set_cover(c, "dall-e-3", "ai", query=search_q, prompt=ai_prompt)
                if not paths["cover"]:
                    c = _generate_replicate_sdxl(ai_prompt, work_dir, cover_fname)
                    if c and _vision_accept(c, search_q, "cover/sdxl"):
                        _set_cover(c, "sdxl", "ai", query=search_q, prompt=ai_prompt)

        # Step 3 — real-photo fallback (Wiki CC → Pexels → Pixabay)
        # Triggered only when AI cascade exhausted for non-persons,
        # OR for persons whose Wikimedia REST lookup missed.
        if not paths["cover"] and search_q:
            c = _fetch_person_photo(search_q, work_dir, cover_fname)
            # Wiki for non-persons gets Vision check; person path is exact-name match (skip)
            if c and (subject_type == "person" or _vision_accept(c, search_q, "cover/wikimedia")):
                _set_cover(c, "wikimedia", "cc", query=search_q)
            if not paths["cover"] and subject_type != "person":
                c = _fetch_pexels_image(search_q, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, "cover/pexels"):
                    _set_cover(c, "pexels", "stock", query=search_q)
            if not paths["cover"] and subject_type != "person":
                c = _fetch_pixabay_image(search_q, work_dir, cover_fname)
                if c and _vision_accept(c, search_q, "cover/pixabay"):
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
    # Applies only to slides with visual_hint == "context-image"; bio-cards
    # are rendered separately from mentioned_people[*].image_hint.
    for i, slide in enumerate(content.get("slides", []), start=2):
        if slide.get("visual_hint") != "context-image":
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
        # Tier 0 — library-first reuse + scene-preserving enhancement
        if _IMAGE_LIBRARY_AVAILABLE:
            try:
                lib_hit = _search_library(cq, niche)
                if lib_hit:
                    rel = _enhance_library_image(lib_hit.get("drive_url", ""), work_dir, fname, cq)
                    if rel and _vision_accept(rel, cq, f"slide{i}/library"):
                        _set_slide(i, rel, "library", "library", query=cq, prompt="scene-lock enhance from library")
                        accepted = True
                        if _mark_library_used:
                            _mark_library_used(lib_hit.get("row_idx", 0), f"{niche}:{cq[:40]}")
            except Exception as _e:
                _log_failure("image_library/slide_lookup", _e)

        # Tier 1: AI cascade — NB2 → Seedream 4.5 → Seedream 5.0 → Gemini → SDXL → DALL-E
        if not accepted and _IMAGE_PROVIDERS_AVAILABLE:
            fresh_prompt = _build_img_prompt(
                slide_text=cq, context_image_query=cq,
                niche=niche, slide_num=i, work_dir=work_dir, save=True, brief=brief,
            ) or ai_prompt
            fname = _make_img_filename(cq, "ai", i)
            img_path, used_prov = _gen_ai_image(fresh_prompt, work_dir, fname)
            if img_path and _vision_accept(img_path, cq, f"slide{i}/{used_prov}"):
                print(f"  Slide {i}: {used_prov} image for '{cq[:50]}'")
                _set_slide(i, img_path, used_prov, "ai", query=cq, prompt=fresh_prompt)
                accepted = True
            else:
                img_path = ""
        else:
            # Legacy fallback when image_providers not available
            img_path = _generate_gemini_image(ai_prompt, work_dir, fname)
            if img_path and _vision_accept(img_path, cq, f"slide{i}/gemini"):
                _set_slide(i, img_path, "gemini", "ai", query=cq, prompt=ai_prompt)
                accepted = True
            else:
                img_path = ""
            if not accepted:
                img_path = _generate_seedream_image(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/seedream"):
                    _set_slide(i, img_path, "seedream", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""
            if not accepted:
                img_path = _generate_replicate_sdxl(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/sdxl"):
                    _set_slide(i, img_path, "sdxl", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""
            if not accepted:
                img_path = _generate_ai_cover(ai_prompt, work_dir, fname)
                if img_path and _vision_accept(img_path, cq, f"slide{i}/dall-e-3"):
                    _set_slide(i, img_path, "dall-e-3", "ai", query=cq, prompt=ai_prompt)
                    accepted = True
                else:
                    img_path = ""

        # Tier 2: real-photo fallback (Wiki CC → Pexels → Pixabay) — only when AI exhausted
        if not accepted:
            img_path = _fetch_person_photo(cq, work_dir, fname)
            if img_path and _vision_accept(img_path, cq, f"slide{i}/wikimedia"):
                print(f"  Slide {i}: Wikimedia fallback for '{cq[:50]}'")
                _set_slide(i, img_path, "wikimedia", "cc", query=cq, prompt=ai_prompt)
                accepted = True
            else:
                img_path = ""
        if not accepted:
            img_path = _fetch_pexels_image(cq, work_dir, fname)
            if img_path and _vision_accept(img_path, cq, f"slide{i}/pexels"):
                print(f"  Slide {i}: Pexels fallback for '{cq[:50]}'")
                _set_slide(i, img_path, "pexels", "stock", query=cq, prompt=ai_prompt)
                accepted = True
            else:
                img_path = ""
        if not accepted:
            img_path = _fetch_pixabay_image(cq, work_dir, fname)
            if img_path and _vision_accept(img_path, cq, f"slide{i}/pixabay"):
                print(f"  Slide {i}: Pixabay fallback for '{cq[:50]}'")
                _set_slide(i, img_path, "pixabay", "stock", query=cq, prompt=ai_prompt)
                accepted = True

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


def fetch_clips(content, work_dir):
    """Download video clips for motion version. Returns (clips, clip_failures).

    clips       = {slide_idx: abs_clip_path} for every slot that succeeded.
    clip_failures = {slide_idx: slot_name}   for every slot that exhausted all tiers.
    Callers must unpack both: clips, clip_failures = fetch_clips(content, work_dir)

    Distribution: cover + up to 2 evenly spaced middle slides (never sources).
    Source chain per slot is delegated to motion_sources.fetch_clip_with_fallback:
      1. Apify YouTube  →  2. Apify Instagram  →  3. Pexels
      4. Pixabay        →  5. Archive.org      →  6. Wikimedia Commons
      7. (stock scrapers — placeholder)         →  empty = Ken Burns fallback by caller.

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

    slides = content.get("slides", [])
    n_slides = len(slides)

    by_slide = {c.get("slide", 0): c for c in suggestions}

    # Distribution: cover always first. Then pick up to 2 evenly from middle slides (not last).
    clip_slots = []
    cover_suggestion = by_slide.get(1) or (suggestions[0] if suggestions else None)
    if cover_suggestion:
        clip_slots.append(("cover", 1, cover_suggestion))

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
            print(f"  Clip slot '{slot_name}': every tier missed — Ken Burns floor on PNG")
            clip_failures[slide_idx] = slot_name

    print(f"  fetch_clips: {len(clips)}/{len(clip_slots)} clip(s) ready: {list(clips.keys())}")
    if clip_failures:
        print(f"  fetch_clips: {len(clip_failures)} slot(s) failed — placeholder div will render in motion HTML: {list(clip_failures.keys())}")
    return clips, clip_failures


def build_motion_html(content, niche, topic_slug, work_dir, clips, media_paths=None, clip_failures=None):
    """Generate per-slide motion HTML files for Playwright video recording.

    Every-other-slide rule (cover + even-indexed middles, never sources):
      - Cover always gets a motion file.
      - Middle slides: every other one gets a motion file (slide 3, 5, ...).
      - Sources slide: never gets motion.

    Each motion HTML has:
      - KB bg: CSS Ken Burns zoom on the background IMAGE layer only — text layer is z-index 2
        and stays perfectly static. Ken Burns never touches text/logo/arrows.
      - Clip sticker: looping <video> in the clip-frame/sticker-slot when a clip is available.
        When no clip → clip-frame is omitted, only KB bg animates.

    Works for all niches (brazil, usa, opc). Existing cover.html is NOT modified.
    Returns list of (slide_idx, html_path) tuples.
    """

    results = []
    slides = content.get("slides", [])
    n_slides = len(slides)   # middle slides only; cover=1, sources=n_slides+2
    total_slides = n_slides + 2  # cover + middles + sources

    css = _brazil_motion_css()

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # Which slide indices get motion: cover(1) + every other middle (3,5,...) — never sources
    motion_indices = [1] + [i for i in range(3, n_slides + 2, 2)]

    for slide_idx in motion_indices:
        clip_path = clips.get(slide_idx)
        rel_clip = os.path.relpath(clip_path, work_dir) if clip_path else None
        html_body = ""

        # Clip sticker block — real clip when fetched, placeholder when all tiers failed
        clip_block = ""
        if rel_clip:
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
<div class="slide slide-cover motion-slide opc-cover">
  <div class="kb-bg" {bg_style}></div>
  {clip_block}
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
<div class="slide slide-cover motion-slide">
  <div class="kb-bg" {bg_style}></div>
  {clip_block}
  <div class="slide-content">
    <div class="tag">Quem decidiu isso?</div>
    <div class="cover-date">{cover_date}</div>
    <div class="cover-hl">{cover_hl}</div>
    <div class="cover-en">{cover_en}</div>
    <div class="swipe">SWIPE &#8594;</div>
  </div>
</div>"""
        else:
            # Middle slide — content["slides"] holds middle slides only (cover/sources are separate).
            # slide_idx=2 → slides[0]. Clamp to avoid off-by-one silent empty.
            data_idx = max(0, min(slide_idx - 2, len(slides) - 1)) if slides else 0
            slide_data = slides[data_idx] if slides else {}
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
<div class="slide motion-slide">
  <div class="kb-bg" {bg_style}></div>
  {clip_block}
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
        clip_info = f"clip: {os.path.basename(clip_path)}" if clip_path else "no clip — KB bg only"
        print(f"  Motion HTML: {fname} ({clip_info})")

    return results


def _brazil_motion_css():
    """CSS for per-slide motion HTML files — Ken Burns animation + clip frame styling."""
    return """
*{box-sizing:border-box;margin:0;padding:0}
:root{--ob:#0E0D0B;--pa:#F2ECE0;--ca:#C9A84C;--gr:#7A7267;--W:1080px;--H:1350px;--P:108px}
body{background:var(--ob);overflow:hidden}
.slide{width:var(--W);height:var(--H);background:var(--ob);color:var(--pa);position:relative;overflow:hidden;font-family:'Inter',sans-serif}
.kb-bg{position:absolute;inset:0;background-size:cover;background-position:center top;
       animation:kb-zoom 5s ease-in-out forwards;transform-origin:center center;}
@keyframes kb-zoom{0%{transform:scale(1);}100%{transform:scale(1.08);}}
.slide-content{position:relative;z-index:2;padding:var(--P);height:100%;display:flex;flex-direction:column;}
.tag{font-family:'JetBrains Mono',monospace;font-size:26px;color:var(--gr);letter-spacing:.06em;text-transform:uppercase;margin-bottom:28px}
.accent{color:var(--ca)}
.cover-date{font-family:'JetBrains Mono',monospace;font-size:24px;color:var(--gr);margin-bottom:40px}
.cover-hl{font-family:'Fraunces',serif;font-weight:700;font-size:88px;line-height:1.0;text-transform:uppercase;margin-bottom:20px;text-shadow:0 2px 20px rgba(0,0,0,.8);}
.cover-en{font-family:'Inter',sans-serif;font-style:italic;font-size:30px;color:var(--gr)}
.slide-hl{font-family:'Fraunces',serif;font-weight:700;font-size:68px;line-height:1.1;text-transform:uppercase;margin-bottom:12px;text-shadow:0 2px 20px rgba(0,0,0,.8);}
.slide-en{font-family:'Inter',sans-serif;font-style:italic;font-size:26px;color:var(--gr);margin-bottom:28px}
.swipe{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);right:var(--P)}
/* CLIP FRAME — z-index:1 so it sits BEHIND .slide-content (z-index:2). Text always wins. */
.clip-frame{position:absolute;top:120px;right:var(--P);width:340px;height:420px;
            z-index:1;border:3px solid var(--ca);background:#000;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.7);}
.clip-frame-mid{top:auto;bottom:200px;}
.clip-stamp{font-family:'JetBrains Mono',monospace;font-size:18px;color:var(--ca);
            background:var(--ob);padding:6px 12px;position:absolute;top:-1px;right:-1px;
            border:1px solid var(--ca);z-index:3;letter-spacing:.05em;}
.clip-video{width:100%;height:100%;object-fit:cover;display:block;}
.clip-frame-missing{border:3px dashed #ff4444;background:#1a0000;}
.clip-missing-badge{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  text-align:center;color:#ff6666;font-family:'JetBrains Mono',monospace;font-size:15px;
  line-height:1.5;padding:12px;background:rgba(0,0,0,.7);}
.clip-missing-badge small{font-size:12px;color:#ff9999;}
"""


def build_html(content, niche, topic_slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    if niche == "opc":
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
        return _build_brazil_html(content, topic_slug, work_dir, handle=handle, media_paths=media_paths)
    return None


def _cap34(text: str) -> str:
    """Hard-cap list item titles at 34 chars (reviewer limit) at word boundary."""
    if len(text) <= 34:
        return text
    t = text[:34]
    return t[:t.rfind(" ")].rstrip() if " " in t else t


def _build_opc_html(content, slug, work_dir, media_paths=None):
    hl = content["headline"]
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

    s4_hl = content.get("slide4_headline", "THE PRO MOVE")
    s4_accent = s4_hl.split()[-1] if s4_hl else "MOVE"
    opc_slides_meta = content.get("slides", []) if isinstance(content.get("slides", []), list) else []

    def _opc_context_slot(slide_num, fallback_label):
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
                f'<img src="{img_path}" alt="{fallback_label}">'
                '</div>'
            )
        # No image: if hint is explicitly "none", omit the slot entirely
        if visual_hint == "none":
            return ""
        # No image but slot is expected: show a branded placeholder (not raw query text)
        return (
            f'<div class="context-img-slot context-img-placeholder" data-query="{query_attr}">'
            f'<div class="ctx-placeholder-inner">'
            f'<div class="ctx-placeholder-icon">&#9632;</div>'
            f'<div class="ctx-placeholder-label">{fallback_label}</div>'
            f'</div>'
            '</div>'
        )

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
        return f"""
<!-- {v_class.upper()} -->
<div class="slide slide-cover {v_class}">
  {bg_photo_el}
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Tip of the Week · Oak Park Construction</div>
  <div class="headline">{hl_html}</div>
  <div class="body-text">{content["subhead"]}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-stat {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">The Real Number</div>
  <div class="stat-big">{content.get("slide2_stat", "—")}</div>
  <div class="stat-label">{content.get("slide2_label", "")}</div>
  <div class="project-note">What you are seeing here: cost, scope, and site conditions can change this number.</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-list {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">What To Know</div>
  <div class="headline" style="font-size:96px; margin-bottom:36px;">THE <span class="accent">LIST.</span></div>
  {_opc_context_slot(3, "PROCESS IMAGE")}
  <div class="list">
{items_html}  </div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-tip {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Pro Tip</div>
  <div class="tip-label"><span class="tip-arrow">&#9658;</span> The Pro Move</div>
  <div class="tip-big">{s4_hl.replace(s4_accent, f'<span style="color:{s4_accent_style};">{s4_accent}</span>')}</div>
  {_opc_context_slot(4, "TIP IN ACTION IMAGE")}
  <div class="tip-explain">{content.get("slide4_body", "")}</div>
  <div class="arrow">SWIPE &#8594;</div>
  <div class="slide-logo">Oak Park Construction · CBC1263425</div>
</div>

<div class="slide slide-sources {v_class}">
  {sources_bg_el}
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Sources</div>
  <div class="src-head">WHERE THIS<br>COMES <span style="color:{src_accent_style};">FROM.</span></div>
  <div class="src-list">
{sources_html}  </div>
  <div class="save-cta">{cta}</div>
  <div class="footer">
    <span class="handle">@oakparkconstruction</span>
    <span class="license">LIC · CBC1263425</span>
  </div>
</div>
"""

    v1 = variant_block("v1", "#CBCC10", "#CBCC10", "#CBCC10")
    v2 = variant_block("v2", "#0A0A0A", "#CBCC10", "#CBCC10")
    v3 = variant_block("v3", "#F0EBE3", "#F0EBE3", "#F0EBE3")

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
{base_css}
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

    v1 = variant_block("v1")
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
{base_css}
{progress_extra_css}
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


def _build_opc_illustrated_html(content, slug, work_dir, media_paths=None):
    """Illustrated editorial variant:
    keeps OPC typography/colors, adds topic-related image blocks with sketch/line treatment."""
    hl = content["headline"]
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
  <div class="tag">What To Know</div>
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

    v1 = variant_block("v1")
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
{base_css}
{illustrated_css}
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


def _build_opc_cutout_html(content, slug, work_dir, media_paths=None):
    """Cutout sticker editorial variant:
    designed for background-removed PNGs when available, with graceful fallback."""
    hl = content["headline"]
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
  <div class="tag">What To Know</div>
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

    v1 = variant_block("v1")
    v2 = variant_block("v2")
    v3 = variant_block("v3")

    html_path = Path(work_dir) / "cover.html"
    with open(Path(__file__).parent / "opc_tip_base.css") as f:
        base_css = f.read()

    cutout_css = """
.cut-shell { position:relative; overflow:hidden; }
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
  max-height:390px; max-width:68%; object-fit:contain;
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
  max-height:300px; max-width:100%; object-fit:contain;
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
{base_css}
{cutout_css}
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
:root{{--ob:{brand['obsidian']};--pa:{brand['paper']};--ac:{brand['accent']};--mu:{brand['muted']};--W:1080px;--H:1350px;--P:100px}}
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
.cutout-wrap{{width:100%;min-height:220px;max-height:320px;margin:14px 0 16px;display:flex;align-items:flex-end;justify-content:center;overflow:hidden}}
.cutout-img{{max-width:100%;max-height:100%;object-fit:contain;filter:drop-shadow(0 10px 26px rgba(0,0,0,.38)) contrast(1.1) saturate(1.05)}}
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
:root{{--ob:#0A0A0A;--pa:#F0EBE3;--ca:#C9A84C;--gr:rgba(240,235,227,0.45);--rule:rgba(240,235,227,0.12);--W:1080px;--H:1350px;--P:108px}}
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
:root{{--ob:#0A0A0A;--pa:#F0EBE3;--ca:#C9A84C;--gr:rgba(240,235,227,0.45);--rule:rgba(240,235,227,0.12);--W:1080px;--H:1350px;--P:108px}}
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
