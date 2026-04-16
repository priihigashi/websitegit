#!/usr/bin/env python3
"""
carousel_builder.py — Generates carousel HTML from template + topic, renders PNGs.
Uses Claude Haiku for content generation, Playwright for rendering.
"""
import json, os, re, subprocess, time, urllib.request, urllib.parse
from pathlib import Path

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

OPC_TEMPLATE = "tip"
BRAZIL_TEMPLATE = "quem-decidiu"

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
    },
}


def generate_carousel_content(topic, niche, template_key=None):
    if not template_key:
        template_key = OPC_TEMPLATE if niche == "opc" else BRAZIL_TEMPLATE

    tmpl = TEMPLATES.get(niche, {}).get(template_key)
    if not tmpl:
        print(f"  No template for {niche}/{template_key}")
        return None

    lang = "Portuguese (Brazilian)" if niche == "brazil" else "English"

    prompt = f"""You are a content writer for a construction company's Instagram carousel.
Generate content for a {tmpl['slides']}-slide carousel about: "{topic}"

Series: {tmpl['series']}
Structure: {tmpl['structure']}
Language: {lang}

Return ONLY a JSON object with these fields:
{{
  "headline": "3-4 word cover headline (ALL CAPS, punchy)",
  "accent_word": "1 word from headline to highlight in accent color",
  "subhead": "1 sentence under the headline",
  "slide2_headline": "3-4 word headline for slide 2",
  "slide2_stat": "a big number or stat (e.g. '+$12K' or '1 IN 3')",
  "slide2_label": "1 line explaining the stat",
  "slide3_items": [
    {{"title": "Item 1 title", "sub": "1 line detail"}},
    {{"title": "Item 2 title", "sub": "1 line detail"}},
    {{"title": "Item 3 title", "sub": "1 line detail"}}
  ],
  "slide4_headline": "3-4 word tip/action headline",
  "slide4_body": "2-3 sentences explaining the tip",
  "sources": [
    "Source 1 — description",
    "Source 2 — description",
    "Source 3 — description",
    "Oak Park Construction job data — South Florida 2023-2025"
  ],
  "cta": "2-3 word call to action (e.g. SAVE THIS.)"
}}

Rules:
- Keep it simple, direct, no jargon
- Stats should use ranges not exact numbers (safer)
- Last source MUST be "Oak Park Construction job data — South Florida 2023-2025"
- If you can't find a reliable stat, use a general industry figure with a range
- Headlines in ALL CAPS"""

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1000,
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
    text = resp["content"][0]["text"]

    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        print(f"  Failed to parse carousel content from Claude response")
        return None

    return json.loads(json_match.group())


def build_html(content, niche, topic_slug, work_dir):
    if niche == "opc":
        return _build_opc_html(content, topic_slug, work_dir)
    return None


def _build_opc_html(content, slug, work_dir):
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

    def variant_block(v_class, cover_accent_style, s4_accent_style, src_accent_style):
        return f"""
<!-- {v_class.upper()} -->
<div class="slide slide-cover {v_class}">
  <div class="bg-photo"></div>
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
  <div class="tag">From Mike</div>
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


def render_pngs(html_path, output_dir):
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


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "5 things your contractor won't tell you"
    content = generate_carousel_content(topic, "opc", "tip")
    if content:
        print(json.dumps(content, indent=2))
