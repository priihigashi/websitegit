#!/usr/bin/env python3
"""
content_auditor.py — 3-agent AI quality audit for carousel/video content.
Runs after carousel_reviewer.py in content_creator.yml.

3 separate Claude Haiku calls, each with a distinct expert lens:
  Agent 1 — Fact Checker: verifies claims, flags misleading stats
  Agent 2 — Brand & Tone Reviewer: checks voice, PT-BR quality, hook strength
  Agent 3 — Structure & Format Reviewer: slide count, CTA, one-point-per-slide rule

If ANY agent gives FAIL → sends alert email with all 3 reports.
If all PASS → appends audit_result to results.json and exits cleanly.
Always exits 0 (non-blocking — reviewer is informational).

Usage:
  python content_auditor.py           ← reads CONTENT_CREATOR_RUN env var (JSON list)
  python content_auditor.py --dry-run ← print checks without emailing
"""

import json, os, re, subprocess, sys, time
import urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

ANTHROPIC_KEY    = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
ALERT_EMAIL      = os.environ.get("ALERT_EMAIL", "priscila@oakpark-construction.com")
RUN_RESULTS_JSON = os.environ.get("CONTENT_CREATOR_RUN", "[]")
WORK_DIR         = Path(os.environ.get("WORK_DIR", "/tmp/content_creator_run"))
DRY_RUN          = "--dry-run" in sys.argv

# SH-OPC-SMART-SLIDE-PICKER Phase 10: Sonnet primary for content audit.
# Haiku consistently produced low scores on schema-strict OPC content + missed
# label-leak issues that Sonnet catches. Audit cost is small (3 calls per
# carousel) so the upgrade is justified. HAIKU_MODEL kept as alias for any
# backward-compat callers.
SONNET_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL  = SONNET_MODEL  # legacy alias — points to Sonnet now
AUDIT_MODEL  = SONNET_MODEL
API_URL      = "https://api.anthropic.com/v1/messages"


# ─── System prompts (each agent has a distinct expert lens) ───────────────────

FACT_CHECKER_PROMPT = """You are a strict fact-checker. Review this content for: factual accuracy, unsupported claims, misleading statistics, missing context. Rate each claim: VERIFIED / UNVERIFIED / MISLEADING. Give an overall score 1-10 and a PASS/FAIL verdict.

Format your response EXACTLY as:
CLAIMS REVIEW:
- [claim]: [VERIFIED/UNVERIFIED/MISLEADING] — [brief reason]

OVERALL SCORE: [1-10]
VERDICT: [PASS/FAIL]
NOTES: [1-2 sentences max]"""

BRAND_TONE_PROMPT = """You are a brand voice auditor for a Brazilian political news and OPC (Oak Park Construction) content operation. Review for: correct tone (serious/authoritative for news, professional/warm for OPC), PT-BR language quality (if applicable), message clarity, hook strength (first line must stop the scroll). Give a score 1-10 and PASS/FAIL verdict.

Format your response EXACTLY as:
TONE: [assessment]
PT-BR QUALITY: [assessment or N/A if English only]
HOOK STRENGTH: [assessment of first slide/line]
MESSAGE CLARITY: [assessment]

OVERALL SCORE: [1-10]
VERDICT: [PASS/FAIL]
NOTES: [1-2 sentences max]"""

STRUCTURE_FORMAT_PROMPT = """You are a social media format expert. Review the RENDERED carousel brief, not raw JSON fields.

Review for: slide count appropriate (4-8 for carousel), hook in first slide, CTA in last slide, each slide has a clear purpose, text not too long per slide, image described or present for alternating slides. Give a score 1-10 and PASS/FAIL verdict.

Important template-aware rules:
- OPC smart-picker carousels may mix template types. A material/profile grid, four-card comparison grid, and sources/CTA slide are intentionally structured with multiple short micro-points. Do NOT fail those slides for "one point per slide" when the slide has one clear overall purpose.
- Treat rendered image markers such as [VISUALS: ...] as evidence that visuals are present. Do NOT say visuals are missing when the brief lists images/backgrounds/videos.
- A CTA may appear as a final-slide save/action phrase, a source-slide action, or a rendered footer CTA.

Format your response EXACTLY as:
SLIDE COUNT: [count] — [OK/TOO FEW/TOO MANY]
HOOK (slide 1): [PRESENT/MISSING] — [brief assessment]
CTA (last slide): [PRESENT/MISSING] — [brief assessment]
ONE POINT PER SLIDE: [YES/NO — if no, which slides violate]
TEXT LENGTH: [OK/TOO LONG — if too long, which slides]
VISUAL ALTERNATION: [OK/MISSING — every-other-slide visual rule]

OVERALL SCORE: [1-10]
VERDICT: [PASS/FAIL]
NOTES: [1-2 sentences max]"""

_BASE_AGENTS = [
    {"name": "Fact Checker",          "system": FACT_CHECKER_PROMPT},
    {"name": "Brand & Tone Reviewer", "system": BRAND_TONE_PROMPT},
    {"name": "Structure & Format",    "system": STRUCTURE_FORMAT_PROMPT},
]

# Compact niche copy rules injected into each agent's system prompt so they
# know what standard to apply rather than inventing a generic rubric.
_NICHE_CONTEXT = {
    "opc": (
        "NICHE RULES (Oak Park Construction — OPC):\n"
        "- Language: English. Tone: direct, educational, no hype.\n"
        "- NEVER promise what OPC does for clients. Use ranges for stats, not exact averages.\n"
        "- Every stat needs a qualified source (Houzz, NAHB, Remodeling Magazine).\n"
        "- No exclamation marks in slides. Caption max 3 sentences before hashtags.\n"
        "- PASS threshold: factual, sourced, educational, no service promises.\n"
    ),
    "brazil": (
        "NICHE RULES (Brazil News — PT-BR):\n"
        "- Language: Brazilian Portuguese (informal, not slangy).\n"
        "- Political content must be FACTUAL — no opinion, no accusation.\n"
        "- Always include party affiliation: 'Fulano (PT-RJ)'.\n"
        "- Every factual claim needs 2+ sources from different outlets.\n"
        "- Hook slide = big claim/number only. Skepticism lives in middle slides, not the hook.\n"
        "- Caption hashtags: NO party names (#PT, #PL, #Bolsonaro). Topic hashtags only.\n"
        "- PASS threshold: factual, bilingual, sourced, no party hashtags, hook before skepticism.\n"
    ),
    "usa": (
        "NICHE RULES (USA News — English):\n"
        "- Language: English. Journalistic tone: factual, no editorial.\n"
        "- Every factual claim needs 2+ sources (AP, Reuters, NYT, official records).\n"
        "- No partisan framing. Present both sides when relevant.\n"
        "- Hook slide = big claim/number only.\n"
        "- PASS threshold: factual, sourced, neutral, hook-first structure.\n"
    ),
}


def _agents_for_niche(niche: str) -> list:
    """Return AGENTS list with niche-specific context prepended to each system prompt."""
    ctx = _NICHE_CONTEXT.get((niche or "").lower(), "")
    if not ctx:
        return _BASE_AGENTS
    return [
        {"name": a["name"], "system": ctx + "\n" + a["system"]}
        for a in _BASE_AGENTS
    ]


# ─── HTML text extraction ─────────────────────────────────────────────────────

def _strip_tags(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _visual_summary_for_slide(block: str) -> str:
    """Summarize rendered visual evidence for the audit LLM.

    The structure auditor only receives text, so without this marker it can
    falsely claim that a rendered smart-template slide has no visuals.
    """
    img_count = len(re.findall(r'<img\b', block, flags=re.IGNORECASE))
    video_count = len(re.findall(r'<video\b', block, flags=re.IGNORECASE))
    bg_count = len(re.findall(
        r'background-image\s*:\s*url\((?!["\']?\s*["\']?\))',
        block,
        flags=re.IGNORECASE,
    ))
    placeholder_count = len(re.findall(
        r'context-img-placeholder|ctx-placeholder',
        block,
        flags=re.IGNORECASE,
    ))
    bits = []
    if img_count:
        bits.append(f"{img_count} img")
    if bg_count:
        bits.append(f"{bg_count} bg")
    if video_count:
        bits.append(f"{video_count} video")
    if placeholder_count:
        bits.append(f"{placeholder_count} placeholder")
    return ", ".join(bits) if bits else "none"


def _first_variant_html(html: str) -> str:
    """The carousel builder stores two visual variants in one cover.html file.

    Audit only the first variant; otherwise the same 5-slide carousel is read
    as a fake 10-slide repeated story and the Structure agent fails it.
    """
    marker = "<!-- V3 -->"
    return html.split(marker, 1)[0] if marker in html else html


def _extract_slides_from_html(html: str) -> list[str]:
    """Extract text content from each .slide element.

    Uses a split-by-opening-tag approach instead of balanced-tag regex so nested
    divs inside each slide are fully captured rather than truncated at the first
    inner closing tag.
    """
    html = _first_variant_html(html)

    # Find positions of top-level slide opening tags. Match the CSS class
    # token exactly so nested helpers like "slide-logo" are not counted as
    # fake slides.
    tag_re = re.compile(
        r'<(?:div|section|article)\b[^>]*class=["\']([^"\']*)["\'][^>]*>',
        flags=re.IGNORECASE,
    )
    positions = [
        m.start()
        for m in tag_re.finditer(html)
        if "slide" in (m.group(1) or "").split()
    ]

    if not positions:
        # Fallback: segment by data-slide or id=slide-N
        fallback_re = re.compile(
            r'<(?:div|section)[^>]*(?:data-slide|id=["\']slide)[^>]*>',
            flags=re.IGNORECASE,
        )
        positions = [m.start() for m in fallback_re.finditer(html)]

    if not positions:
        return []

    # Segment HTML between consecutive slide openings — captures full nested content
    positions.append(len(html))
    texts = []
    for i in range(min(len(positions) - 1, 8)):  # one carousel variant only
        block = html[positions[i]:positions[i + 1]]
        t = _strip_tags(block).strip()
        if len(t) > 20:
            visuals = _visual_summary_for_slide(block)
            texts.append(f"{t[:500]} [VISUALS: {visuals}]")
    return texts


def _slide_plan_summary(content: dict) -> list[str]:
    """Return compact smart-picker template roles for audit context."""
    plan = (content or {}).get("_slide_plan") or {}
    resolved = plan.get("_resolved_slides") or []
    planned = plan.get("slides") or []
    lines = []
    for idx, slide in enumerate(planned, start=1):
        resolved_item = resolved[idx - 1] if idx - 1 < len(resolved) else {}
        template_id = resolved_item.get("effective_id") or slide.get("template_id") or ""
        role = slide.get("role") or resolved_item.get("role") or ""
        kind = resolved_item.get("kind") or ""
        goal = slide.get("content_goal") or ""
        if template_id or role:
            lines.append(
                f"  Slide {idx}: role={role or 'unknown'}; "
                f"template={template_id or 'unknown'}; kind={kind or 'unknown'}; "
                f"goal={goal[:140]}"
            )
    return lines


def extract_content_brief(result: dict) -> str:
    """Build a structured content brief from result dict + local HTML file.
    Tries to extract slide text from cover.html if it still exists in WORK_DIR.
    Falls back gracefully to metadata-only brief.
    """
    topic          = result.get("topic", "(no topic)")
    niche          = result.get("niche", "unknown")
    post_id        = result.get("post_id", "unknown")
    mentioned      = result.get("mentioned_people", [])
    series_override = result.get("series_override", "")

    lines = [
        f"POST ID: {post_id}",
        f"NICHE: {niche.upper()}",
        f"TOPIC: {topic}",
    ]
    if series_override:
        lines.append(f"SERIES: {series_override}")
    if mentioned:
        lines.append(f"PEOPLE MENTIONED: {', '.join(mentioned)}")

    content = result.get("content") or {}
    plan_lines = _slide_plan_summary(content)
    if plan_lines:
        lines.append("\nSMART TEMPLATE PLAN:")
        lines.extend(plan_lines)

    # Locate cover.html — it lives at WORK_DIR/post_id/cover.html
    html_path = WORK_DIR / post_id / "cover.html"
    if not html_path.exists():
        candidates = list(WORK_DIR.glob(f"**/{post_id}/cover.html"))
        html_path = Path(candidates[0]) if candidates else None

    if html_path and html_path.exists():
        try:
            html = html_path.read_text(encoding="utf-8", errors="ignore")
            slides = _extract_slides_from_html(html)
            if slides:
                lines.append(f"\nSLIDE CONTENT ({len(slides)} slide(s) extracted):")
                for i, s in enumerate(slides, start=1):
                    lines.append(f"  Slide {i}: {s}")
            else:
                # Fallback: strip full body text
                body_m = re.search(r'<body[^>]*>(.*?)</body>', html, flags=re.DOTALL | re.IGNORECASE)
                body_text = _strip_tags(body_m.group(1) if body_m else html)
                lines.append(f"\nCONTENT EXTRACT:\n{body_text[:2000]}")
        except Exception as e:
            lines.append(f"\n(HTML read error: {e} — auditing from metadata only)")
    else:
        lines.append("\n(cover.html not in work dir — auditing from metadata only)")

    return "\n".join(lines)


# ─── Claude Haiku API call ────────────────────────────────────────────────────

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")


def _call_openai_chat(system_prompt: str, user_content: str) -> str:
    """Phase 11 — fallback when Anthropic 400/401/429/529 on credits/capacity.
    Returns text or raises."""
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    payload = json.dumps({
        "model": "gpt-4o",
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type":  "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def call_haiku(system_prompt: str, user_content: str, agent_name: str) -> dict:
    """Call Claude Sonnet (Phase 10) via Anthropic Messages API with OpenAI
    fallback on credits/capacity failure (Phase 11).
    Returns: {agent, score, verdict, full_response, error}
    """
    if not ANTHROPIC_KEY and not OPENAI_KEY:
        return {
            "agent": agent_name, "score": None, "verdict": "SKIP",
            "full_response": "Neither CLAUDE_KEY_4_CONTENT nor OPENAI_API_KEY set — skipping agent",
            "error": "no key",
        }

    text = ""
    used = "sonnet"
    if ANTHROPIC_KEY:
        payload = json.dumps({
            "model": SONNET_MODEL,
            "max_tokens": 600,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }).encode()

        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            text = (data.get("content") or [{}])[0].get("text", "")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="ignore")[:200]
            # Phase 11/C3 — fallback on credits/capacity (4xx) AND 5xx
            # server errors so a transient Anthropic outage doesn't blank
            # the audit row.
            if e.code in (400, 401, 402, 429, 500, 502, 503, 504, 529) and OPENAI_KEY:
                try:
                    text = _call_openai_chat(system_prompt, user_content)
                    used = "openai-fallback"
                except Exception as oe:
                    return {
                        "agent": agent_name, "score": None, "verdict": "ERROR",
                        "full_response": f"Anthropic HTTP {e.code}: {err_body} | OpenAI fallback failed: {oe}",
                        "error": str(oe),
                    }
            else:
                return {
                    "agent": agent_name, "score": None, "verdict": "ERROR",
                    "full_response": f"HTTP {e.code}: {err_body}", "error": str(e),
                }
        except Exception as e:
            if OPENAI_KEY:
                try:
                    text = _call_openai_chat(system_prompt, user_content)
                    used = "openai-fallback"
                except Exception as oe:
                    return {
                        "agent": agent_name, "score": None, "verdict": "ERROR",
                        "full_response": f"Anthropic err: {e} | OpenAI fallback err: {oe}",
                        "error": str(oe),
                    }
            else:
                return {
                    "agent": agent_name, "score": None, "verdict": "ERROR",
                    "full_response": str(e), "error": str(e),
                }
    else:
        # No Anthropic key but OpenAI is set — go directly to OpenAI.
        try:
            text = _call_openai_chat(system_prompt, user_content)
            used = "openai-only"
        except Exception as e:
            return {
                "agent": agent_name, "score": None, "verdict": "ERROR",
                "full_response": f"OpenAI error: {e}", "error": str(e),
            }

    # Parse score and verdict from response text
    score_m   = re.search(r'OVERALL SCORE:\s*(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    verdict_m = re.search(r'VERDICT:\s*(PASS|FAIL)', text, re.IGNORECASE)

    score   = float(score_m.group(1)) if score_m else None
    if verdict_m:
        verdict = verdict_m.group(1).upper()
    elif score is not None:
        verdict = "PASS" if score >= 6 else "FAIL"
    else:
        verdict = "FAIL"  # can't parse → assume fail

    return {
        "agent": agent_name,
        "score": score,
        "verdict": verdict,
        "full_response": text,
        "error": None,
        "model_used": used,
    }


# ─── Audit one post (3 sequential agent calls) ────────────────────────────────

def audit_post(result: dict) -> dict:
    """Run all 3 agents on a single post result dict.
    Returns audit summary: {post_id, topic, niche, passed, avg_score, agents, drive_link}
    """
    post_id = result.get("post_id", "unknown")
    topic   = result.get("topic", "")
    niche   = result.get("niche", "")

    print(f"  [{niche.upper()}] {topic[:50]}")

    brief = extract_content_brief(result)

    agent_results = []
    for agent in _agents_for_niche(niche):
        print(f"    → {agent['name']}...", end="", flush=True)
        r = call_haiku(agent["system"], brief, agent["name"])
        agent_results.append(r)
        icon      = "✅" if r["verdict"] == "PASS" else ("⚠️" if r["verdict"] == "SKIP" else "❌")
        score_str = f"{r['score']:.0f}/10" if r["score"] is not None else "?"
        print(f" {icon} {r['verdict']} ({score_str})")
        time.sleep(0.3)  # small pause between calls

    all_pass = all(r["verdict"] in ("PASS", "SKIP") for r in agent_results)
    scored   = [r["score"] for r in agent_results if r["score"] is not None]
    avg_score = round(sum(scored) / len(scored), 1) if scored else None

    return {
        "post_id":    post_id,
        "topic":      topic[:60],
        "niche":      niche,
        "passed":     all_pass,
        "avg_score":  avg_score,
        "agents":     agent_results,
        "drive_link": result.get("version_link") or result.get("static_link", ""),
    }


# ─── Email report ─────────────────────────────────────────────────────────────

def send_audit_email(failed_posts: list, all_audits: list):
    """Send audit report via send_email.yml workflow (same pattern as carousel_reviewer)."""
    now    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    n_fail = len(failed_posts)
    n_pass = len(all_audits) - n_fail

    subject = f"[content-audit] {n_pass}/{len(all_audits)} PASS — {n_fail} FAIL — {now}"

    lines = [
        f"AI CONTENT AUDIT REPORT — {now}",
        f"Total: {len(all_audits)} | Passed: {n_pass} | Failed: {n_fail}",
        f"Agents: Fact Checker · Brand & Tone · Structure & Format",
        "",
    ]

    for a in all_audits:
        status    = "✅ ALL PASS" if a["passed"] else "❌ FAIL"
        score_str = f"avg {a['avg_score']}/10" if a["avg_score"] is not None else "no score"
        lines.append(f"{status}  [{a['niche'].upper()}] {a['topic']} ({score_str})")
        lines.append(f"       Drive: {a['drive_link']}")

        for ar in a["agents"]:
            icon = "✅" if ar["verdict"] == "PASS" else ("⚠️" if ar["verdict"] == "SKIP" else "❌")
            sc   = f"{ar['score']:.0f}/10" if ar["score"] is not None else "?"
            lines.append(f"       {icon} {ar['agent']}: {ar['verdict']} ({sc})")
            # Include full response text only for non-passing agents
            if ar["verdict"] not in ("PASS", "SKIP") and ar.get("full_response"):
                for resp_line in ar["full_response"].splitlines()[:20]:
                    lines.append(f"          {resp_line}")
        lines.append("")

    lines += [
        "─" * 60,
        "Workflow: https://github.com/priihigashi/oak-park-ai-hub/actions/workflows/content_creator.yml",
    ]

    body = "\n".join(lines)

    if DRY_RUN:
        print("\n[DRY RUN] Would send email:")
        print(f"Subject: {subject}")
        print(body)
        return

    try:
        subprocess.run(
            [
                "gh", "workflow", "run", "send_email.yml",
                "--repo", "priihigashi/oak-park-ai-hub",
                "-f", f"to={ALERT_EMAIL}",
                "-f", f"subject={subject}",
                "-f", f"body={body}",
            ],
            check=False, timeout=30,
        )
        print(f"  Audit report emailed to {ALERT_EMAIL}")
    except Exception as e:
        print(f"  Audit email failed (non-fatal): {e}")
        print(body)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n[content-auditor] Starting 3-agent AI content audit...")

    # Read results from env var — same pattern as carousel_reviewer.py
    try:
        results = json.loads(RUN_RESULTS_JSON.strip()) if RUN_RESULTS_JSON.strip() else []
    except json.JSONDecodeError:
        results = []

    if not results:
        print("  No results to audit (CONTENT_CREATOR_RUN not set or empty) — exiting")
        return

    if not ANTHROPIC_KEY:
        print("  WARNING: CLAUDE_KEY_4_CONTENT not set — all agents will be SKIPPED")

    first_niche = results[0].get("niche", "") if results else ""
    agent_count = len(_agents_for_niche(first_niche))
    print(f"  Auditing {len(results)} post(s)  ×  {agent_count} agents each...\n")

    all_audits = [audit_post(r) for r in results]

    passed = [a for a in all_audits if a["passed"]]
    failed = [a for a in all_audits if not a["passed"]]

    print(f"\n  Summary: {len(passed)}/{len(all_audits)} passed all 3 agents")

    # Append audit_result to each entry in results.json (alongside technical review data)
    results_file = WORK_DIR / "results.json"
    try:
        audit_map = {a["post_id"]: a for a in all_audits}
        for r in results:
            pid = r.get("post_id", "")
            if pid in audit_map:
                a = audit_map[pid]
                r["audit_result"] = {
                    "passed":    a["passed"],
                    "avg_score": a["avg_score"],
                    "agents": [
                        {
                            "name": ar["agent"],
                            "verdict": ar["verdict"],
                            "score": ar["score"],
                            "full_response": ar.get("full_response", ""),
                        }
                        for ar in a["agents"]
                    ],
                }
        results_file.write_text(json.dumps(results, default=str))
        print("  audit_result appended to results.json")
    except Exception as e:
        print(f"  Could not update results.json (non-fatal): {e}")

    # Always send report (confirms audit ran; full detail included for any FAILs)
    send_audit_email(failed, all_audits)

    print("[content-auditor] Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\n🔴 content-auditor uncaught: {e}\n{traceback.format_exc()[-1500:]}")
    sys.exit(0)  # Always exit 0 — non-blocking
