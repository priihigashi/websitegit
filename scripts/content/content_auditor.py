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

HAIKU_MODEL = "claude-haiku-4-5-20251001"
API_URL     = "https://api.anthropic.com/v1/messages"


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

STRUCTURE_FORMAT_PROMPT = """You are a social media format expert. Review for: slide count appropriate (4-8 for carousel), hook in first slide, CTA in last slide, each slide has ONE main point, text not too long per slide, image described or present for alternating slides. Give a score 1-10 and PASS/FAIL verdict.

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

AGENTS = [
    {"name": "Fact Checker",          "system": FACT_CHECKER_PROMPT},
    {"name": "Brand & Tone Reviewer", "system": BRAND_TONE_PROMPT},
    {"name": "Structure & Format",    "system": STRUCTURE_FORMAT_PROMPT},
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


def _extract_slides_from_html(html: str) -> list[str]:
    """Extract text content from each .slide element.
    Returns list of stripped text strings, one per slide found.
    """
    # Match <div|section|article class="...slide..."> ... </div|section|article>
    # Use non-greedy with DOTALL; cap nesting by looking for first closing tag at same depth.
    # Simple approach: find all elements whose class contains 'slide'.
    slide_blocks = re.findall(
        r'<(?:div|section|article)[^>]*class=["\'][^"\']*\bslide\b[^"\']*["\'][^>]*>'
        r'(.*?)'
        r'</(?:div|section|article)>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if not slide_blocks:
        # Fallback: look for data-slide or id matching slide-N patterns
        slide_blocks = re.findall(
            r'<(?:div|section)[^>]*(?:data-slide|id=["\']slide)[^>]*>(.*?)</(?:div|section)>',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

    texts = []
    for block in slide_blocks[:10]:  # cap at 10 slides to keep brief compact
        t = _strip_tags(block).strip()
        if len(t) > 20:  # skip near-empty blocks
            texts.append(t[:400])
    return texts


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

def call_haiku(system_prompt: str, user_content: str, agent_name: str) -> dict:
    """Call Claude Haiku via Anthropic Messages API.
    Returns: {agent, score, verdict, full_response, error}
    """
    if not ANTHROPIC_KEY:
        return {
            "agent": agent_name, "score": None, "verdict": "SKIP",
            "full_response": "CLAUDE_KEY_4_CONTENT not set — skipping agent",
            "error": "no key",
        }

    payload = json.dumps({
        "model": HAIKU_MODEL,
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
        return {
            "agent": agent_name, "score": None, "verdict": "ERROR",
            "full_response": f"HTTP {e.code}: {err_body}", "error": str(e),
        }
    except Exception as e:
        return {
            "agent": agent_name, "score": None, "verdict": "ERROR",
            "full_response": str(e), "error": str(e),
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
    for agent in AGENTS:
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

    print(f"  Auditing {len(results)} post(s)  ×  {len(AGENTS)} agents each...\n")

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
                        {"name": ar["agent"], "verdict": ar["verdict"], "score": ar["score"]}
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
