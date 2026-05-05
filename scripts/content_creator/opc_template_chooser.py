#!/usr/bin/env python3
"""
opc_template_chooser.py — dry-run OPC template recommender.

Phase 2 only: reads docs/templates/opc_template_intelligence.json and prints a
story-aware template recommendation for Oak Park Construction topics.

Important safety rules:
- Does NOT import carousel_builder.py.
- Does NOT render HTML/PNG/MP4.
- Does NOT upload to Drive or schedule posts.
- Does NOT rename files or change production routing.
- Keeps `tip` as a full 5-slide OPC educational carousel until slide-by-slide
  wiring is intentionally built.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "docs" / "templates" / "opc_template_intelligence.json"


SIGNALS = {
    "material": {
        "words": [
            "material", "materials", "tile", "tiles", "countertop", "quartz",
            "granite", "marble", "floor", "flooring", "cabinet", "cabinets",
            "paint", "finish", "finishes", "fixture", "fixtures", "waterproof",
            "grout", "backsplash", "shower", "drywall", "wood", "concrete",
        ],
        "story_job": "material/product/service explanation",
        "preferred": ["opc_material_profile", "opc_item_spotlight", "opc_tip"],
    },
    "single_item": {
        "words": [
            "one ", "single", "spotlight", "this product", "this material",
            "this finish", "this fixture", "this option", "profile",
        ],
        "story_job": "single product/material/style spotlight",
        "preferred": ["opc_item_spotlight", "opc_material_profile"],
    },
    "comparison": {
        "words": [
            "compare", "comparison", "versus", "vs", "options", "choices",
            "which", "best", "better", "pros", "cons", "4 ", "four ", "3 ",
            "three ", "list", "types", "ways",
        ],
        "story_job": "comparison/options decision",
        "preferred": ["opc_four_card_grid", "opc_tip", "opc_material_profile"],
    },
    "progress": {
        "words": [
            "before", "after", "before-after", "progress", "jobsite", "job site",
            "site update", "field", "install", "installation", "installed", "demo",
            "framing", "pour", "poured", "repair", "renovation update", "project update",
        ],
        "story_job": "project proof/progress story",
        "preferred": ["opc_progress_media", "opc_tip"],
    },
    "warning": {
        "words": [
            "mistake", "avoid", "warning", "red flag", "trap", "risk", "delay",
            "delays", "costly", "hidden", "problem", "fail", "fails", "bad",
            "cheap", "permit", "code", "inspection", "overrun", "change order",
        ],
        "story_job": "homeowner risk/warning/tension",
        "preferred": ["opc_duotone", "opc_tip", "opc_statement"],
    },
    "quote_statement": {
        "words": [
            "quote", "remember", "rule", "truth", "statement", "takeaway",
            "myth", "myths", "client asked", "homeowners ask", "what to ask",
        ],
        "story_job": "statement/quote/takeaway",
        "preferred": ["opc_statement", "opc_duotone", "opc_tip"],
    },
    "education": {
        "words": [
            "how to", "guide", "tips", "what to know", "understand", "explain",
            "explainer", "homeowner", "planning", "plan", "choose", "before you",
            "questions to ask", "checklist", "cost", "budget", "timeline",
        ],
        "story_job": "general homeowner education",
        "preferred": ["opc_tip", "opc_material_profile", "opc_statement"],
    },
}


GENERIC_HOOK_PHRASES = [
    "here's what you need to know",
    "lets talk about",
    "let's talk about",
    "important update",
    "things homeowners should know",
    "tips and tricks",
    "what to do",
]


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Registry not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def keyword_hits(text: str, words: list[str]) -> list[str]:
    hits: list[str] = []
    padded = f" {text} "
    for word in words:
        w = word.lower()
        if w.endswith(" ") or w.startswith(" "):
            if w in padded:
                hits.append(word.strip())
        elif w in text:
            hits.append(word)
    return hits


def classify_story(topic: str, brief: str = "") -> dict[str, Any]:
    combined = normalize(f"{topic} {brief}")
    matches: dict[str, dict[str, Any]] = {}

    for label, spec in SIGNALS.items():
        hits = keyword_hits(combined, spec["words"])
        if hits:
            matches[label] = {
                "hits": hits,
                "story_job": spec["story_job"],
                "preferred": spec["preferred"],
                "score": len(hits),
            }

    if not matches:
        matches["education"] = {
            "hits": [],
            "story_job": "general homeowner education",
            "preferred": SIGNALS["education"]["preferred"],
            "score": 1,
        }

    central_tension = infer_central_tension(matches)
    audience_question = infer_audience_question(matches)
    proof_needed = infer_proof_needed(matches)
    payoff = infer_payoff(matches)

    generic_hook_risk = [p for p in GENERIC_HOOK_PHRASES if p in combined]

    return {
        "matches": matches,
        "central_tension": central_tension,
        "audience_question": audience_question,
        "proof_needed": proof_needed,
        "payoff": payoff,
        "generic_hook_risk": generic_hook_risk,
    }


def infer_central_tension(matches: dict[str, Any]) -> str:
    keys = set(matches)
    if "warning" in keys and "material" in keys:
        return "Homeowner may choose a material/finish without seeing the hidden risk, cost, delay, or durability consequence."
    if "comparison" in keys and "material" in keys:
        return "Homeowner has multiple options but needs a clearer way to compare cost, durability, and fit."
    if "progress" in keys:
        return "The strongest story depends on real project proof: what changed, what stage it is in, and what the viewer should notice."
    if "warning" in keys:
        return "A common homeowner decision can create avoidable cost, delay, or scope problems."
    if "material" in keys or "single_item" in keys:
        return "A product/material/service choice looks simple, but the important tradeoffs are easy to miss."
    return "A homeowner needs a clear practical explanation before making a remodel decision."


def infer_audience_question(matches: dict[str, Any]) -> str:
    keys = set(matches)
    if "comparison" in keys:
        return "Which option should I choose, and what should I compare before deciding?"
    if "progress" in keys:
        return "What am I looking at, what changed, and why does this stage matter?"
    if "warning" in keys:
        return "What mistake should I avoid before this costs me time or money?"
    if "material" in keys or "single_item" in keys:
        return "Is this material/product/service actually the right fit for my project?"
    return "What should I understand before moving forward?"


def infer_proof_needed(matches: dict[str, Any]) -> list[str]:
    keys = set(matches)
    proof = ["contractor-safe explanation"]
    if "material" in keys or "single_item" in keys:
        proof.append("material/product photo or specific visual example")
    if "comparison" in keys:
        proof.append("side-by-side criteria such as cost, durability, maintenance, or use case")
    if "progress" in keys:
        proof.append("real project photo/video or before/during/after evidence")
    if "warning" in keys:
        proof.append("named consequence, cost/delay range, permit/code reference, or documented process risk")
    return proof


def infer_payoff(matches: dict[str, Any]) -> str:
    keys = set(matches)
    if "comparison" in keys:
        return "Give the viewer a practical comparison framework or shortlist."
    if "progress" in keys:
        return "Show what changed and what the viewer should learn from the stage/proof."
    if "warning" in keys:
        return "End with what to ask, check, document, or compare before signing/starting."
    if "material" in keys or "single_item" in keys:
        return "Clarify when the material/product/service is a good fit and what tradeoff matters most."
    return "Give a clear homeowner takeaway without sounding salesy or promise-based."


def score_templates(story: dict[str, Any], registry: dict[str, Any]) -> list[dict[str, Any]]:
    templates = [t for t in registry.get("templates", []) if t.get("niche") == "opc"]
    preferred_order: list[str] = []
    signal_reasons: dict[str, list[str]] = {}

    for label, match in story["matches"].items():
        for template_id in match["preferred"]:
            preferred_order.append(template_id)
            signal_reasons.setdefault(template_id, []).append(
                f"{label}: {match['story_job']}"
            )

    scored: list[dict[str, Any]] = []
    for template in templates:
        tid = template.get("id")
        score = 0
        reasons: list[str] = []

        # Main signal scoring.
        for idx, preferred_id in enumerate(preferred_order):
            if tid == preferred_id:
                score += max(1, 5 - min(idx, 4))
        reasons.extend(signal_reasons.get(tid, []))

        # Preference and status weighting, but keep it modest so signals win.
        score += int(template.get("priscila_preference_score") or 0)
        if template.get("registry_kind") == "full_carousel":
            score += 1
            reasons.append("safe full-carousel option")
        if template.get("wiring_status", "").startswith("active_pipeline"):
            score += 1
            reasons.append("already has an active pipeline key today")
        if template.get("wiring_status", "").startswith("gallery_only"):
            reasons.append("gallery-only today; dry-run recommendation only")

        # Penalize progress if no progress signal; it should not be chosen just because it scores high.
        if tid == "opc_progress_media" and "progress" not in story["matches"]:
            score -= 4
            reasons.append("not primary unless real progress/proof media exists")

        # Penalize full tip for very specific standalone jobs, but keep as backup.
        if tid == "opc_tip" and any(k in story["matches"] for k in ["single_item", "comparison", "material", "progress"]):
            score -= 1
            reasons.append("backup full-carousel option, not necessarily the best slide-role match")

        scored.append({
            "template_id": tid,
            "public_name": template.get("public_name"),
            "website_file": template.get("website_file"),
            "style_code": template.get("style_code"),
            "registry_kind": template.get("registry_kind"),
            "wiring_status": template.get("wiring_status"),
            "score": score,
            "reasons": reasons or ["general OPC fallback"],
            "needs_photo_or_video": template.get("needs_photo_or_video"),
            "visual_energy": template.get("visual_energy"),
            "production_safe_now": template.get("pipeline_key_today") is not None,
        })

    return sorted(scored, key=lambda item: item["score"], reverse=True)


def build_recommendation(topic: str, brief: str, registry_path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    registry = load_registry(registry_path)
    story = classify_story(topic, brief)
    scored = score_templates(story, registry)
    primary = scored[0]
    backups = scored[1:4]

    do_not_use: list[dict[str, str]] = []
    if "progress" not in story["matches"]:
        do_not_use.append({
            "template_id": "opc_progress_media",
            "reason": "No progress/before-after/jobsite proof signal was detected; this template should require real media."
        })
    if primary["template_id"] != "opc_tip":
        do_not_use.append({
            "template_id": "do_not_mix_tip_slides_yet",
            "reason": "opc_tip stays a full 5-slide carousel until slide-by-slide wiring is intentionally built."
        })

    confidence = "high" if primary["score"] - backups[0]["score"] >= 3 else "medium"
    if primary["wiring_status"].startswith("gallery_only"):
        confidence += "_as_dry_run_only"

    return {
        "mode": "dry_run_only",
        "topic": topic,
        "brief_present": bool(brief.strip()),
        "storytelling_read": {
            "central_tension": story["central_tension"],
            "audience_question": story["audience_question"],
            "proof_needed": story["proof_needed"],
            "payoff": story["payoff"],
            "matched_signals": story["matches"],
            "generic_hook_risk": story["generic_hook_risk"],
        },
        "primary_recommendation": primary,
        "backup_recommendations": backups,
        "do_not_use": do_not_use,
        "confidence": confidence,
        "safety_notes": [
            "No rendering performed.",
            "No production routing changed.",
            "No filenames renamed.",
            "For OPC, only niche=opc templates were considered.",
            "Issue #122 storytelling logic is represented as story read: tension, audience question, proof, payoff, clarity.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run OPC template chooser")
    parser.add_argument("topic", help="OPC topic or working title")
    parser.add_argument("--brief", default="", help="Optional brief/details to improve classification")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="Path to opc_template_intelligence.json")
    parser.add_argument("--pretty", action="store_true", help="Print a readable summary instead of JSON")
    args = parser.parse_args()

    result = build_recommendation(args.topic, args.brief, Path(args.registry))
    if not args.pretty:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    primary = result["primary_recommendation"]
    print(f"Topic: {result['topic']}")
    print(f"Mode: {result['mode']}")
    print("\nStorytelling read:")
    sr = result["storytelling_read"]
    print(f"- Central tension: {sr['central_tension']}")
    print(f"- Audience question: {sr['audience_question']}")
    print(f"- Proof needed: {', '.join(sr['proof_needed'])}")
    print(f"- Payoff: {sr['payoff']}")
    if sr["generic_hook_risk"]:
        print(f"- Generic hook risk: {', '.join(sr['generic_hook_risk'])}")

    print("\nPrimary recommendation:")
    print(f"- {primary['template_id']} ({primary['public_name']})")
    print(f"- File: {primary['website_file']} | Style: {primary['style_code']}")
    print(f"- Wiring: {primary['wiring_status']}")
    print(f"- Reason: {'; '.join(primary['reasons'])}")

    print("\nBackups:")
    for item in result["backup_recommendations"]:
        print(f"- {item['template_id']} — {item['public_name']} (score {item['score']})")

    if result["do_not_use"]:
        print("\nDo not use / caution:")
        for item in result["do_not_use"]:
            print(f"- {item['template_id']}: {item['reason']}")

    print(f"\nConfidence: {result['confidence']}")
    print("Safety: dry-run only; no rendering/routing/filename changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
