"""manifest_renderer.py — Phase 3 adapter.

Consumes evidence_manifest.json and produces:
  - carousel_content_spec.json   : input shape for carousel_builder.py
  - remotion_props.json          : input shape for EvidenceCompilation.tsx
  - sources_block.txt            : sources slide content

Phase 3 STATUS: scaffold + spec generation works. Actual carousel/Remotion
RENDER wiring remains in Phase 3 final-mile (after Phase 2 face-match ships
and the first Frei Gilson manifest is approved by Priscila).

Why scaffold-first: rendering requires real verified manifests to design
slide layouts against. Locking the data contracts NOW lets the renderer
team (or next chat session) wire pixels to a stable shape.

Public API:
  load_manifest(path) -> dict
  build_carousel_spec(manifest) -> dict
  build_remotion_props(manifest) -> dict
  build_sources_block(manifest) -> str
  render_all(manifest_path, output_dir) -> dict
  audit_pre_render(manifest) -> (ok: bool, issues: list[str])
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Per CLAUDE.md NAMED-PERSON → FACE rule, every slide naming a person needs
# a face treatment. Phase 3 renderer must wire bio-card / sticker-slot / hero
# slot. Phase 1 marks the requirement; Phase 3 final-mile fulfills it.

REQUIRED_PER_CLIP = ("best_quote", "timestamp_start", "timestamp_end")
REQUIRED_PER_VERIFIED = REQUIRED_PER_CLIP + ("claim_type", "match_score")
SENSITIVE_CLAIM_TYPES = {
    "group-targeting", "dehumanizing", "moral-contradiction", "hypocrisy",
}


def load_manifest(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


# ── pre-render audit ─────────────────────────────────────────────────────────

def audit_pre_render(manifest: dict) -> tuple[bool, list[str]]:
    """Reviewer block. Returns (ok, issues). Phase 1.13 / 3.8 enforcement."""
    issues = []
    if manifest.get("mode") != "person_evidence_mining":
        issues.append("manifest mode is not person_evidence_mining")
    person = manifest.get("person", {}) or {}
    if not person.get("name"):
        issues.append("manifest.person.name is empty")
    if not manifest.get("seed", {}).get("url"):
        issues.append("manifest.seed.url is empty")
    verified = manifest.get("verified_clips", []) or []
    if len(verified) < 3:
        issues.append(f"verified_clips count {len(verified)} < 3 minimum")
    for i, vc in enumerate(verified):
        score = vc.get("score", {}) or {}
        cand = vc.get("candidate", {}) or {}
        for k in REQUIRED_PER_VERIFIED:
            if not score.get(k) and score.get(k) != 0:
                issues.append(f"verified[{i}] missing score.{k}")
        if not cand.get("url"):
            issues.append(f"verified[{i}] missing candidate.url")
        if not score.get("safe_to_use"):
            issues.append(f"verified[{i}] safe_to_use is false (filter before render)")
    gates = manifest.get("build_gates", {}) or {}
    if not gates:
        issues.append("manifest.build_gates is missing")
    return (len(issues) == 0), issues


# ── carousel content spec ───────────────────────────────────────────────────

def build_carousel_spec(manifest: dict) -> dict:
    """Produce a carousel content spec downstream of carousel_builder.py.

    Slide 1 = seed/cover hook. Slides 2..N = one verified clip each.
    Final slide = sources.

    The spec is intentionally renderer-agnostic; it carries the *what*,
    not the *how*. carousel_builder.py converts this into HTML/PNGs.
    """
    person = manifest.get("person", {}) or {}
    person_name = person.get("name", "Unknown person")
    requirement = manifest.get("requirement", "")
    seed = manifest.get("seed", {}) or {}
    verified = manifest.get("verified_clips", []) or []
    niche = manifest.get("niche", "brazil")

    slides = [{
        "index": 1,
        "type": "cover",
        "title_main": person_name,
        "title_sub": "O que mais foi dito" if niche == "brazil" else "What else was said",
        "hook_text": f"6+ clips verificados — {person_name}" if niche == "brazil" else f"6+ verified clips — {person_name}",
        "visual_hint": "bio-card",  # named person → face mandatory
        "person_name": person_name,
        "seed_url": seed.get("url", ""),
        "renderer_notes": "Hero face/sticker. NO 'hate speech' label. Use neutral framing.",
    }]

    for i, vc in enumerate(verified, start=2):
        score = vc.get("score", {}) or {}
        cand = vc.get("candidate", {}) or {}
        is_sensitive = score.get("claim_type") in SENSITIVE_CLAIM_TYPES
        slides.append({
            "index": i,
            "type": "evidence_quote",
            "person_name": person_name,
            "quote": score.get("best_quote", ""),
            "timestamp_start": score.get("timestamp_start", "00:00"),
            "timestamp_end": score.get("timestamp_end", "00:00"),
            "claim_type": score.get("claim_type", "needs-context"),
            "match_score": score.get("match_score", 0.0),
            "source_url": cand.get("url", ""),
            "source_platform": cand.get("platform", ""),
            "source_title": cand.get("title", ""),
            "source_uploader": cand.get("uploader", ""),
            "context_warning": score.get("context_needed", ""),
            "show_context_warning": is_sensitive or bool(score.get("context_needed")),
            "visual_hint": "bio-card",  # face mandatory for named person
            "renderer_notes": ("Quote in large type. Source attribution at bottom. "
                               "Context warning chip when show_context_warning=true."),
        })

    slides.append({
        "index": len(slides) + 1,
        "type": "sources",
        "title": "Fontes" if niche == "brazil" else "Sources",
        "sources": [
            {
                "url": (vc.get("candidate", {}) or {}).get("url", ""),
                "title": (vc.get("candidate", {}) or {}).get("title", "")[:120],
                "platform": (vc.get("candidate", {}) or {}).get("platform", ""),
            }
            for vc in verified
        ],
        "footer": "Manifesto: " + (manifest.get("run_id") or "see Drive folder"),
    })

    return {
        "schema_version": 1,
        "format": "person_evidence_carousel",
        "niche": niche,
        "person_name": person_name,
        "requirement": requirement,
        "manifest_run_id": manifest.get("run_id", ""),
        "slide_count": len(slides),
        "slides": slides,
        "constraints": {
            "named_person_face_required": True,
            "no_hate_speech_label": True,
            "attribution_per_slide": True,
            "context_warning_when_sensitive": True,
        },
    }


# ── Remotion render props ────────────────────────────────────────────────────

def build_remotion_props(manifest: dict) -> dict:
    """Render props for scripts/remotion/src/EvidenceCompilation.tsx
    (composition to be authored in Phase 3 final-mile).

    Shape mirrors what existing CarouselReel/NewsReel compositions consume:
    a flat sequence of segments with type + duration + payload.
    """
    person = manifest.get("person", {}) or {}
    person_name = person.get("name", "Unknown person")
    seed = manifest.get("seed", {}) or {}
    verified = manifest.get("verified_clips", []) or []
    niche = manifest.get("niche", "brazil")

    segments = [{
        "kind": "title_card",
        "duration_sec": 2.0,
        "title": person_name,
        "subtitle": "O que mais foi dito" if niche == "brazil" else "What else was said",
        "person_name": person_name,
    }, {
        "kind": "seed_clip",
        "duration_sec": 3.0,
        "url": seed.get("url", ""),
        "transcript_excerpt": (seed.get("transcript_excerpt") or "")[:240],
        "role": "intro_hook",
    }]

    for vc in verified:
        score = vc.get("score", {}) or {}
        cand = vc.get("candidate", {}) or {}
        is_sensitive = score.get("claim_type") in SENSITIVE_CLAIM_TYPES
        if is_sensitive or score.get("context_needed"):
            segments.append({
                "kind": "context_card",
                "duration_sec": 2.0,
                "warning": score.get("context_needed", "Contexto necessário"),
                "claim_type": score.get("claim_type", ""),
            })
        segments.append({
            "kind": "evidence_clip",
            "duration_sec": 5.0,
            "url": cand.get("url", ""),
            "platform": cand.get("platform", ""),
            "uploader": cand.get("uploader", ""),
            "title": cand.get("title", "")[:200],
            "quote": score.get("best_quote", ""),
            "timestamp_start": score.get("timestamp_start", "00:00"),
            "timestamp_end": score.get("timestamp_end", "00:00"),
            "claim_type": score.get("claim_type", ""),
            "match_score": score.get("match_score", 0.0),
            "attribution": f"@{cand.get('uploader','')} · {cand.get('platform','')}",
        })

    segments.append({
        "kind": "verdict_card",
        "duration_sec": 3.0,
        "question": ("E você, o que acha?" if niche == "brazil"
                     else "What do you think?"),
        "person_name": person_name,
    })
    segments.append({
        "kind": "sources_card",
        "duration_sec": 3.0,
        "sources": [
            {"url": (vc.get("candidate", {}) or {}).get("url", ""),
             "platform": (vc.get("candidate", {}) or {}).get("platform", "")}
            for vc in verified
        ],
    })

    total_sec = sum(s.get("duration_sec", 0) for s in segments)
    return {
        "schema_version": 1,
        "composition_id": "EvidenceCompilation",
        "format": "vertical_9_16",
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "duration_seconds": total_sec,
        "person_name": person_name,
        "niche": niche,
        "segment_count": len(segments),
        "segments": segments,
        "constraints": {
            "attribution_overlay_every_segment": True,
            "fair_use_commentary_required": True,
            "no_buffer_before_approval": True,
        },
    }


def build_sources_block(manifest: dict) -> str:
    verified = manifest.get("verified_clips", []) or []
    lines = []
    for i, vc in enumerate(verified, start=1):
        cand = vc.get("candidate", {}) or {}
        score = vc.get("score", {}) or {}
        lines.append(
            f"{i}. [{cand.get('platform','?')}] {cand.get('uploader','')} — "
            f"{cand.get('title','')[:80]} ({score.get('timestamp_start','?')}–"
            f"{score.get('timestamp_end','?')})\n   {cand.get('url','')}"
        )
    return "\n".join(lines)


# ── orchestrator ─────────────────────────────────────────────────────────────

def render_all(manifest_path: str, output_dir: str | None = None) -> dict:
    """Read manifest, run pre-render audit, write all spec files."""
    manifest = load_manifest(manifest_path)
    ok, issues = audit_pre_render(manifest)
    out_dir = Path(output_dir) if output_dir else Path(manifest_path).parent / "render_specs"
    out_dir.mkdir(parents=True, exist_ok=True)

    audit_path = out_dir / "pre_render_audit.json"
    with open(audit_path, "w") as f:
        json.dump({
            "ok": ok, "issues": issues,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)

    if not ok:
        # Don't write specs when audit fails — block render
        return {
            "ok": False,
            "audit": str(audit_path),
            "issues": issues,
            "carousel_spec": None,
            "remotion_props": None,
        }

    carousel_spec = build_carousel_spec(manifest)
    remotion_props = build_remotion_props(manifest)
    sources_block = build_sources_block(manifest)

    cs_path = out_dir / "carousel_content_spec.json"
    rp_path = out_dir / "remotion_props.json"
    sb_path = out_dir / "sources_block.txt"

    with open(cs_path, "w") as f:
        json.dump(carousel_spec, f, indent=2, ensure_ascii=False)
    with open(rp_path, "w") as f:
        json.dump(remotion_props, f, indent=2, ensure_ascii=False)
    sb_path.write_text(sources_block, encoding="utf-8")

    return {
        "ok": True,
        "audit": str(audit_path),
        "carousel_spec": str(cs_path),
        "remotion_props": str(rp_path),
        "sources_block": str(sb_path),
        "issues": [],
        "summary": {
            "person_name": manifest.get("person", {}).get("name", ""),
            "verified_count": len(manifest.get("verified_clips", [])),
            "carousel_slide_count": carousel_spec["slide_count"],
            "remotion_segment_count": remotion_props["segment_count"],
            "remotion_duration_seconds": remotion_props["duration_seconds"],
        },
    }


# ── CLI for ad-hoc / approval-handler use ───────────────────────────────────

if __name__ == "__main__":
    import sys
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("manifest", help="Path to evidence_manifest.json")
    p.add_argument("--output-dir", default=None)
    args = p.parse_args()
    result = render_all(args.manifest, args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["ok"] else 1)
