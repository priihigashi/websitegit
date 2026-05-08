#!/usr/bin/env python3
"""
opc_template_chooser.py — OPC template recommender + slide-by-slide planner.

Reads docs/templates/opc_template_intelligence.json and produces:
1. A whole-carousel recommendation (legacy, story-aware).
2. A 5-slide plan with one template_id + role per slide (Phase 4 — feature
   flagged via OPC_SLIDE_PLANNER_ENABLED). Phase 6 wired Python builders for
   all 7 standalones; Phase 8 added per-template content generation, image
   queries, image fetching, and reviewer content gates.

Important safety rules:
- Does NOT import carousel_builder.py.
- Does NOT render HTML/PNG/MP4.
- Does NOT upload to Drive or schedule posts.
- Does NOT rename files or change production routing.
- Treats Oak Park as Oak Park Construction in Pompano Beach / South Florida,
  not Oak Park, Illinois. Architecture references are allowed only when they
  support construction/remodel/design education; tourism/community framing is blocked.
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
            # Structural/concrete components (added 2026-05-06 — Phase 3)
            "rebar", "formwork", "foundation", "slab", "footing", "stud",
            "framing", "shingle", "shingles", "siding", "stucco", "insulation",
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

# Terms that prove the content is about Oak Park Construction / South Florida
# remodeling, not the Village of Oak Park, Illinois. Keep broad enough for
# architecture/design inspiration, but require a construction/remodel purpose.
OPC_CONSTRUCTION_TERMS = [
    # Core OPC identity
    "construction", "contractor", "general contractor", "remodel", "remodeling",
    "renovation", "homeowner", "homeowners", "bathroom", "kitchen", "addition",
    "build", "builder", "jobsite", "job site", "project", "permit", "inspection",
    "code", "material", "tile", "drywall", "cabinet", "countertop", "flooring",
    "concrete", "waterproof", "layout", "design-build", "south florida",
    "pompano", "broward", "miami-dade", "palm beach", "oak park construction",
    "opc", "mike", "michael", "matthew",
    # Structural / concrete (added 2026-05-06 — Phase 3 verification surfaced gap)
    "rebar", "formwork", "foundation", "slab", "footing", "structural",
    "load bearing", "beam", "joist", "header", "framing", "stud", "wall framing",
    # Roofing / envelope
    "roof", "roofing", "shingle", "shingles", "fascia", "soffit", "gutter",
    "siding", "stucco",
    # Finishing
    "paint", "painting", "primer", "caulk", "sealant", "grout", "trim",
    "baseboard", "crown molding", "wainscot",
    # Systems
    "plumbing", "electrical", "hvac", "ductwork", "insulation", "vapor barrier",
    # Outdoor
    "deck", "decking", "patio", "porch", "driveway", "walkway", "retaining wall",
    # Common issues / inspections
    "crack", "mold", "water damage", "leak", "settlement", "moisture",
]

# Terms that usually mean the wrong entity: Oak Park, Illinois / tourism / city
# content. Frank Lloyd Wright can be used as an architecture reference, but if
# these appear without construction terms, block the item before template choice.
OAK_PARK_ILLINOIS_OR_TOURISM_TERMS = [
    "oak park, illinois", "oak park illinois", "chicago suburb", "suburb of chicago",
    "near chicago", "west of chicago", "cta", "green line", "blue line",
    "frank lloyd wright", "ernest hemingway", "unity temple", "hemingway",
    "village of oak park", "oak park arts district", "downtown oak park",
    "visit oak park", "plan your visit", "hidden gems", "restaurants",
    "food scene", "festivals", "tourism", "travel", "travel guide", "trip to chicago",
    "save this for your trip", "tag someone who needs to visit",
]


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Registry not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _clean_comparison_entity(value: str) -> str:
    """Normalize one side of an X-vs-Y comparison without losing brand terms."""
    value = re.sub(r"\([^)]*\)", " ", value or "")
    value = re.sub(r"\b(which|wins?|winner|better|best|costs?|pros?|cons?)\b.*$", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(for|in|on|with|without|before|after)\b.*$", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"^(?:should\s+i\s+choose|choose|pick|is|are|the|a|an)\s+", "", value.strip(), flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n-—:;,.?!\"'")
    words = value.split()
    # Keep entities compact; trailing explanatory words usually make matching noisy.
    if len(words) > 4:
        value = " ".join(words[:4])
    return value.strip()


def extract_comparison_pair(topic: str, brief: str = "") -> dict[str, str] | None:
    """Extract the two subjects from clear comparison topics.

    This is intentionally conservative. It only returns a pair when the wording
    has an explicit comparator such as "X vs Y", "X versus Y", or "X or Y".
    """
    text = re.sub(r"\s+", " ", f"{topic or ''} {brief or ''}").strip()
    patterns = [
        r"(?P<left>[A-Za-z0-9][A-Za-z0-9 &/\-]{1,60}?)\s+(?:vs\.?|versus)\s+(?P<right>[A-Za-z0-9][A-Za-z0-9 &/\-]{1,60}?)(?:[:?!.—-]|$)",
        r"(?:choose|pick|use|install)\s+(?P<left>[A-Za-z0-9][A-Za-z0-9 &/\-]{1,50}?)\s+or\s+(?P<right>[A-Za-z0-9][A-Za-z0-9 &/\-]{1,50}?)(?:[:?!.—-]|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        left = _clean_comparison_entity(m.group("left"))
        right = _clean_comparison_entity(m.group("right"))
        if len(left) >= 2 and len(right) >= 2 and left.lower() != right.lower():
            return {"left": left, "right": right}
    return None


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


def detect_opc_content_fit(topic: str, brief: str = "") -> dict[str, Any]:
    """Gate OPC selection before template scoring.

    Oak Park must mean Oak Park Construction (Pompano Beach / South Florida),
    not Oak Park, Illinois. Architecture references are allowed only when they
    support construction/remodel/design education.
    """
    combined = normalize(f"{topic} {brief}")
    construction_hits = keyword_hits(combined, OPC_CONSTRUCTION_TERMS)
    wrong_entity_hits = keyword_hits(combined, OAK_PARK_ILLINOIS_OR_TOURISM_TERMS)

    if wrong_entity_hits and not construction_hits:
        return {
            "status": "blocked_needs_reclassification",
            "is_opc_construction_ready": False,
            "reason": (
                "Content appears to treat Oak Park as Oak Park, Illinois / tourism / community content. "
                "For this pipeline, Oak Park means Oak Park Construction in Pompano Beach / South Florida."
            ),
            "construction_hits": construction_hits,
            "wrong_entity_hits": wrong_entity_hits,
        }

    if not construction_hits:
        return {
            "status": "needs_human_review",
            "is_opc_construction_ready": False,
            "reason": (
                "No strong construction/remodel/homeowner/service/material/project signal was detected. "
                "Do not select an OPC template until the topic is confirmed as Oak Park Construction content."
            ),
            "construction_hits": construction_hits,
            "wrong_entity_hits": wrong_entity_hits,
        }

    return {
        "status": "passed",
        "is_opc_construction_ready": True,
        "reason": "Construction/remodel/homeowner OPC signals detected.",
        "construction_hits": construction_hits,
        "wrong_entity_hits": wrong_entity_hits,
    }


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
    comparison_pair = extract_comparison_pair(topic, brief)

    generic_hook_risk = [p for p in GENERIC_HOOK_PHRASES if p in combined]

    return {
        "matches": matches,
        "central_tension": central_tension,
        "audience_question": audience_question,
        "proof_needed": proof_needed,
        "payoff": payoff,
        "comparison_pair": comparison_pair,
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

        for idx, preferred_id in enumerate(preferred_order):
            if tid == preferred_id:
                score += max(1, 5 - min(idx, 4))
        reasons.extend(signal_reasons.get(tid, []))

        score += int(template.get("priscila_preference_score") or 0)
        if template.get("registry_kind") == "full_carousel":
            score += 1
            reasons.append("safe full-carousel option")
        if template.get("wiring_status", "").startswith("active_pipeline"):
            score += 1
            reasons.append("already has an active pipeline key today")
        if template.get("wiring_status", "").startswith("gallery_only"):
            # wiring_status stays gallery_only_* until Phase 8 verification —
            # but if phase_8_status == "pending_verification" the Python builder
            # IS wired and the planner is safe to pick this slot.
            if template.get("phase_8_status") == "pending_verification":
                reasons.append("Python builder wired (Phase 8 — pending end-to-end verification)")
            else:
                reasons.append("gallery-only today; dry-run recommendation only")

        if tid == "opc_progress_media" and "progress" not in story["matches"]:
            score -= 4
            reasons.append("not primary unless real progress/proof media exists")

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
    content_fit = detect_opc_content_fit(topic, brief)
    if not content_fit["is_opc_construction_ready"]:
        return {
            "mode": "dry_run_only",
            "status": content_fit["status"],
            "topic": topic,
            "brief_present": bool(brief.strip()),
            "opc_content_fit": content_fit,
            "primary_recommendation": None,
            "backup_recommendations": [],
            "do_not_use": [
                {
                    "template_id": "all_opc_templates",
                    "reason": "OPC templates are blocked until the topic is confirmed as Oak Park Construction / South Florida remodeling content."
                }
            ],
            "confidence": "blocked",
            "safety_notes": [
                "No rendering performed.",
                "No production routing changed.",
                "No filenames renamed.",
                "Oak Park must mean Oak Park Construction in Pompano Beach / South Florida, not Oak Park, Illinois.",
            ],
        }

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
        "status": "passed",
        "topic": topic,
        "brief_present": bool(brief.strip()),
        "opc_content_fit": content_fit,
        "storytelling_read": {
            "central_tension": story["central_tension"],
            "audience_question": story["audience_question"],
            "proof_needed": story["proof_needed"],
            "payoff": story["payoff"],
            "comparison_pair": story.get("comparison_pair"),
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
            "For OPC, only niche=opc templates were considered after OPC content-fit passed.",
            "Issue #122 storytelling logic is represented as story read: tension, audience question, proof, payoff, clarity.",
        ],
    }


# =============================================================================
# Phase 3 — slide-by-slide planner
# =============================================================================
#
# plan_carousel_slides() returns a 5-slide plan with per-slide template_id, role,
# content goal, image need, required content fields, and a production_safe flag.
#
# All 12 OPC template IDs the planner can emit are production-safe as of
# Phase 6 (commit 690873f) — both the 5 tip components and the 7 standalones
# (opc_duotone, opc_base, opc_statement, opc_material_profile,
# opc_item_spotlight, opc_four_card_grid, opc_progress_media) have Python
# builders in carousel_builder.py and scoped CSS in opc_standalones.css.
#
# fallback_template_id is still emitted for each standalone as an emergency
# escape hatch — build_opc_from_slide_plan() uses it only if the standalone's
# entry is somehow missing from OPC_STANDALONE_COMPONENT_RENDERERS at runtime.
# The reviewer flags any actual fallback as a bug (not a missing-builder case).

PRODUCTION_SAFE_TEMPLATE_IDS = {
    "opc_tip_cover",
    "opc_tip_stat",
    "opc_tip_list",
    "opc_tip_explainer",
    "opc_tip_sources",
    # Phase 6 standalones — Python builders shipped 2026-05-06 (commit 690873f).
    "opc_duotone",
    "opc_base",
    "opc_statement",
    "opc_material_profile",
    "opc_item_spotlight",
    "opc_four_card_grid",
    "opc_progress_media",
}

# Standalone template_id → safest tip component to use as a defensive
# fallback. As of Phase 6 (2026-05-06) every standalone has a Python builder,
# so this map is only consulted if a standalone goes missing at runtime.
STANDALONE_TO_TIP_FALLBACK = {
    "opc_duotone":          "opc_tip_cover",
    "opc_base":             "opc_tip_cover",
    "opc_statement":        "opc_tip_explainer",
    "opc_material_profile": "opc_tip_stat",
    "opc_item_spotlight":   "opc_tip_list",
    "opc_four_card_grid":   "opc_tip_list",
    "opc_progress_media":   "opc_tip_explainer",
}

# Required content fields per slide template. Used by the content-generation
# step (separate from the planner) to know what to fill in. For Phase 3 the
# field names mirror the keys already produced by carousel_builder for tip
# slides; standalone schemas come from each template's HTML structure (Phase 6
# will validate them).
SLIDE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "opc_tip_cover":        ["headline", "accent_word", "subhead"],
    "opc_tip_stat":         ["slide2_stat", "slide2_label", "slide2_headline"],
    "opc_tip_list":         ["slide3_items"],
    "opc_tip_explainer":    ["slide4_headline", "slide4_body"],
    "opc_tip_sources":      ["sources", "cta"],
    "opc_duotone":          ["claim", "photo", "quote_text", "attr_name", "duotone_variant"],
    "opc_base":             ["headline", "hook", "byline", "tag", "stamp_text", "bg_photo", "sticker_photo"],
    "opc_statement":        ["tag", "quote_opener", "quote_body", "attribution", "person_photo"],
    "opc_material_profile": [
        "profile_label", "profile_headline",
        "profile_grid_best_for", "profile_grid_not_ideal",
        "profile_grid_durability", "profile_grid_install",
        "profile_grid_cost", "profile_grid_style", "profile_tags",
    ],
    "opc_item_spotlight":   ["tag", "category", "headline", "sub", "fact_list"],
    "opc_four_card_grid":   ["headline", "subhead", "cards"],
    "opc_progress_media":   ["tag", "title", "title_em", "description", "media_frame", "pill_tags"],
}

# Image-need hint per template. The pipeline's image-fetch step uses this to
# pick a Pexels/Pixabay/Apify query strategy and to enforce visual variety.
SLIDE_IMAGE_NEED: dict[str, str] = {
    "opc_tip_cover":        "1 hero photo (jobsite/material/finished room) — covers the slide bg",
    "opc_tip_stat":         "1 context image (stat-relevant photo or branded placeholder)",
    "opc_tip_list":         "1 context image (process/jobsite/checklist visual)",
    "opc_tip_explainer":    "1 context image (technique/detail/before-after) — landscape",
    "opc_tip_sources":      "1 hero photo (re-uses cover or last context image as bg)",
    "opc_duotone":          "1 hero photo, dramatic, high-contrast — duotone filter applied",
    "opc_base":             "2 photos: bg hero + sticker portrait (project detail)",
    "opc_statement":        "1 person photo (Mike, homeowner, inspector) — B&W treated",
    "opc_material_profile": "0 (text-only material grid) — optional material thumbnail",
    "opc_item_spotlight":   "1 product/material thumbnail (260×340)",
    "opc_four_card_grid":   "4 photos (one per card, 185×185)",
    "opc_progress_media":   "1 photo or video (920×585) — required, real jobsite",
}


def _slide_goal(template_id: str, role: str, topic: str) -> str:
    """Short prose describing what THIS slide should communicate. Reads as a brief
    for the content step (planner does NOT generate copy itself)."""
    role_goals = {
        "cover":      f"Hook the homeowner on '{topic}' — name the cost or risk in one line.",
        "definition": f"Define the key thing in '{topic}' so a non-expert understands it.",
        "comparison": f"Compare 3-4 options/checks/scenarios that decide the outcome of '{topic}'.",
        "statement":  f"Land one warning, rule, or quote that crystallizes the lesson of '{topic}'.",
        "sources":    f"List 2-3 sources and a save-this CTA for '{topic}'.",
    }
    template_goals = {
        "opc_duotone":          "Open with a bold warning/red-flag headline + duotone hero image.",
        "opc_base":             "Calm topic intro with bg photo + sticker portrait of project detail.",
        "opc_material_profile": "Present material/product profile in a 6-field grid (best-for, not-ideal, durability, install, cost, style).",
        "opc_item_spotlight":   "Spotlight ONE item (cabinet, tile, fixture) with 4 key fact bullets.",
        "opc_four_card_grid":   "Show exactly 4 options/products/decisions side-by-side as cards.",
        "opc_statement":        "Carry one quoted line + attribution (Mike / homeowner / inspector).",
        "opc_progress_media":   "Show real jobsite proof — before/during/after photo with description.",
    }
    if template_id in template_goals:
        return template_goals[template_id]
    return role_goals.get(role, f"Slide {role} for '{topic}'.")


def plan_carousel_slides(
    topic: str,
    brief: str = "",
    registry_path: Path = DEFAULT_REGISTRY,
) -> dict[str, Any]:
    """Return a 5-slide plan: each slide gets its own template_id chosen for the
    slide ROLE and the topic's storytelling signals.

    Output schema:
      {
        "topic": str,
        "status": "passed" | "blocked",
        "primary_recommendation": dict | None,   # whole-carousel rec (legacy)
        "matched_signals": list[str],
        "slides": [
          {
            "slide": 1..5,
            "role": "cover" | "definition" | "comparison" | "statement" | "sources",
            "template_id": str,
            "content_goal": str,
            "image_need": str,
            "required_fields": list[str],
            "production_safe": bool,
            "fallback_template_id": str | None,
          },
          ...
        ],
        "safety_notes": [...],
      }

    The renderer uses fallback_template_id when production_safe=False so the
    plan can describe the IDEAL design even before standalones are wired."""
    rec = build_recommendation(topic, brief, registry_path)
    if rec.get("status") != "passed":
        return {
            "topic": topic,
            "status": rec.get("status", "blocked"),
            "reason": rec.get("opc_content_fit", {}).get("reason", "blocked"),
            "primary_recommendation": rec.get("primary_recommendation"),
            "matched_signals": [],
            "slides": [],
            "safety_notes": [
                "Plan blocked at OPC content-fit gate.",
                "No rendering performed.",
            ],
        }

    matches = rec["storytelling_read"]["matched_signals"]
    comparison_pair = rec["storytelling_read"].get("comparison_pair")

    # Slide 1 — cover
    if "warning" in matches:
        s1 = "opc_duotone"
    elif "progress" in matches:
        s1 = "opc_base"
    else:
        s1 = "opc_tip_cover"

    # Slide 2 — definition / stat
    if comparison_pair and "comparison" in matches:
        # Comparison topics need a paired contract. The material_profile
        # standalone is intentionally singular, so keep slide 2 on the tip stat
        # component and let slide 3 carry the four-card head-to-head.
        s2 = "opc_tip_stat"
    elif "material" in matches:
        s2 = "opc_material_profile"
    elif "single_item" in matches:
        s2 = "opc_item_spotlight"
    else:
        s2 = "opc_tip_stat"

    # Slide 3 — comparison / list / spotlight
    if "comparison" in matches:
        s3 = "opc_four_card_grid"
    elif "single_item" in matches and s2 != "opc_item_spotlight":
        s3 = "opc_item_spotlight"
    else:
        s3 = "opc_tip_list"

    # Slide 4 — statement / explainer / proof
    if "quote_statement" in matches:
        s4 = "opc_statement"
    elif "progress" in matches:
        s4 = "opc_progress_media"
    else:
        s4 = "opc_tip_explainer"

    # Slide 5 — sources / CTA (only one template handles this role today)
    s5 = "opc_tip_sources"

    role_for_slide = {
        1: "cover", 2: "definition", 3: "comparison",
        4: "statement", 5: "sources",
    }
    plan_slides = []
    for slide_num, template_id in [(1, s1), (2, s2), (3, s3), (4, s4), (5, s5)]:
        role = role_for_slide[slide_num]
        production_safe = template_id in PRODUCTION_SAFE_TEMPLATE_IDS
        plan_slides.append({
            "slide": slide_num,
            "role": role,
            "template_id": template_id,
            "content_goal": (
                _slide_goal(template_id, role, topic)
                + (
                    f" Comparison contract: show both {comparison_pair['left']} and "
                    f"{comparison_pair['right']} with equal weight."
                    if comparison_pair and role in {"cover", "definition", "comparison", "statement"} else ""
                )
            ),
            "image_need": SLIDE_IMAGE_NEED.get(template_id, "1 image"),
            "required_fields": SLIDE_REQUIRED_FIELDS.get(template_id, []),
            "production_safe": production_safe,
            "fallback_template_id": (
                STANDALONE_TO_TIP_FALLBACK.get(template_id) if not production_safe else None
            ),
        })

    return {
        "topic": topic,
        "status": "passed",
        "primary_recommendation": rec.get("primary_recommendation"),
        "matched_signals": list(matches.keys()),
        "comparison_pair": comparison_pair,
        "slides": plan_slides,
        "safety_notes": [
            "Plan only — no rendering performed.",
            "All 7 standalone Python builders shipped in Phase 6 (commit 690873f); fallback_template_id is now a defensive escape hatch only.",
            "Banned legacy keys cutout/illustrated cannot appear in any slot.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run OPC template chooser")
    parser.add_argument("topic", help="OPC topic or working title")
    parser.add_argument("--brief", default="", help="Optional brief/details to improve classification")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="Path to opc_template_intelligence.json")
    parser.add_argument("--pretty", action="store_true", help="Print a readable summary instead of JSON")
    parser.add_argument("--plan", action="store_true",
                        help="Return a 5-slide plan instead of a single template recommendation")
    args = parser.parse_args()

    if args.plan:
        result = plan_carousel_slides(args.topic, args.brief, Path(args.registry))
        if not args.pretty:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        print(f"Topic: {result['topic']}")
        print(f"Status: {result['status']}")
        if result["status"] != "passed":
            print(f"Reason: {result.get('reason')}")
            return 0
        print(f"Matched signals: {', '.join(result['matched_signals']) or '(none)'}")
        print("\n5-Slide Plan:")
        for s in result["slides"]:
            mark = "✓" if s["production_safe"] else "⏳"
            fb = f" (fallback → {s['fallback_template_id']})" if s["fallback_template_id"] else ""
            print(f"  {mark} Slide {s['slide']} [{s['role']}] {s['template_id']}{fb}")
            print(f"      Goal: {s['content_goal']}")
            print(f"      Image: {s['image_need']}")
        for note in result["safety_notes"]:
            print(f"  · {note}")
        return 0

    result = build_recommendation(args.topic, args.brief, Path(args.registry))
    if not args.pretty:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(f"Topic: {result['topic']}")
    print(f"Mode: {result['mode']}")
    print(f"Status: {result.get('status', 'unknown')}")
    content_fit = result.get("opc_content_fit", {})
    print(f"OPC fit: {content_fit.get('status')} — {content_fit.get('reason')}")
    if content_fit.get("wrong_entity_hits"):
        print(f"Wrong-entity/tourism hits: {', '.join(content_fit['wrong_entity_hits'])}")
    if content_fit.get("construction_hits"):
        print(f"Construction hits: {', '.join(content_fit['construction_hits'])}")

    if result.get("primary_recommendation") is None:
        print("\nPrimary recommendation: BLOCKED — no OPC template selected.")
        for item in result.get("do_not_use", []):
            print(f"- {item['template_id']}: {item['reason']}")
        print("Safety: dry-run only; no rendering/routing/filename changes.")
        return 0

    primary = result["primary_recommendation"]
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
