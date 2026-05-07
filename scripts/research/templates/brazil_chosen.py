"""Brazil chosen carousel templates (SH-104 person_evidence_mining).

Sourced from Drive folder `standalone_chosen` (ID 1Dp0igYURaNiCxlZZPXg_2SxXI0KBPbuG):
  - cover     ← news_brazil_standalone.html  ("Main Character")
  - biography ← news_brazil_biography.html   ("Quem é essa pessoa?")
  - evidence  ← news_brazil_duotone.html V2  ("Newspaper" black→cream duotone)
  - sources   ← derived from same brand DNA (gold canário / editorial black)

Brand palette (locked to biography + duotone gold canário):
  --bg     #0D0B08  (editorial black)
  --cream  #F0EBE3
  --gold   #C9A84C  (canário gold accent)
  --ink    #FFFFFF
  --warn   #D9534F  (sensitive claim_type chip)

Fonts: Playfair Display (display) + Roboto Condensed (sans) +
       JetBrains Mono (mono labels) + Barlow Condensed (cover headline)

Public API:
  build_carousel_html(spec: dict) -> str

`spec` follows manifest_renderer.build_carousel_spec() shape:
  spec["person_name"], spec["niche"], spec["slides"][i]["type"] in
  {"cover", "evidence_quote", "sources"} + per-slide payload.

A new "biography" slide type is auto-injected as slide 2 if `spec`
includes `claim_type_summary` (defaults to deriving from verified clips).
"""

from __future__ import annotations
from html import escape as _esc


# ── shared brand head (CSS + SVG duotone filter) ─────────────────────────────

SHARED_HEAD = r"""
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,700;1,900&family=Roboto+Condensed:wght@400;700;900&family=JetBrains+Mono:wght@400;700&family=Barlow+Condensed:wght@700;800;900&family=Barlow:wght@400;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0D0B08;
  --cream:#F0EBE3;
  --gold:#C9A84C;
  --ink:#FFFFFF;
  --warn:#D9534F;
  --W:1080px;
  --H:1350px;
  --P:96px;
}
body{background:#000;margin:0;padding:0;font-family:'Roboto Condensed',sans-serif}
/* export_slides.js expects .slides-container + #track + horizontal slide cycle.
   Container is the 1080x1350 viewport; track holds N slides side-by-side and
   gets translateX-ed by the runner per slide. */
.slides-container{width:var(--W);height:var(--H);overflow:hidden;position:relative;margin:0 auto}
#track{display:flex;flex-direction:row;width:max-content;height:var(--H);transition:none}
.slide{width:var(--W);height:var(--H);position:relative;overflow:hidden;flex-shrink:0}

/* ── COUNTER + LOGO (shared) ── */
.slide-counter{
  position:absolute;top:48px;right:48px;z-index:20;
  font-family:'Roboto Condensed',sans-serif;font-weight:700;
  font-size:18px;color:rgba(240,235,227,.65);
  background:rgba(13,11,8,.6);padding:6px 14px;border-radius:24px;
  letter-spacing:.04em;
}
.logo-circle{
  width:56px;height:56px;border-radius:50%;background:var(--bg);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
}
.logo-circle span{font-family:'Roboto Condensed',sans-serif;font-size:14px;font-weight:900;color:var(--cream);line-height:1}

/* ── COVER (Main Character — torn paper) ── */
.slide.cover{background:var(--bg)}
.cover .photo-zone{
  position:absolute;right:0;top:0;width:580px;height:100%;z-index:2;
  filter:drop-shadow(-16px 0px 28px rgba(0,0,0,.92));
  clip-path:polygon(13% 0%,10% 3%,17% 7%,9% 11%,16% 15%,10% 19%,17% 23%,9% 27%,16% 31%,10% 35%,17% 39%,9% 43%,16% 47%,10% 51%,17% 55%,9% 59%,16% 63%,10% 67%,17% 71%,9% 75%,15% 79%,10% 83%,16% 87%,9% 91%,15% 95%,12% 100%,100% 100%,100% 0%);
}
.cover .sticker-placeholder{width:100%;height:100%;background:#1E1E1E;display:block}
.cover .sticker-placeholder.has-photo{background-size:cover;background-position:center top;filter:grayscale(1) sepia(.6) saturate(2.5) hue-rotate(20deg) brightness(.78)}
.cover .text-zone{
  position:absolute;left:0;top:0;width:528px;height:100%;z-index:10;
  background:var(--bg);padding:64px 52px;display:flex;flex-direction:column;
}
.cover .cover-logo{font-family:'Barlow Condensed',sans-serif;font-weight:900;font-size:38px;line-height:1.05;color:var(--ink);letter-spacing:-.02em;margin-bottom:auto}
.cover .logo-top{display:block;color:var(--gold);border-bottom:3px solid var(--gold);padding-bottom:4px;margin-bottom:4px}
.cover .cover-headline{font-family:'Barlow Condensed',sans-serif;font-weight:900;font-size:94px;line-height:.87;text-transform:uppercase;color:var(--ink);letter-spacing:-.025em;word-break:break-word;margin-bottom:30px}
.cover .accent-rule{width:52px;height:5px;background:var(--gold);margin-bottom:18px;flex-shrink:0}
.cover .cover-credit{font-family:'Barlow',sans-serif;font-size:15px;color:rgba(255,255,255,.32);letter-spacing:.02em}
.cover .cover-sub{color:var(--gold);font-family:'Playfair Display',serif;font-style:italic;font-size:38px;font-weight:400;margin-top:12px}

/* ── BIOGRAPHY (Quem é essa pessoa?) ── */
.slide.biography{background:var(--bg);color:var(--cream);padding:var(--P);display:flex;flex-direction:column}
.bio-label{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:var(--gold);margin-bottom:22px}
.bio-headline{font-family:'Playfair Display',serif;font-size:96px;font-weight:900;line-height:.91;letter-spacing:-.015em;margin-bottom:36px}
.bio-headline em{font-style:italic;color:var(--gold)}
.bio-rule{display:flex;align-items:center;gap:14px;margin-bottom:42px}
.bio-rule-line{flex:1;height:1px;background:rgba(201,168,76,.28)}
.bio-rule-gem{width:8px;height:8px;background:var(--gold);transform:rotate(45deg);flex-shrink:0}
.bio-grid{display:grid;grid-template-columns:1fr 1fr;gap:30px 56px;align-content:start}
.bio-field-label{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);opacity:.85;margin-bottom:8px}
.bio-field-value{font-family:'Roboto Condensed',sans-serif;font-size:24px;font-weight:400;line-height:1.38;color:var(--cream)}
.bio-topics-wrap{margin-top:auto;padding-top:36px;border-top:1px solid rgba(201,168,76,.15)}
.bio-topics-label{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;letter-spacing:.2em;text-transform:uppercase;color:rgba(240,235,227,.45);margin-bottom:16px}
.bio-topics{display:flex;flex-wrap:wrap;gap:10px}
.bio-pill{font-family:'Roboto Condensed',sans-serif;font-size:20px;font-weight:400;color:var(--cream);border:1px solid rgba(201,168,76,.38);padding:7px 18px;border-radius:2px}

/* ── EVIDENCE (Duotone V2 — black→cream newspaper) ── */
.slide.evidence{background:var(--gold);position:relative;overflow:hidden;display:flex;flex-direction:column;padding:80px}
.slide.evidence::before{content:'';position:absolute;inset:0;pointer-events:none;z-index:0;background:repeating-linear-gradient(45deg,transparent,transparent 2px,rgba(0,0,0,.025) 2px,rgba(0,0,0,.025) 4px)}
.slide.evidence>*{position:relative;z-index:1}
.evidence .context-chip{position:absolute;top:32px;left:32px;z-index:5;background:var(--warn);color:var(--cream);padding:10px 20px;border-radius:20px;font-family:'Roboto Condensed',sans-serif;font-size:15px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.evidence .claim-tag{font-family:'Roboto Condensed',sans-serif;font-size:18px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--bg);opacity:.7;margin-bottom:14px}
.evidence .claim{font-family:'Playfair Display',serif;font-size:54px;font-weight:700;color:var(--bg);line-height:1.12;letter-spacing:-.01em;margin-bottom:36px;flex-shrink:0}
.evidence .claim strong{font-weight:900}
.evidence .claim u{text-decoration-thickness:3px;text-underline-offset:5px}
.evidence .photo-wrap{width:920px;height:480px;border-radius:20px;overflow:hidden;position:relative;flex-shrink:0;align-self:center;background:#0A0A0A}
.evidence .photo{position:absolute;inset:0;background-size:cover;background-position:center 15%;width:100%;height:100%;filter:url(#duotone-bw-cream)}
.evidence .quote-block{flex:1;display:flex;flex-direction:column;justify-content:center;padding-top:28px}
.evidence .quote-mark{font-family:'Playfair Display',serif;font-size:80px;font-weight:900;color:var(--bg);line-height:.6;opacity:.18;margin-bottom:8px}
.evidence .quote-text{font-family:'Playfair Display',serif;font-size:31px;font-weight:400;font-style:italic;color:#2A1E06;line-height:1.45}
.evidence .attr{display:flex;align-items:center;gap:20px;padding-top:22px;flex-shrink:0;border-top:2px solid rgba(13,11,8,.18)}
.evidence .attr-meta{flex:1;display:flex;flex-direction:column;gap:4px}
.evidence .attr-name{font-family:'Roboto Condensed',sans-serif;font-size:22px;font-weight:700;color:var(--bg);letter-spacing:.08em;text-transform:uppercase}
.evidence .attr-source{font-family:'JetBrains Mono',monospace;font-size:14px;color:rgba(13,11,8,.65);letter-spacing:.02em}
.evidence .timestamp{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:var(--bg);background:rgba(13,11,8,.1);padding:6px 14px;border-radius:8px}
.evidence .logo-circle{width:56px;height:56px}
.evidence .logo-circle span{color:var(--gold)}

/* ── SOURCES ── */
.slide.sources{background:var(--bg);color:var(--cream);padding:var(--P);display:flex;flex-direction:column}
.sources .src-label{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:var(--gold);margin-bottom:18px}
.sources .src-headline{font-family:'Playfair Display',serif;font-size:88px;font-weight:900;line-height:.92;letter-spacing:-.015em;margin-bottom:36px}
.sources .src-headline em{font-style:italic;color:var(--gold)}
.sources .src-rule{display:flex;align-items:center;gap:14px;margin-bottom:32px}
.sources .src-rule-line{flex:1;height:1px;background:rgba(201,168,76,.28)}
.sources .src-rule-gem{width:8px;height:8px;background:var(--gold);transform:rotate(45deg)}
.sources .src-list{flex:1;overflow:hidden;display:flex;flex-direction:column;gap:18px}
.sources .src-item{display:flex;gap:18px;align-items:flex-start;padding-bottom:14px;border-bottom:1px solid rgba(240,235,227,.12)}
.sources .src-num{font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:700;color:var(--gold);min-width:40px}
.sources .src-meta{flex:1;display:flex;flex-direction:column;gap:4px;min-width:0}
.sources .src-platform{font-family:'JetBrains Mono',monospace;font-size:14px;color:var(--gold);letter-spacing:.18em;text-transform:uppercase}
.sources .src-url{font-family:'Roboto Condensed',sans-serif;font-size:18px;color:var(--cream);word-break:break-all;line-height:1.4}
.sources .src-footer{margin-top:auto;padding-top:28px;border-top:1px solid rgba(201,168,76,.18);font-family:'JetBrains Mono',monospace;font-size:14px;color:rgba(240,235,227,.5);letter-spacing:.1em}
</style>

<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <filter id="duotone-bw-cream" color-interpolation-filters="sRGB">
    <feColorMatrix type="matrix" values=".299 .587 .114 0 0  .299 .587 .114 0 0  .299 .587 .114 0 0  0 0 0 1 0"/>
    <feComponentTransfer><feFuncR type="linear" slope="1.4" intercept="-0.2"/><feFuncG type="linear" slope="1.4" intercept="-0.2"/><feFuncB type="linear" slope="1.4" intercept="-0.2"/></feComponentTransfer>
    <feComponentTransfer><feFuncR type="table" tableValues="0.039 0.941"/><feFuncG type="table" tableValues="0.039 0.922"/><feFuncB type="table" tableValues="0.039 0.890"/></feComponentTransfer>
  </filter>
</svg>
"""


# ── slide builders ──────────────────────────────────────────────────────────

def _photo_attr(url: str | None) -> str:
    if not url:
        return ''
    safe_url = _esc(url, quote=True)
    return f' style="background-image:url(\'{safe_url}\');background-size:cover;background-position:center top" class="sticker-placeholder has-photo"'


def slide_cover(spec: dict, slide: dict, total_slides: int) -> str:
    person_name = spec.get("person_name", "")
    title = slide.get("title_main", person_name) or person_name
    sub = slide.get("title_sub", "O que mais foi dito")
    person_photo = slide.get("person_photo_url") or spec.get("person_photo_url")
    counter = f'1 / {total_slides}'
    photo_inner = '<div class="sticker-placeholder"></div>' if not person_photo \
        else f'<div{_photo_attr(person_photo)}></div>'
    return f"""<div class="slide cover">
  <div class="photo-zone sticker-slot">{photo_inner}</div>
  <div class="text-zone">
    <div class="cover-logo"><span class="logo-top">In</span>Br</div>
    <div class="cover-headline">{_esc(title.upper())}</div>
    <div class="accent-rule"></div>
    <div class="cover-sub">{_esc(sub)}</div>
    <div class="cover-credit">SH-104 / Evidence Carousel</div>
  </div>
  <div class="slide-counter">{counter}</div>
</div>"""


def slide_biography(spec: dict, slide: dict, total_slides: int, position: int) -> str:
    person_name = spec.get("person_name", "Esta pessoa")
    fields = slide.get("fields") or []
    if not fields:
        fields = [
            ("BACKGROUND",          slide.get("background", "—")),
            ("PLATAFORMA",          slide.get("platform", "—")),
            ("MISSÃO PÚBLICA",      slide.get("mission", "—")),
            ("ESTILO",              slide.get("style", "—")),
        ]
    field_html = "".join(
        f'<div class="bio-field"><div class="bio-field-label">{_esc(label)}</div>'
        f'<div class="bio-field-value">{_esc(val)}</div></div>'
        for label, val in fields
    )
    pills = slide.get("controversies") or slide.get("topics") or []
    pills_html = "".join(f'<div class="bio-pill">{_esc(p)}</div>' for p in pills) or \
        '<div class="bio-pill">Sem tópicos polêmicos identificados</div>'
    return f"""<div class="slide biography">
  <div class="bio-label">Quem é · Who is</div>
  <div class="bio-headline">{_esc(person_name)}<br><em>?</em></div>
  <div class="bio-rule"><div class="bio-rule-line"></div><div class="bio-rule-gem"></div><div class="bio-rule-line"></div></div>
  <div class="bio-grid">{field_html}</div>
  <div class="bio-topics-wrap">
    <div class="bio-topics-label">◆ TÓPICOS POLÊMICOS</div>
    <div class="bio-topics">{pills_html}</div>
  </div>
  <div class="slide-counter">{position} / {total_slides}</div>
</div>"""


def slide_evidence(spec: dict, slide: dict, total_slides: int, position: int) -> str:
    person_name = spec.get("person_name", "")
    quote = slide.get("quote", "") or ""
    ts_start = slide.get("timestamp_start", "00:00")
    ts_end = slide.get("timestamp_end", "00:00")
    claim_type = slide.get("claim_type", "needs-context")
    is_sensitive = bool(slide.get("show_context_warning"))
    chip = ('<div class="context-chip">⚠ Contexto necessário</div>' if is_sensitive else "")
    person_photo = slide.get("person_photo_url") or spec.get("person_photo_url")
    photo_attr = _photo_attr(person_photo) if person_photo else ''
    photo_html = f'<div class="photo"{photo_attr}></div>' if person_photo \
        else '<div class="photo" style="background:linear-gradient(135deg,#1A1410 0%,#3D2E14 100%)"></div>'
    source_uploader = slide.get("source_uploader", "") or "—"
    source_platform = (slide.get("source_platform", "") or "").upper()
    claim_label = {
        "group-targeting":       "DECLARAÇÃO · GRUPO ALVO",
        "dehumanizing":          "DECLARAÇÃO · DESUMANIZAÇÃO",
        "unfair-generalization": "DECLARAÇÃO · GENERALIZAÇÃO",
        "moral-contradiction":   "DECLARAÇÃO · CONTRADIÇÃO MORAL",
        "hypocrisy":             "DECLARAÇÃO · HIPOCRISIA",
        "needs-context":         "DECLARAÇÃO · CONTEXTO NECESSÁRIO",
    }.get(claim_type, "DECLARAÇÃO")
    return f"""<div class="slide evidence">
  {chip}
  <div class="claim-tag">{claim_label}</div>
  <div class="claim"><strong>{_esc(person_name)}</strong></div>
  <div class="photo-wrap">{photo_html}</div>
  <div class="quote-block">
    <div class="quote-mark">“</div>
    <div class="quote-text">{_esc(quote)}</div>
  </div>
  <div class="attr">
    <div class="logo-circle"><span>In</span><span>Br</span></div>
    <div class="attr-meta">
      <div class="attr-name">{_esc(source_uploader)}</div>
      <div class="attr-source">{_esc(source_platform)} · {_esc(ts_start)}–{_esc(ts_end)}</div>
    </div>
    <div class="timestamp">{_esc(ts_start)}</div>
  </div>
  <div class="slide-counter">{position} / {total_slides}</div>
</div>"""


def slide_sources(spec: dict, slide: dict, total_slides: int, position: int) -> str:
    items_html = []
    for i, src in enumerate(slide.get("sources") or [], 1):
        items_html.append(f"""<div class="src-item">
      <div class="src-num">{i:02d}</div>
      <div class="src-meta">
        <div class="src-platform">{_esc((src.get('platform') or '').upper())}</div>
        <div class="src-url">{_esc(src.get('url',''))}</div>
      </div>
    </div>""")
    return f"""<div class="slide sources">
  <div class="src-label">Fontes · Sources</div>
  <div class="src-headline">{_esc(slide.get('title','Fontes'))}<em>.</em></div>
  <div class="src-rule"><div class="src-rule-line"></div><div class="src-rule-gem"></div><div class="src-rule-line"></div></div>
  <div class="src-list">{''.join(items_html) or '<div class="src-item"><div class="src-meta"><div class="src-url">— sem fontes —</div></div></div>'}</div>
  <div class="src-footer">{_esc(slide.get('footer',''))}</div>
  <div class="slide-counter">{position} / {total_slides}</div>
</div>"""


# ── public composer ──────────────────────────────────────────────────────────

def build_carousel_html(spec: dict, *, lang: str = "pt-BR") -> str:
    """Compose the multi-slide HTML using the chosen brand templates.

    Auto-injects a biography slide as slide 2 if not already present.
    Returns the full HTML document string.
    """
    slides_in = spec.get("slides", []) or []
    has_biography = any((s.get("type") == "biography") for s in slides_in)

    # Derive controversies from verified clips' claim_types if not provided
    derived_pills = []
    for s in slides_in:
        if s.get("type") == "evidence_quote":
            ct = s.get("claim_type")
            if ct and ct not in derived_pills:
                derived_pills.append(ct)

    composed = []
    cover = next((s for s in slides_in if s.get("type") == "cover"), None)
    evidence = [s for s in slides_in if s.get("type") == "evidence_quote"]
    sources = next((s for s in slides_in if s.get("type") == "sources"), None)

    bio_slide = None
    if not has_biography:
        bio_slide = {"type": "biography", "controversies": derived_pills[:6]}
    else:
        bio_slide = next(s for s in slides_in if s.get("type") == "biography")

    total = (1 if cover else 0) + (1 if bio_slide else 0) + len(evidence) + (1 if sources else 0)
    pos = 0

    if cover:
        pos += 1
        composed.append(slide_cover(spec, cover, total))
    if bio_slide:
        pos += 1
        composed.append(slide_biography(spec, bio_slide, total, pos))
    for ev in evidence:
        pos += 1
        composed.append(slide_evidence(spec, ev, total, pos))
    if sources:
        pos += 1
        composed.append(slide_sources(spec, sources, total, pos))

    title_text = spec.get("person_name", "Evidence carousel")
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{_esc(title_text)} — SH-104 evidence carousel</title>
{SHARED_HEAD}
</head>
<body>
<div class="slides-container">
  <div id="track">
{chr(10).join(composed)}
  </div>
</div>
</body>
</html>
"""
