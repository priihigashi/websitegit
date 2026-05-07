"""evidence_scoring.py — transcript -> rubric score against requirement.

Public:
  score_candidate(candidate, transcript, person_name, requirement, seed_excerpt)
    -> dict (locked schema)
  validate_score(score)                -> (ok: bool, reasons: list[str])
  apply_build_gates(verified, rejected) -> dict (build_gates block)
  write_manifest(path, payload)        -> str (path)

LOCKED scoring schema (per CLAUDE.md / SH-104 / FLOW_person_evidence_mining):
{
  "same_person": bool,
  "person_confidence": float (0.0-1.0),
  "same_person_method": "metadata|transcript|title|channel|user_passed|face_match",
  "requirement_match": bool,
  "match_score": float (0.0-1.0),
  "claim_type": "group-targeting|dehumanizing|unfair-generalization|moral-contradiction|hypocrisy|needs-context",
  "best_quote": str,
  "timestamp_start": "MM:SS",
  "timestamp_end": "MM:SS",
  "targeted_group": str | None,
  "context_needed": str,
  "safe_to_use": bool,
  "why": str
}

NEVER auto-label "hate speech." Only the 6 categorical labels above.
NEVER use Detoxify. Haiku/Sonnet rubric only.

PHASE 2 PRIVACY (face-match — not yet built):
  When face-embedding verification ships, embeddings MUST be in-memory only
  for the duration of a single run, then discarded. Do NOT persist face
  embeddings to Drive, Sheets, logs, JSON manifests, or workflow artifacts.
  Phase 1 must NEVER pretend face-match has run — see _coerce_to_schema.
"""

from __future__ import annotations
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Sibling import shim (route_state, llm_router).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

CLAUDE_KEY = os.environ.get("CLAUDE_KEY_4_CONTENT", "")

ALLOWED_CLAIM_TYPES = {
    "group-targeting",
    "dehumanizing",
    "unfair-generalization",
    "moral-contradiction",
    "hypocrisy",
    "needs-context",
}

ALLOWED_SAME_PERSON_METHODS = {
    "metadata", "transcript", "title", "channel",
    "user_passed", "face_match",
}

SENSITIVE_CLAIM_TYPES = {
    "group-targeting",
    "dehumanizing",
    "moral-contradiction",
    "hypocrisy",
}

THIRD_PARTY_NARRATION_PATTERNS = [
    # ── Portuguese ───────────────────────────────────────────────────────────
    r"\bfoi denunciad[oa]s?\b",
    r"\bfoi acusado\b",
    r"\bfoi processado\b",
    r"\bfoi alvo\b",
    r"\bdeclara(?:ç|c)[õo]es consideradas\b",
    r"\bminist[eé]rio p[uú]blico\b",
    r"\bmp[-\s]?sp\b",
    r"\bo sacerdote\b.*\bfoi\b",
    r"\bo padre\b.*\bfoi\b",
    r"\bele quer\b",
    r"\bele coloca\b",
    r"\bele defende\b",
    r"\bele disse que\b",
    r"\bdisse que ele\b",
    r"\bsegundo\b.+\bele\b",
    # Anchored "denúncia" — only fires in narrative reporting context, not when
    # subject mentions the word inside their own statement.
    r"\b(?:foi|sofreu|recebeu)\s+(?:uma\s+)?den[uú]ncia\b",
    r"\bden[uú]ncia\s+(?:contra|sobre|feita|registrada|formal)\b",
    r"\bden[uú]ncia\s+ao\s+minist[eé]rio\b",
    r"\b(?:foi|sofreu|recebeu)\s+(?:uma\s+)?acusa(?:ç|c)[ãa]o\b",
    r"\bacusa(?:ç|c)[ãa]o\s+(?:contra|formal|criminal)\b",
    r"\bcritic[ao]\b.*\bele\b",
    # ── English mirrors (for EN-language news/commentary clips) ─────────────
    r"\bwas reported\b",
    r"\bwas accused\b",
    r"\bwas denounced\b",
    r"\bwas charged\b",
    r"\bwas sued\b",
    r"\bfaced\s+(?:a\s+)?(?:complaint|charge|lawsuit|investigation)\b",
    r"\b(?:a\s+)?complaint\s+(?:was\s+)?(?:filed|made)\s+against\b",
    r"\baccording to (?:him|her|them|the priest|the pastor|reports?)\b",
    r"\bsources?\s+say\b",
    r"\bcritics?\s+(?:say|claim|argue)\b",
    r"\bhe\s+(?:wants|claims|says|believes|argues|defends|attacks)\s+\w+",
    r"\bshe\s+(?:wants|claims|says|believes|argues|defends|attacks)\s+\w+",
    r"\bthe\s+(?:priest|pastor|cleric|cardinal|bishop|imam|rabbi)\s+(?:was|has been)\b",
    r"\bpublic ministry\b",
    r"\bdeemed\s+discriminatory\b",
    r"\bconsidered\s+discriminatory\b",
]

CONTRADICTION_SUPPORT_PATTERNS = [
    # ── Portuguese ───────────────────────────────────────────────────────────
    r"\bcontradiz\b",
    r"\bcontradi(?:ç|c)[ãa]o\b",
    r"\bhipocrisia\b",
    r"\bhip[oó]crita\b",
    r"\bposi(?:ç|c)[ãa]o anterior\b",
    r"\bdisse antes\b",
    r"\bj[áa] (?:disse|defendeu|pregava)\b.*\bagora\b",
    r"\bantes\b.*\bagora\b",
    r"\bmudou de (?:posi(?:ç|c)[ãa]o|opini[ãa]o|ideia)\b",
    r"\bvoltou atr[áa]s\b",
    # ── English mirrors ─────────────────────────────────────────────────────
    r"\bcontradicts?\b",
    r"\bcontradiction\b",
    r"\bhypocrisy\b",
    r"\bhypocrite\b",
    r"\bprevious\b",
    r"\bpast position\b",
    r"\bprior position\b",
    r"\bpreviously\b",
    r"\bcurrent statement\b",
    r"\bused to (?:say|preach|teach|argue|claim)\b",
    r"\bonce (?:said|preached|taught|argued|claimed)\b",
    r"\bchanged\s+(?:his|her|their)\s+(?:position|stance|mind|tune)\b",
    r"\bdoubled back\b",
    r"\bflip[- ]?flopped?\b",
]


# ── rubric prompt ────────────────────────────────────────────────────────────

_RUBRIC_PROMPT = """You are a fact-checking rubric scorer. You will score ONE candidate clip transcript against a stated evidence requirement about a public figure.

You are NOT publishing or making claims. You are producing a structured JSON scoring record. Your job: extract evidence FROM THE TRANSCRIPT, never from your own knowledge.

PERSON OF INTEREST: {person_name}

EVIDENCE REQUIREMENT (what we are looking for):
{requirement}

SEED CLIP EXCERPT (this is the original clip the user wants to anchor to — for tone reference only):
{seed_excerpt}

CANDIDATE METADATA:
  Platform: {platform}
  Title: {title}
  Uploader / channel: {uploader}
  URL: {url}

CANDIDATE TRANSCRIPT:
\"\"\"
{transcript}
\"\"\"

RULES — FOLLOW EXACTLY:

1. SAME PERSON DETERMINATION (transcript+metadata only — no external knowledge):
   - same_person=true ONLY when the evidence quote is spoken by PERSON OF INTEREST,
     or the transcript contains a clearly embedded direct quote from them.
   - Title/channel/name mentions are discovery signals, NOT enough by themselves
     for a verified clip when the quote is third-party narration or criticism.
   - News narration, criticism, commentary, or "X was denounced/reported/accused"
     ABOUT the person is a context source, not a verified same-person clip.
   - If the transcript says "he/they want..." or a journalist/critic is describing
     the person rather than the person speaking, mark same_person=false.
   - If only inferential or speaker attribution is unclear -> mark same_person=false.
     Do NOT guess.

2. REQUIREMENT MATCH:
   - Quote MUST come from the transcript verbatim (or near-verbatim with minor cleanup).
   - Quote MUST be the public person's own statement, not a journalist's or
     critic's summary about them.
   - If you cannot find a quote in the transcript that matches the requirement -> requirement_match=false, best_quote="", safe_to_use=false.

3. CLAIM TYPE (pick ONE — these are the ONLY allowed values):
   - "group-targeting"        : statement specifically directed at an identifiable group.
   - "dehumanizing"           : language that strips humanity from a group/person.
   - "unfair-generalization"  : sweeping claim about a group not supported by evidence.
   - "moral-contradiction"    : statement has transcript-grounded tension with the
                                person's public moral/religious persona. The word
                                "contrário" or standard doctrine is NOT enough.
   - "hypocrisy"              : statement contradicts a past public position by the
                                same person, visible in the transcript or seed context.
   - "needs-context"          : ambiguous — meaning depends on context not visible in transcript.

   You MAY NOT use the label "hate speech" or any other label. If unsure -> "needs-context".

4. SAFETY:
   - safe_to_use=true ONLY when: same_person=true AND requirement_match=true AND quote is unambiguous AND context_needed is non-blocking.
   - safe_to_use=false for third-party commentary/news clips about the person.
   - safe_to_use=false when moral-contradiction/hypocrisy lacks the prior-vs-current
     or persona-vs-statement evidence needed to explain the contradiction.
   - When in doubt -> safe_to_use=false and explain in "why".

5. TIMESTAMPS:
   - If the transcript contains time markers, use them. Format MM:SS.
   - If no markers, write best-effort estimates as MM:SS based on word position. If unknown, write "00:00" for both.

6. OUTPUT: STRICT JSON ONLY. No prose. No code fences. Schema:

{{
  "same_person": <bool>,
  "person_confidence": <float 0.0-1.0>,
  "same_person_method": "metadata|transcript|title|channel|user_passed|face_match",
  "requirement_match": <bool>,
  "match_score": <float 0.0-1.0>,
  "claim_type": "group-targeting|dehumanizing|unfair-generalization|moral-contradiction|hypocrisy|needs-context",
  "best_quote": "<verbatim from transcript or empty>",
  "timestamp_start": "MM:SS",
  "timestamp_end": "MM:SS",
  "targeted_group": "<group name or null>",
  "context_needed": "<what additional context would a reviewer need, if any>",
  "safe_to_use": <bool>,
  "why": "<one-sentence transcript-grounded justification>"
}}"""


# ── core scorer ──────────────────────────────────────────────────────────────

def _empty_score(reason: str) -> dict:
    return {
        "same_person": False,
        "person_confidence": 0.0,
        "same_person_method": "metadata",
        "requirement_match": False,
        "match_score": 0.0,
        "claim_type": "needs-context",
        "best_quote": "",
        "timestamp_start": "00:00",
        "timestamp_end": "00:00",
        "targeted_group": None,
        "context_needed": reason,
        "safe_to_use": False,
        "why": reason,
    }


def score_candidate(candidate: dict, transcript: str, person_name: str,
                    requirement: str, seed_excerpt: str = "",
                    person_passed_by_user: bool = False,
                    on_failure=None) -> dict:
    """Score a single candidate. Returns LOCKED schema dict.
    NEVER raises — failures return safe-default empty score with reason in 'why'.

    Routes through llm_router (Anthropic → OpenAI cascade per fallback_mode).
    """
    if not transcript or not transcript.strip():
        return _empty_score("no_transcript")

    # Truncate transcript to keep token cost bounded
    trimmed = transcript[:12000]
    prompt = _RUBRIC_PROMPT.format(
        person_name=person_name,
        requirement=requirement[:1500],
        seed_excerpt=(seed_excerpt or "(none)")[:1000],
        platform=candidate.get("platform", ""),
        title=candidate.get("title", "")[:300],
        uploader=candidate.get("uploader", "")[:200],
        url=candidate.get("url", ""),
        transcript=trimmed,
    )
    try:
        from llm_router import llm_json
        data = llm_json(prompt, max_tokens=1200, on_failure=on_failure)
    except Exception as e:
        # strict mode raises — return empty, runner records the route failure
        return _empty_score(f"score_failed: {str(e)[:200]}")
    if not isinstance(data, dict) or not data:
        return _empty_score("score_failed: no_llm_available")

    # User-passed person flag overrides ambiguous same_person inference
    if person_passed_by_user and not data.get("same_person"):
        # Promote only when title/uploader corroborates
        title_low = (candidate.get("title", "") + " " + candidate.get("uploader", "")).lower()
        if person_name.lower() in title_low:
            data["same_person"] = True
            data["same_person_method"] = "user_passed"
            data["person_confidence"] = max(data.get("person_confidence", 0.0), 0.7)

    # Validate + clamp
    score = _coerce_to_schema(data)
    return score


def _coerce_to_schema(data: dict) -> dict:
    """Force fields into LOCKED schema. Never trust LLM output blindly."""
    out = _empty_score("validated")
    out["same_person"] = bool(data.get("same_person", False))
    out["person_confidence"] = _clamp_float(data.get("person_confidence"))

    # Track context flags (joined into context_needed at the end).
    extra_context: list[str] = []

    # same_person_method: validate AND enforce Phase 1 face_match downgrade.
    raw_method = str(data.get("same_person_method", "metadata"))
    if raw_method not in ALLOWED_SAME_PERSON_METHODS:
        method = "metadata"
    elif raw_method == "face_match":
        # Phase 1 has no visual face verification. Haiku may hallucinate this
        # method — treat as not-yet-verified, downgrade to metadata, cap conf.
        method = "metadata"
        out["person_confidence"] = min(out["person_confidence"], 0.6)
        extra_context.append("face_match_not_run_phase1")
    else:
        method = raw_method
    out["same_person_method"] = method

    out["requirement_match"] = bool(data.get("requirement_match", False))
    out["match_score"] = _clamp_float(data.get("match_score"))

    # claim_type: enforce whitelist. Forbidden labels (incl. "hate-speech",
    # "hate speech", "racist", etc.) must NEVER pass through as approved
    # evidence — relabel to needs-context AND mark unsafe so reviewers see
    # this clip flagged for human judgement.
    raw_claim = str(data.get("claim_type", "needs-context")).strip()
    invalid_claim_relabeled = False
    if raw_claim in ALLOWED_CLAIM_TYPES:
        out["claim_type"] = raw_claim
    else:
        out["claim_type"] = "needs-context"
        invalid_claim_relabeled = True
        extra_context.append("invalid_claim_type_relabeled")
        # Preserve the original (truncated) label internally for reviewers.
        out["_rejected_claim_type"] = raw_claim[:80]

    # B9: pre-strip best_quote so " " whitespace doesn't survive truncation.
    out["best_quote"] = str(data.get("best_quote", "")).strip()[:1000]
    out["timestamp_start"] = _coerce_mmss(data.get("timestamp_start"))
    out["timestamp_end"] = _coerce_mmss(data.get("timestamp_end"))
    tg = data.get("targeted_group")
    out["targeted_group"] = str(tg)[:120] if tg else None
    base_ctx = str(data.get("context_needed", "")).strip()
    if extra_context:
        joined = " | ".join(extra_context)
        out["context_needed"] = (f"{base_ctx} | {joined}".strip(" |"))[:600]
    else:
        out["context_needed"] = base_ctx[:600]
    out["safe_to_use"] = bool(data.get("safe_to_use", False))
    out["why"] = str(data.get("why", ""))[:600]

    # Self-consistency: no quote -> not safe
    if not out["best_quote"].strip():
        out["safe_to_use"] = False
        out["requirement_match"] = False
    # Not same person -> not safe
    if not out["same_person"]:
        out["safe_to_use"] = False
    # Invalid claim_type was relabeled -> not safe (reviewer must see it)
    if invalid_claim_relabeled:
        out["safe_to_use"] = False
    _apply_speaker_and_claim_guardrails(out)
    return out


def _apply_speaker_and_claim_guardrails(out: dict) -> None:
    """Reject credibility-risky scorer outputs after LLM coercion.

    This pass intentionally errs toward review/rejection. A news or commentary
    clip can still be a research lead, but it must not count as a verified clip
    of the subject person making the evidence statement.
    """
    quote = out.get("best_quote", "") or ""
    why = out.get("why", "") or ""
    context = out.get("context_needed", "") or ""
    combined = " ".join([quote, why, context]).lower()

    if _looks_like_third_party_narration(combined):
        out["same_person"] = False
        out["requirement_match"] = False
        out["safe_to_use"] = False
        out["person_confidence"] = min(out.get("person_confidence", 0.0), 0.4)
        out["match_score"] = min(out.get("match_score", 0.0), 0.4)
        out["claim_type"] = "needs-context"
        out["context_needed"] = _append_context(
            out.get("context_needed", ""),
            "third_party_or_news_narration_not_verified_speaker",
        )
        out["why"] = (
            "Clip appears to discuss or report on the person rather than "
            "capture the person making the evidence statement."
        )
        return

    if out.get("claim_type") in {"moral-contradiction", "hypocrisy"}:
        if not _has_contradiction_support(combined):
            out["requirement_match"] = False
            out["safe_to_use"] = False
            out["match_score"] = min(out.get("match_score", 0.0), 0.5)
            out["context_needed"] = _append_context(
                out.get("context_needed", ""),
                "unsupported_contradiction_requires_prior_or_persona_context",
            )
            if not out.get("why"):
                out["why"] = (
                    "Transcript does not show enough prior-vs-current or "
                    "persona-vs-statement evidence for this contradiction label."
                )


def _looks_like_third_party_narration(text: str) -> bool:
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in THIRD_PARTY_NARRATION_PATTERNS
    )


def _has_contradiction_support(text: str) -> bool:
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in CONTRADICTION_SUPPORT_PATTERNS
    )


def _append_context(existing: str, marker: str) -> str:
    existing = (existing or "").strip()
    if marker in existing:
        return existing[:600]
    return (f"{existing} | {marker}".strip(" |"))[:600]


def _clamp_float(v) -> float:
    try:
        f = float(v)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, f))


def _coerce_mmss(v) -> str:
    s = str(v or "00:00").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        return s if len(s) == 5 else "0" + s
    if re.fullmatch(r"\d+", s):
        secs = int(s)
        return f"{secs//60:02d}:{secs%60:02d}"
    return "00:00"


# ── validation + gates ───────────────────────────────────────────────────────

def validate_score(score: dict) -> tuple[bool, list[str]]:
    """Return (ok, reasons). ok=True means score passes minimum quality bar
    to be considered for verified_clips. Doesn't mean safe_to_use=True."""
    reasons = []
    if not isinstance(score, dict):
        return False, ["not_a_dict"]
    if score.get("claim_type") not in ALLOWED_CLAIM_TYPES:
        reasons.append("invalid_claim_type")
    if not score.get("best_quote", "").strip():
        reasons.append("no_quote")
    if not score.get("same_person"):
        reasons.append("not_same_person")
    if score.get("person_confidence", 0) < 0.6:
        reasons.append("person_confidence_low")
    if not score.get("requirement_match"):
        reasons.append("requirement_not_matched")
    return (len(reasons) == 0), reasons


def apply_build_gates(verified: list[dict], rejected: list[dict],
                      target_count: int = 6) -> dict:
    """Determine if manifest is ready for manifest review / render trigger."""
    requires_approval = any(
        v.get("score", {}).get("claim_type") in SENSITIVE_CLAIM_TYPES
        for v in verified
    )
    reason_parts = []
    if len(verified) < 3:
        reason_parts.append(f"verified_count {len(verified)} < 3 minimum")
    if requires_approval:
        reason_parts.append("sensitive claim_types present — manual approval required")
    reason_parts.append("Phase 1 manifest-only — render gate is manual")
    return {
        "verified_count": len(verified),
        "rejected_count": len(rejected),
        "target_count": target_count,
        "ready_for_render": len(verified) >= 3,
        "requires_manual_approval": True,
        "reason": " | ".join(reason_parts),
    }


def _manifest_status(candidates_collected: int, candidates_transcribed: int,
                     verified_count: int) -> tuple[str, str, str]:
    if candidates_transcribed < 3:
        return (
            "Needs Research — Transcription Blocked",
            "media retrieval/transcription",
            "Check transcript route failures, provider actor media fields, or paste manual candidate URLs.",
        )
    if verified_count < 3:
        return (
            "Needs Research — Evidence Weak",
            "evidence mismatch",
            "Use broader or more targeted candidate URLs; current transcripts did not satisfy same-person/requirement rubric.",
        )
    return (
        "Ready for Manifest Review",
        "",
        "Review manifest quotes, then trigger carousel or Remotion render if acceptable.",
    )


def _failure_summary(rejected: list[dict]) -> dict:
    counts = Counter()
    for item in rejected or []:
        trace = item.get("error_trace") or {}
        if trace.get("stage"):
            key = str(trace.get("stage"))[:80]
        else:
            key = str(item.get("reason") or "rejected")[:80]
        counts[key] += 1
    return dict(counts.most_common(10))


def _candidate_count_by_route(verified: list[dict], rejected: list[dict]) -> dict:
    counts = Counter()
    for item in (verified or []) + (rejected or []):
        cand = item.get("candidate") or {}
        route = cand.get("route") or cand.get("query") or "unknown"
        counts[str(route)[:80]] += 1
    return dict(counts.most_common())


# ── manifest writer ──────────────────────────────────────────────────────────

def build_manifest(seed_url: str, person_name: str, person_confidence: float,
                   person_method: str, requirement: str, niche: str,
                   queries: dict, candidates_collected: int,
                   candidates_transcribed: int, verified: list[dict],
                   rejected: list[dict], seed_excerpt: str = "",
                   run_id: str = "", target_count: int = 6) -> dict:
    """Assemble evidence_manifest.json payload (does not write to disk)."""
    status, blocker, next_action = _manifest_status(
        candidates_collected, candidates_transcribed, len(verified),
    )
    return {
        "mode": "person_evidence_mining",
        "schema_version": 1,
        "run_id": run_id or os.environ.get("GITHUB_RUN_ID", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "ready_for_render": len(verified) >= 3,
        "seed": {
            "url": seed_url,
            "role": "intro_hook",
            "transcript_excerpt": (seed_excerpt or "")[:1500],
        },
        "person": {
            "name": person_name,
            "confidence": _clamp_float(person_confidence),
            "method": person_method if person_method in ALLOWED_SAME_PERSON_METHODS
                      else "user_passed",
        },
        "requirement": requirement,
        "niche": niche,
        "queries_used": queries,
        "candidates_collected": candidates_collected,
        "candidates_transcribed": candidates_transcribed,
        "verified_clips": verified,
        "rejected_candidates": rejected,
        "diagnostics": {
            "candidate_count": candidates_collected,
            "transcribed_count": candidates_transcribed,
            "verified_count": len(verified),
            "candidate_count_by_route": _candidate_count_by_route(verified, rejected),
            "transcription_failure_summary": _failure_summary(rejected),
            "primary_blocker": blocker,
            "recommended_next_action": next_action,
        },
        "build_gates": apply_build_gates(verified, rejected, target_count),
    }


def write_manifest(path: str, payload: dict) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


# ── slug helpers (used by run orchestrator for folder naming) ────────────────

def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "unnamed"


def slugify_bounded(s: str, max_len: int = 30) -> str:
    """Collision-safe truncated slugify. If the full slug would exceed
    max_len, append a 6-char SHA1 hash of the original input so two
    requirements that differ past char `max_len` produce DIFFERENT folder
    names. Use this when the slug becomes part of a Drive path.
    """
    import hashlib
    full = slugify(s)
    if len(full) <= max_len:
        return full
    suffix = hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()[:6]
    base_max = max_len - len(suffix) - 1  # reserve room for "-<hash>"
    if base_max < 4:
        base_max = 4
    base = full[:base_max].rstrip("-") or "unnamed"
    return f"{base}-{suffix}"
