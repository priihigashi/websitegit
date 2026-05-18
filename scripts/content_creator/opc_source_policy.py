"""OPC source policy helpers.

Generation should not cite sources that the reviewer is guaranteed to block.
This module keeps the banned-source list and deterministic repair logic in one
place so prompts, pre-render cleanup, and reviewer tests do not drift.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


OPC_BANNED_SOURCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "aci 314.1r",
        "ACI 314.1R is not an OPC masonry-maintenance source; use TMS 402/602 or ACI 530/ASCE 5 only for structural/code criteria.",
    ),
    (
        "angi",
        "Angi/HomeAdvisor consumer quote guides cannot be primary proof for OPC numeric claims.",
    ),
    (
        "homeadvisor",
        "Angi/HomeAdvisor consumer quote guides cannot be primary proof for OPC numeric claims.",
    ),
    (
        "thumbtack",
        "Thumbtack consumer quote guides cannot be primary proof for OPC numeric claims.",
    ),
    (
        "fixr",
        "Fixr consumer quote guides cannot be primary proof for OPC numeric claims.",
    ),
    (
        "reddit",
        "Reddit threads cannot be primary proof for OPC numeric claims.",
    ),
    (
        "quora",
        "Quora answers cannot be primary proof for OPC numeric claims.",
    ),
    (
        "wikihow",
        "WikiHow articles cannot be primary proof for OPC numeric claims.",
    ),
    (
        "houzz reviews",
        "Houzz consumer reviews cannot be primary proof for OPC numeric claims.",
    ),
    (
        "houzz.com",
        "Houzz consumer pages cannot be primary proof for OPC numeric claims.",
    ),
)

OPC_SAFE_SOURCE_FALLBACKS: tuple[str, ...] = (
    "Florida Building Code - residential construction requirements and code minimums",
    "International Residential Code - residential building code requirements",
    "NAHB Cost of Constructing a Home - construction cost category benchmarks",
    "UF IFAS Extension - Florida termite and wood-destroying organism guidance",
    "TMS 402/602 and ACI 530/ASCE 5 - masonry structural code criteria",
)


def _contains_banned_source(text: Any) -> tuple[str, str] | None:
    blob = str(text or "").lower()
    for token, message in OPC_BANNED_SOURCE_PATTERNS:
        if token in blob:
            return token, message
    return None


def find_banned_source_hits(value: Any, *, path: str = "content") -> list[dict[str, str]]:
    """Return banned-source hits in strings nested under a JSON-like object."""
    hits: list[dict[str, str]] = []
    if isinstance(value, str):
        hit = _contains_banned_source(value)
        if hit:
            token, message = hit
            hits.append({"path": path, "token": token, "message": message, "value": value})
        return hits
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).startswith("_"):
                continue
            hits.extend(find_banned_source_hits(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            hits.extend(find_banned_source_hits(child, path=f"{path}[{idx}]"))
    return hits


def _clean_source_list(sources: Any) -> tuple[list[str], list[str]]:
    if isinstance(sources, str):
        raw_sources = [sources]
    elif isinstance(sources, list):
        raw_sources = [s for s in sources if isinstance(s, str) and s.strip()]
    else:
        raw_sources = []

    kept: list[str] = []
    removed: list[str] = []
    for source in raw_sources:
        if _contains_banned_source(source):
            removed.append(source)
            continue
        kept.append(source)

    for fallback in OPC_SAFE_SOURCE_FALLBACKS:
        if len(kept) >= 3:
            break
        if not any(fallback.lower() == existing.lower() for existing in kept):
            kept.append(fallback)

    return kept, removed


def _scrub_banned_source_mentions(value: Any) -> Any:
    """Remove banned-source names from display fields without deleting copy."""
    if isinstance(value, str):
        cleaned = value
        replacements = {
            "Angi/HomeAdvisor aggregate repair data": "scope-dependent contractor estimate",
            "Angi/HomeAdvisor": "scope-dependent contractor estimate",
            "HomeAdvisor": "audit-grade source",
            "Angi": "audit-grade source",
            "Thumbtack": "audit-grade source",
            "Fixr": "audit-grade source",
            "Reddit": "audit-grade source",
            "Quora": "audit-grade source",
            "WikiHow": "audit-grade source",
            "Houzz.com": "audit-grade source",
            "Houzz reviews": "audit-grade source",
            "ACI 314.1R": "TMS 402/602 or ACI 530/ASCE 5",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        return cleaned
    if isinstance(value, list):
        return [_scrub_banned_source_mentions(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (_scrub_banned_source_mentions(child) if not str(key).startswith("_") else child)
            for key, child in value.items()
        }
    return value


def enforce_opc_source_policy(content: dict, topic: str = "", brief: str = "") -> dict:
    """Strip banned OPC sources before render/review.

    The function is deterministic by design: no extra LLM call, no new secret,
    and no hidden runtime dependency. It only mutates OPC source fields enough
    to prevent known-bad citations from killing a run after media/render costs
    have already been spent.
    """
    if not isinstance(content, dict):
        return content

    before_hits = find_banned_source_hits(content)
    original_sources = content.get("sources")
    original_count = (
        len(original_sources)
        if isinstance(original_sources, list)
        else (1 if isinstance(original_sources, str) and original_sources.strip() else 0)
    )
    sources, removed_sources = _clean_source_list(original_sources)
    if not before_hits and not removed_sources:
        return content

    repaired = deepcopy(content)
    repaired = _scrub_banned_source_mentions(repaired)
    repaired["sources"] = sources
    repaired["_opc_source_policy"] = {
        "changed": True,
        "topic": topic,
        "removed_sources": removed_sources,
        "banned_hits": before_hits,
        "fallback_sources_added": max(0, len(sources) - max(0, original_count - len(removed_sources))),
        "mode": "deterministic_pre_review_repair",
    }
    print(
        "  [source-policy] removed banned OPC source references: "
        + ", ".join(sorted({hit["token"] for hit in before_hits}) or ["sources"])
    )
    return repaired
