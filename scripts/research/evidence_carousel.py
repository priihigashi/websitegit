"""evidence_carousel.py — SH-104 Phase 3 carousel renderer.

Reads a `carousel_content_spec.json` produced by manifest_renderer.build_carousel_spec
and generates a static carousel:
  - cover.html (renderable by export_slides.js / Playwright)
  - png/<NN>_<name>.png   (one per slide)
  - resources/manifest_excerpt.json (audit)

Why a dedicated renderer instead of carousel_builder.py:
  carousel_builder is built around editorial brief + photo sourcing for OPC /
  News series; the data shape is different. The evidence-carousel uses the
  manifest's verified_clips directly with attribution per slide and a
  context-warning chip when claim_type is sensitive. Reusing carousel_builder
  would force us to fake an editorial brief and lose attribution overlays.

Output structure (matches CAROUSEL FOLDER STANDARD):
  <work_dir>/
    cover.html
    png/
      01_cover.png  02_evidence_<n>.png  ...  NN_sources.png
    resources/
      manifest_excerpt.json   (subset of manifest for audit trail)
"""

from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
EXPORT_SLIDES_JS = (
    "/Users/priscilahigashi/ClaudeWorkspace/Content Templates/_Scripts/export_slides.js"
)
# CI runner alternative: bundle a minimal export script if Content Templates
# isn't checked into the repo. Looked up in this priority order.
_CI_EXPORT_FALLBACKS = [
    str(_REPO / "scripts" / "remotion" / "export_slides.js"),
    str(_REPO / "scripts" / "content_creator" / "export_slides.js"),
]


# ── HTML template — single file with .slide divs ────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{person_name} — Evidence carousel</title>
<style>
  :root {{
    --bg: #0F0F12;
    --panel: #15151A;
    --ink: #F2EFE8;
    --ink-dim: #9B988F;
    --accent: #E8C547;
    --warn: #D9534F;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #000;
    font-family: 'Inter', system-ui, sans-serif;
    color: var(--ink);
  }}
  .slide {{
    width: 1080px;
    height: 1350px;
    background: var(--bg);
    padding: 80px 64px;
    position: relative;
    display: flex;
    flex-direction: column;
    page-break-after: always;
    overflow: hidden;
  }}
  .corner {{
    position: absolute;
    top: 32px;
    right: 48px;
    color: var(--ink-dim);
    font-family: 'JetBrains Mono', Menlo, monospace;
    font-size: 22px;
    letter-spacing: 2px;
  }}
  /* Cover */
  .cover-title {{
    margin-top: auto;
    color: var(--ink);
    font-size: 132px;
    font-weight: 800;
    line-height: 1.02;
    letter-spacing: -3px;
    text-transform: uppercase;
  }}
  .cover-sub {{
    color: var(--accent);
    font-size: 56px;
    font-weight: 500;
    margin-top: 24px;
  }}
  .cover-hook {{
    margin-top: auto;
    color: var(--ink-dim);
    font-size: 36px;
    font-weight: 400;
  }}
  /* Evidence slides */
  .evidence-tag {{
    color: var(--accent);
    font-size: 32px;
    text-transform: uppercase;
    letter-spacing: 4px;
    margin-bottom: 16px;
    font-weight: 700;
  }}
  .quote {{
    color: var(--ink);
    font-size: 76px;
    font-weight: 600;
    line-height: 1.15;
    margin-top: 24px;
  }}
  .timestamp-row {{
    display: flex;
    align-items: center;
    gap: 24px;
    margin-top: 48px;
  }}
  .timestamp {{
    color: var(--accent);
    font-family: 'JetBrains Mono', Menlo, monospace;
    font-size: 36px;
    font-weight: 600;
  }}
  .match-pill {{
    background: var(--panel);
    color: var(--ink-dim);
    padding: 8px 20px;
    border-radius: 12px;
    font-size: 24px;
    font-family: 'JetBrains Mono', Menlo, monospace;
  }}
  .attribution {{
    margin-top: auto;
    border-top: 2px solid var(--ink-dim);
    padding-top: 24px;
    color: var(--ink-dim);
    font-size: 28px;
  }}
  .context-chip {{
    display: inline-block;
    background: var(--warn);
    color: var(--ink);
    padding: 12px 24px;
    border-radius: 12px;
    font-size: 24px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 24px;
  }}
  /* Sources */
  .sources-title {{
    color: var(--accent);
    font-size: 88px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: -1px;
    margin-bottom: 32px;
  }}
  .sources-list {{
    flex: 1;
    overflow: hidden;
  }}
  .source-item {{
    color: var(--ink);
    font-family: 'JetBrains Mono', Menlo, monospace;
    font-size: 22px;
    line-height: 1.6;
    word-break: break-all;
    margin-bottom: 16px;
  }}
  .footer {{
    color: var(--ink-dim);
    font-size: 22px;
    margin-top: 16px;
  }}
</style>
</head>
<body>
{slides_html}
</body>
</html>
"""


def _slide_cover(spec: dict, slide: dict) -> str:
    title = slide.get("title_main", "")
    sub = slide.get("title_sub", "")
    hook = slide.get("hook_text", "")
    return f"""<div class="slide" data-kind="cover">
  <div class="corner">SH-104 / EVIDENCE</div>
  <div class="cover-title">{_esc(title)}</div>
  <div class="cover-sub">{_esc(sub)}</div>
  <div class="cover-hook">{_esc(hook)}</div>
</div>"""


def _slide_evidence(spec: dict, slide: dict) -> str:
    is_sensitive = bool(slide.get("show_context_warning"))
    chip = ('<div class="context-chip">Contexto necessário</div>'
            if is_sensitive else "")
    quote = slide.get("quote", "") or ""
    ts_start = slide.get("timestamp_start", "00:00")
    ts_end = slide.get("timestamp_end", "00:00")
    match = slide.get("match_score", 0.0)
    try:
        match_str = f"match {float(match):.2f}"
    except Exception:
        match_str = "match —"
    attribution = (
        f"@{slide.get('source_uploader','')} · {slide.get('source_platform','')}"
    )
    if slide.get("context_warning"):
        attribution += f" · {slide['context_warning']}"
    return f"""<div class="slide" data-kind="evidence">
  <div class="corner">{_esc(spec.get('person_name',''))}</div>
  {chip}
  <div class="evidence-tag">Evidence #{slide.get('index','')}</div>
  <div class="quote">“{_esc(quote)}”</div>
  <div class="timestamp-row">
    <div class="timestamp">{_esc(ts_start)} — {_esc(ts_end)}</div>
    <div class="match-pill">{_esc(match_str)}</div>
  </div>
  <div class="attribution">{_esc(attribution)}</div>
</div>"""


def _slide_sources(spec: dict, slide: dict) -> str:
    items = []
    for i, src in enumerate(slide.get("sources", []) or [], 1):
        items.append(
            f'<div class="source-item">{i}. [{_esc(src.get("platform",""))}] '
            f'{_esc(src.get("url",""))}</div>'
        )
    return f"""<div class="slide" data-kind="sources">
  <div class="corner">SOURCES</div>
  <div class="sources-title">{_esc(slide.get('title','Sources'))}</div>
  <div class="sources-list">
    {''.join(items)}
  </div>
  <div class="footer">{_esc(slide.get('footer',''))}</div>
</div>"""


def _esc(s) -> str:
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


# ── public API ──────────────────────────────────────────────────────────────

def build_carousel_html(spec: dict, work_dir: str) -> str:
    """Write cover.html. Returns its path."""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    niche = spec.get("niche", "brazil")
    lang = "pt-BR" if niche == "brazil" else "en"
    slides_html_parts = []
    for slide in spec.get("slides", []) or []:
        kind = slide.get("type")
        if kind == "cover":
            slides_html_parts.append(_slide_cover(spec, slide))
        elif kind == "evidence_quote":
            slides_html_parts.append(_slide_evidence(spec, slide))
        elif kind == "sources":
            slides_html_parts.append(_slide_sources(spec, slide))
    html = _HTML.format(
        lang=lang,
        person_name=_esc(spec.get("person_name", "")),
        slides_html="\n".join(slides_html_parts),
    )
    out = work / "cover.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


def render_carousel_pngs(html_path: str, png_dir: str) -> int:
    """Run export_slides.js (Playwright). Returns exit code (0 == success).
    Returns -1 if no exporter found on disk (e.g. minimal CI checkout).
    """
    Path(png_dir).mkdir(parents=True, exist_ok=True)
    exporter = EXPORT_SLIDES_JS if os.path.exists(EXPORT_SLIDES_JS) else None
    if not exporter:
        for cand in _CI_EXPORT_FALLBACKS:
            if os.path.exists(cand):
                exporter = cand
                break
    if not exporter:
        print("evidence_carousel: no export_slides.js found — HTML only")
        return -1
    cmd = ["node", exporter, html_path, png_dir]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"export_slides.js failed: {r.stderr[:300]}")
    return r.returncode


def write_audit_excerpt(spec: dict, manifest: dict, work_dir: str) -> str:
    """Write resources/manifest_excerpt.json — minimal audit trail kept beside
    the rendered carousel (full manifest stays in the clipmine_* folder)."""
    work = Path(work_dir) / "resources"
    work.mkdir(parents=True, exist_ok=True)
    excerpt = {
        "person_name": manifest.get("person", {}).get("name", ""),
        "manifest_run_id": manifest.get("run_id", ""),
        "verified_count": len(manifest.get("verified_clips", []) or []),
        "constraints": spec.get("constraints", {}),
        "rendered_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    out = work / "manifest_excerpt.json"
    out.write_text(json.dumps(excerpt, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def render_from_manifest(manifest_path: str, work_dir: str) -> dict:
    """Top-level entry. Reads manifest, runs build_carousel_spec via the
    manifest_renderer module, renders HTML + PNGs.
    Returns:
      {"ok": bool, "html_path": str, "png_dir": str, "audit_path": str,
       "issues": list[str], "png_render_code": int}
    """
    sys.path.insert(0, str(_HERE))
    from manifest_renderer import (
        load_manifest, build_carousel_spec, audit_pre_render,
    )
    manifest = load_manifest(manifest_path)
    ok, issues = audit_pre_render(manifest)
    if not ok:
        return {"ok": False, "issues": issues, "html_path": "", "png_dir": "",
                "audit_path": "", "png_render_code": -1}
    spec = build_carousel_spec(manifest)
    html_path = build_carousel_html(spec, work_dir)
    png_dir = str(Path(work_dir) / "png")
    code = render_carousel_pngs(html_path, png_dir)
    audit = write_audit_excerpt(spec, manifest, work_dir)
    return {"ok": code == 0,
            "html_path": html_path,
            "png_dir": png_dir,
            "audit_path": audit,
            "issues": [] if code == 0 else [f"png_render_code={code}"],
            "png_render_code": code}


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("manifest", help="Path to evidence_manifest.json")
    p.add_argument("--work-dir", required=True)
    args = p.parse_args()
    result = render_from_manifest(args.manifest, args.work_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)
