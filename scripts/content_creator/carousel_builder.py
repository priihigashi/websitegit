#!/usr/bin/env python3
"""
carousel_builder.py — Generates carousel HTML from template + topic, renders PNGs.
Uses Claude Haiku for content generation, Playwright for rendering.
Also generates Instagram caption following Priscila's copy rules.
"""
import gzip, json, os, re, subprocess, time, urllib.request, urllib.parse
from pathlib import Path

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", "")

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
    if niche in ("brazil", "usa", "sovereign"):
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
  "headline": "3-4 word cover headline (ALL CAPS, punchy)",
  "accent_word": "1 word from headline to highlight in accent color",
  "subhead": "1 sentence under the headline",
  "slide2_headline": "3-4 word headline for slide 2",
  "slide2_stat": "a big number or stat WITH QUALIFIER (e.g. 'UP TO $15K' not '$12K')",
  "slide2_label": "1 line explaining the stat — include source name",
  "slide3_items": [
    {{"title": "Item 1 title", "sub": "1 line detail with cost range if applicable"}},
    {{"title": "Item 2 title", "sub": "1 line detail"}},
    {{"title": "Item 3 title", "sub": "1 line detail"}}
  ],
  "slide4_headline": "3-4 word tip/action headline",
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
  "receipts_needed": ["URL or description of primary source to screenshot as evidence slide"],
  "opposition_confirmation": "Name the opposing political side or outlet that also confirms this fact (leave empty string if not applicable)"
}}

Rules:
- Keep it simple, direct, no jargon
- Stats MUST use ranges (e.g. "$5K-$15K") not exact averages — safer and more honest
- Every stat must name its source in slide2_label or on the sources slide
- Headlines in ALL CAPS
- Caption hook = first line visible in feed — make it a question or surprising fact
- NEVER promise what OPC does for clients"""

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

        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": _prompt}],
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
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            text = resp["content"][0]["text"]
        except Exception as e:
            print(f"  HTTP/JSON error from Claude API (OPC, attempt {attempt+1}): {e}")
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
    }}
  ],
  "clip_suggestions": [
    {{"person_or_topic": "name or topic", "youtube_query": "specific YouTube search for a relevant clip", "slide": 3, "duration_hint": "5-8 seconds", "reason": "why this clip fits this slide"}}
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
- Body text and bullet items (items_pt, facts_pt, context_pt) must be in PORTUGUESE ONLY. Never mix English words into PT sentences. heading_en is a small subtitle only — it is NOT body copy.

COVER VISUAL RULES (apply before filling cover_visual):
subject_type guide:
  "person" → named individual is the main subject. option_a=CC real photo (Wikimedia/Agência Brasil). option_b=AI portrait (Seedream 4.5). recommended=a unless no CC photo exists → then b.
  "place" → country/city is the character (not a specific person). option_a=archival/news CC photo. option_b=AI composition with place name in massive letters + historical leaders fading into letterforms. recommended=b (more graphic, stops scroll on IG).
  "event" → specific law/decision/moment. option_a=document screenshot or news headline crop. option_b=archival texture + event name in bold stamp. recommended=a (receipt journalism visual).
  "concept" → abstract policy/ideology/system. option_a=contextual CC photo. option_b=bold typographic AI composition. recommended=b.

VISUAL-EVERY-OTHER-SLIDE RULE (non-negotiable):
Between cover and sources, never output "visual_hint": "none" on more than 1 consecutive slide.
visual_hint values:
  "bio-card" → slide has named person in mentioned_people (face cards render automatically from that field)
  "context-image" → slide references a specific institution, building, place, event, or document; fill context_image_query with a specific search term (e.g. "Câmara dos Deputados Brasília", "Viktor Orbán 2026", "Supremo Tribunal Federal fachada", "Congresso Nacional aerial")
  "none" → text-only, max 1 consecutive allowed
First choice for Brazilian institutions: Agência Brasil CC BY 3.0 search terms. International subjects: English search terms.

CLIP SUGGESTIONS RULE:
If the topic involves a famous speech, public statement, historical moment, or iconic event that is available on YouTube (e.g. "I Have a Dream", Lula inauguration speech, Orbán victory speech, Lei Áurea signing ceremony), add an entry to clip_suggestions for the most relevant slide. YouTube clips will be cut to 5-8 sec and added to that slide's motion version. If no relevant clip exists, return an empty array.

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

        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
            text = resp["content"][0]["text"]
        except Exception as e:
            print(f"  HTTP/JSON error from Claude API (Brazil, attempt {attempt+1}): {e}")
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


def fetch_all_media(content, niche, work_dir):
    """Download/generate all images needed by this carousel BEFORE build_html().
    Returns dict:
      {"cover": rel_path_or_empty, "slides": {slide_idx: rel_path_or_empty}}
    slide_idx is 1-based (cover=0 implied, slides start at 2 to match enumerate in _build_brazil_html).
    All paths relative to work_dir (e.g. "resources/images/cover.jpg").
    Safe: all failures are caught and return empty string for that slot.
    """
    paths = {"cover": "", "slides": {}}

    # Cover image — try CC photo (option_a), fall back to AI generation (option_b)
    cv = content.get("cover_visual", {})
    if cv:
        search_q = cv.get("option_a", {}).get("search_query", "")
        if search_q:
            cover_path = _fetch_person_photo(search_q, work_dir, "cover.jpg")
            paths["cover"] = cover_path
        if not paths["cover"]:
            opt_b = cv.get("option_b", {})
            ai_prompt = opt_b.get("prompt", "")
            if ai_prompt:
                paths["cover"] = _generate_ai_cover(ai_prompt, work_dir, "cover.jpg")

    # Middle slides — context images. CC photo first; AI fallback if it fails.
    for i, slide in enumerate(content.get("slides", []), start=2):
        if slide.get("visual_hint") == "context-image":
            cq = slide.get("context_image_query", "").strip()
            if not cq:
                continue
            fname = f"slide_{i}_context.jpg"
            img_path = _fetch_person_photo(cq, work_dir, fname)
            if not img_path and OPENAI_KEY:
                # AI fallback: simple editorial prompt from the context query
                ai_prompt = f"Editorial documentary photograph, {cq}, high contrast journalistic style, no text"
                img_path = _generate_ai_cover(ai_prompt, work_dir, fname)
                if img_path:
                    print(f"  Slide {i}: used AI fallback image for '{cq[:50]}'")
            if img_path:
                paths["slides"][i] = img_path

    fetched_total = (1 if paths["cover"] else 0) + len(paths["slides"])
    print(f"  Media fetch: {fetched_total} image(s) ready (cover={bool(paths['cover'])}, slides={list(paths['slides'].keys())})")
    return paths


def build_html(content, niche, topic_slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    if niche == "opc":
        return _build_opc_html(content, topic_slug, work_dir, media_paths=media_paths)
    if niche in ("brazil", "usa", "sovereign"):
        return _build_brazil_html(content, topic_slug, work_dir, handle=handle, media_paths=media_paths)
    return None


def _build_opc_html(content, slug, work_dir, media_paths=None):
    hl = content["headline"]
    accent = content.get("accent_word", hl.split()[-1])
    hl_html = hl.replace(accent, f'<span class="accent">{accent}</span>')

    s2_hl = content.get("slide2_headline", "THE NUMBERS")
    s2_accent = s2_hl.split()[-1] if s2_hl else "NUMBERS"
    s2_html = s2_hl.replace(s2_accent, f'<span class="accent">{s2_accent}</span>')

    items_html = ""
    for i, item in enumerate(content.get("slide3_items", []), 1):
        items_html += f'''    <div class="list-item"><span class="list-num">{i:02d}</span><div><div class="list-text">{item["title"]}</div><div class="list-sub">{item["sub"]}</div></div></div>\n'''

    sources_html = ""
    for i, src in enumerate(content.get("sources", []), 1):
        sources_html += f'    <div class="src-row"><span class="src-num">{i:02d}</span><span>{src}</span></div>\n'

    s4_hl = content.get("slide4_headline", "THE PRO MOVE")
    s4_accent = s4_hl.split()[-1] if s4_hl else "MOVE"

    cta = content.get("cta", "SAVE THIS.")

    cover_img = (media_paths or {}).get("cover", "")
    bg_photo_el = (
        f'<div class="bg-photo" style="background-image:url(\'{cover_img}\');"></div>'
        if cover_img else '<div class="bg-photo"></div>'
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
  <div class="sticker-stamp">▸ TIP</div>
  <div class="sticker-slot">
    <svg class="worker-silhouette" viewBox="0 0 200 260" xmlns="http://www.w3.org/2000/svg">
      <path d="M100 50 C65 50 50 30 50 20 C50 15 55 12 100 12 C145 12 150 15 150 20 C150 30 135 50 100 50 Z" fill="currentColor" opacity="0.9"/>
      <rect x="45" y="48" width="110" height="12" fill="currentColor" opacity="0.95"/>
      <ellipse cx="100" cy="90" rx="32" ry="38" fill="currentColor"/>
      <path d="M60 140 C60 120 75 110 100 110 C125 110 140 120 140 140 L140 260 L60 260 Z" fill="currentColor"/>
      <rect x="92" y="150" width="16" height="40" fill="#0A0A0A" opacity="0.3"/>
    </svg>
    <div class="sticker-placeholder">ON-SITE · SWAP-IN</div>
  </div>
  <div class="arrow">SWIPE →</div>
  <div class="slide-logo">Oak Park · CBC1263425</div>
</div>

<div class="slide slide-stat {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">The Real Number</div>
  <div class="headline">{s2_html}</div>
  <div class="stat-big">{content.get("slide2_stat", "—")}</div>
  <div class="stat-label">{content.get("slide2_label", "")}</div>
  <div class="arrow">SWIPE →</div>
  <div class="slide-logo">Oak Park · CBC1263425</div>
</div>

<div class="slide slide-list {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">What To Know</div>
  <div class="headline" style="font-size:96px; margin-bottom:36px;">THE <span class="accent">LIST.</span></div>
  <div class="list">
{items_html}  </div>
  <div class="arrow">SWIPE →</div>
  <div class="slide-logo">Oak Park · CBC1263425</div>
</div>

<div class="slide slide-tip {v_class}">
  <div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div>
  <div class="tag">Pro Tip</div>
  <div class="tip-label">▸ The Pro Move</div>
  <div class="tip-big">{s4_hl.replace(s4_accent, f'<span style="color:{s4_accent_style};">{s4_accent}</span>')}</div>
  <div class="tip-explain">{content.get("slide4_body", "")}</div>
  <div class="arrow">SWIPE →</div>
  <div class="slide-logo">Oak Park · CBC1263425</div>
</div>

<div class="slide slide-sources {v_class}">
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


def _build_brazil_html(content, slug, work_dir, handle="@HANDLE_PLACEHOLDER", media_paths=None):
    """Generate Brazil News 1080x1350 carousel HTML — dark + Canário brand spec v1.1.
    handle: footer handle shown on slides — defaults to @HANDLE_PLACEHOLDER for non-Brazil niches."""

    # Avoid double-docstring (was a copy-paste artifact)

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    cover_pt    = esc(content.get("cover_pt", "TÍTULO AQUI"))
    cover_en    = esc(content.get("cover_en", "TITLE HERE"))
    cover_accent = esc(content.get("cover_accent", ""))
    cover_date  = esc(content.get("cover_date", ""))
    cta_pt      = esc(content.get("cta_pt", "Salva pra não esquecer."))
    cta_en      = esc(content.get("cta_en", "Save this."))
    sources     = content.get("sources", [])

    raw_cover = content.get("cover_pt", "")
    if cover_accent and cover_accent in raw_cover:
        cover_hl = cover_pt.replace(cover_accent, f'<span class="accent">{cover_accent}</span>', 1)
    else:
        cover_hl = cover_pt

    cover_img = (media_paths or {}).get("cover", "")
    cover_bg_style = (
        f'style="background-image:linear-gradient(rgba(14,13,11,.72),rgba(14,13,11,.72)),'
        f'url(\'{cover_img}\');background-size:cover;background-position:center top;"'
        if cover_img else ""
    )
    slides_html = f"""
<div class="slide slide-cover" {cover_bg_style}>
  <div class="tag">Quem decidiu isso?</div>
  <div class="cover-date">{cover_date}</div>
  <div class="cover-hl">{cover_hl}</div>
  <div class="cover-en">{cover_en}</div>
  <div class="swipe">SWIPE →</div>
  <div class="footer-handle">{handle}</div>
</div>
"""

    for slide_i, slide in enumerate(content.get("slides", []), start=2):
        stype = slide.get("type", "list")
        h_pt  = esc(slide.get("heading_pt", ""))
        h_en  = esc(slide.get("heading_en", ""))

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
<div class="slide slide-profile">
  <div class="tag">Quem é</div>
  <div class="party-tag">{party}</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>
  <div class="profile-layout">
    {sticker_el}
    <ul class="fact-list">{facts_li}</ul>
  </div>
  <div class="swipe">SWIPE →</div>
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
<div class="slide slide-data">
  <div class="tag">Os Números</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>{ctx_slot}
  <div class="nums-grid">{nums_html}</div>
  <div class="swipe">SWIPE →</div>
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
<div class="slide slide-list">
  <div class="tag">Segue o fio</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>{ctx_slot}
  <ul class="item-list">{items_li}</ul>
  <div class="swipe">SWIPE →</div>
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
            else:
                ctx_slot = ""
            slides_html += f"""
<div class="slide slide-quote">
  <div class="tag">Não é opinião</div>
  <div class="slide-hl">{h_pt}</div>
  <div class="slide-en">{h_en}</div>{ctx_slot}
  <div class="quote-block">
    <div class="quote-mark">"</div>
    <div class="quote-text">{esc(slide.get("quote",""))}</div>
    <div class="quote-source">— {esc(slide.get("source",""))}</div>
  </div>
  <div class="quote-context">{esc(slide.get("context_pt",""))}</div>
  <div class="swipe">SWIPE →</div>
</div>
"""

    src_rows = "".join(
        f'<div class="src-row"><span class="src-num">{i:02d}</span><span>{esc(s)}</span></div>\n'
        for i, s in enumerate(sources, 1)
    )
    slides_html += f"""
<div class="slide slide-sources">
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
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;1,9..144,700&family=Inter:wght@400;500;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ob:#0E0D0B;--pa:#F2ECE0;--ca:#F4C430;--bl:#1F3A5F;--gr:#7A7267;--W:1080px;--H:1350px;--P:108px}}
body{{background:#111;display:flex;flex-wrap:wrap;gap:24px;padding:24px;font-family:'Inter',sans-serif}}
.slide{{width:var(--W);height:var(--H);background:var(--ob);color:var(--pa);padding:var(--P);position:relative;overflow:hidden;flex-shrink:0;display:flex;flex-direction:column}}
.tag{{font-family:'JetBrains Mono',monospace;font-size:26px;color:var(--gr);letter-spacing:.06em;text-transform:uppercase;margin-bottom:28px}}
.accent{{color:var(--ca)}}
.swipe{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);right:var(--P)}}
.footer-handle{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);position:absolute;bottom:var(--P);left:var(--P)}}
/* COVER */
.slide-cover .cover-date{{font-family:'JetBrains Mono',monospace;font-size:24px;color:var(--gr);margin-bottom:40px}}
.slide-cover .cover-hl{{font-family:'Fraunces',serif;font-weight:700;font-size:104px;line-height:1.0;text-transform:uppercase;margin-bottom:28px}}
.slide-cover .cover-en{{font-family:'Inter',sans-serif;font-style:italic;font-size:34px;color:var(--gr)}}
/* HEADINGS */
.slide-hl{{font-family:'Fraunces',serif;font-weight:700;font-size:68px;line-height:1.1;text-transform:uppercase;margin-bottom:12px}}
.slide-en{{font-family:'Inter',sans-serif;font-style:italic;font-size:26px;color:var(--gr);margin-bottom:36px}}
/* PROFILE */
.party-tag{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--ca);background:rgba(244,196,48,.1);padding:6px 14px;display:inline-block;margin-bottom:20px}}
.profile-layout{{display:flex;gap:40px;align-items:flex-start;flex:1}}
.sticker-slot{{width:260px;min-height:360px;border:2px dashed var(--gr);display:flex;align-items:center;justify-content:center;flex-shrink:0;border-radius:4px}}
.sticker-placeholder{{font-family:'JetBrains Mono',monospace;font-size:18px;color:var(--gr);text-align:center;padding:16px;word-break:break-all}}
.sticker-initials{{flex-direction:column;background:rgba(244,196,48,.06);border-color:rgba(244,196,48,.4)}}
.bio-initials{{font-family:'Fraunces',serif;font-weight:700;font-size:90px;color:var(--ca);letter-spacing:.05em;line-height:1}}
.bio-init-name{{font-family:'JetBrains Mono',monospace;font-size:15px;color:var(--gr);text-align:center;margin-top:14px;text-transform:uppercase;letter-spacing:.1em;padding:0 8px}}
.fact-list{{list-style:none;flex:1}}
.fact-list li{{font-size:34px;font-weight:500;padding:16px 0;border-bottom:1px solid rgba(242,236,224,.12);line-height:1.3}}
.fact-list li::before{{content:"▸ ";color:var(--ca)}}
/* DATA */
.nums-grid{{display:grid;grid-template-columns:1fr 1fr;gap:28px;flex:1}}
.num-block{{background:rgba(244,196,48,.06);border:1px solid rgba(244,196,48,.2);padding:28px 20px;border-radius:4px}}
.num-val{{font-family:'Fraunces',serif;font-weight:700;font-size:76px;color:var(--ca);line-height:1;margin-bottom:10px}}
.num-label{{font-size:28px;font-weight:500;margin-bottom:6px}}
.num-en{{font-style:italic;font-size:20px;color:var(--gr)}}
/* LIST */
.item-list{{list-style:none;flex:1}}
.item-list li{{font-size:36px;font-weight:500;padding:18px 0;border-bottom:1px solid rgba(242,236,224,.1);line-height:1.3}}
.item-list li::before{{content:"→ ";color:var(--ca)}}
/* QUOTE */
.quote-block{{background:rgba(31,58,95,.25);border-left:4px solid var(--bl);padding:28px 32px;margin-bottom:28px;flex:1}}
.quote-mark{{font-family:'Fraunces',serif;font-size:90px;color:var(--ca);line-height:.7;margin-bottom:12px}}
.quote-text{{font-size:36px;font-style:italic;line-height:1.4;margin-bottom:20px}}
.quote-source{{font-family:'JetBrains Mono',monospace;font-size:24px;color:var(--bl)}}
.quote-context{{font-size:28px;color:var(--gr);line-height:1.4}}
/* SOURCES */
.src-head{{font-family:'Fraunces',serif;font-weight:700;font-size:76px;line-height:1.0;text-transform:uppercase;margin-bottom:36px}}
.src-list{{flex:1}}
.src-row{{font-family:'JetBrains Mono',monospace;font-size:22px;color:var(--gr);display:flex;gap:18px;padding:10px 0;border-bottom:1px solid rgba(242,236,224,.08);line-height:1.4}}
.src-num{{color:var(--ca);flex-shrink:0;width:32px}}
.cta-pt{{font-size:36px;font-weight:700;margin-top:28px}}
.cta-en{{font-style:italic;font-size:26px;color:var(--gr);margin-top:6px}}
/* CONTEXT IMAGE SLOT */
.context-img-slot{{height:240px;border:2px dashed var(--gr);border-radius:4px;display:flex;align-items:center;justify-content:center;margin-bottom:20px;background:rgba(122,114,103,.06);flex-shrink:0}}
.ctx-query{{font-family:'JetBrains Mono',monospace;font-size:18px;color:var(--ca);text-align:center;padding:16px;opacity:.75}}
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
                issues.append(f"Slide {i}: '{p.get('name','?')}' named but visual_hint != bio-card — face won't render.")

    # context-image with empty query
    for i, s in enumerate(slides, start=2):
        if s.get("visual_hint") == "context-image" and not s.get("context_image_query", "").strip():
            issues.append(f"Slide {i}: visual_hint=context-image but context_image_query is empty.")

    # Cover visual missing
    if not content.get("cover_visual"):
        issues.append("Cover: no cover_visual field — cover will be text-only.")

    is_ok = len(issues) == 0
    if is_ok:
        summary = "VISUAL AUDIT: PASSED — all slides have visual anchors."
    else:
        summary = f"VISUAL AUDIT: {len(issues)} ISSUE(S) FOUND:\n" + "\n".join(f"  - {x}" for x in issues)
    return is_ok, issues, summary


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
