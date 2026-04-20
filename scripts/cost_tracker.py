#!/usr/bin/env python3
"""
cost_tracker.py
===============
Weekly AI cost aggregator. Pulls usage data from:
  - Anthropic: estimated from Content Creation Log runs (no public cost API)
  - OpenAI: /v1/usage?date=YYYY-MM-DD (daily usage data)
  - Apify: /v2/users/me (monthly usage stats)
  - GitHub Actions: /repos/{owner}/{repo}/actions/billing/usage

Writes to Cost Tracker spreadsheet (COST_TRACKER_SHEET_ID env var or hardcoded).
Tabs: Dashboard | Claude | OpenAI | Apify | GitHub Actions | Manual

Env vars:
  SHEETS_TOKEN          — Google OAuth refresh token JSON
  OPENAI_API_KEY        — OpenAI API key
  APIFY_API_KEY         — Apify API key
  GH_TOKEN              — GitHub token with repo billing scope
  COST_TRACKER_SHEET_ID — override sheet ID (optional)
  CLAUDE_KEY_4_CONTENT     — not used for billing (no public endpoint); cost estimated from logs

Anthropic model pricing (per 1M tokens, as of April 2026):
  claude-haiku-4-5-20251001:  input $0.80 / output $4.00
  claude-sonnet-4-6:          input $3.00 / output $15.00
  claude-opus-4-6:            input $15.00 / output $75.00
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta, timezone

COST_TRACKER_SHEET_ID = os.environ.get(
    "COST_TRACKER_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
)
IDEAS_INBOX_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CONTENT_LOG_TAB = "📊 Content Creation Log"
COST_TAB   = "💰 Cost Tracker"
SHEETS_TOKEN = os.environ.get("SHEETS_TOKEN", "")
OPENAI_KEY   = os.environ.get("OPENAI_API_KEY", "")
APIFY_KEY    = os.environ.get("APIFY_API_KEY", "")
GH_TOKEN     = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
GH_REPO      = os.environ.get("GITHUB_REPOSITORY", "priihigashi/oak-park-ai-hub")

# Model cost per 1M tokens (input, output) USD
ANTHROPIC_PRICING = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-haiku-4-5":          (0.80, 4.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "claude-opus-4-6":           (15.00, 75.00),
    "claude-opus-4-7":           (15.00, 75.00),
}
ANTHROPIC_DEFAULT_PRICE = (3.00, 15.00)  # assume Sonnet if unknown

# Rough token estimates per pipeline run (input+output, in thousands)
PIPELINE_TOKEN_ESTIMATES = {
    "capture_pipeline":   (15, 3),   # ~15K input + 3K output per run
    "capture_queue":      (15, 3),
    "content_creator":    (8, 2),    # Haiku-driven
    "youtube_research":   (20, 4),
    "photo_catalog":      (2, 1),    # Haiku + vision, per photo
    "default":            (5, 1),
}


# ─── AUTH ─────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    if not SHEETS_TOKEN:
        sys.exit("ERROR: SHEETS_TOKEN not set")
    td = json.loads(SHEETS_TOKEN)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    ).read())
    return resp["access_token"]


# ─── SHEETS ───────────────────────────────────────────────────────────────────

def _sheets_get(token: str, sheet_id: str, range_: str) -> list:
    enc = urllib.parse.quote(range_, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    ).read())
    return resp.get("values", [])


def _sheets_append(token: str, sheet_id: str, tab: str, rows: list):
    enc = urllib.parse.quote(f"'{tab}'!A:Z", safe="!:'")
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
           f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    body = json.dumps({"values": rows}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req).read()


def _ensure_tab(token: str, sheet_id: str, tab_name: str, header: list):
    meta = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}?fields=sheets.properties",
            headers={"Authorization": f"Bearer {token}"}
        )
    ).read())
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if tab_name not in existing:
        body = json.dumps({"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}:batchUpdate",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )).read()
        _sheets_append(token, sheet_id, tab_name, [header])
        print(f"  Created tab: {tab_name}")


# ─── ANTHROPIC (estimated from Content Creation Log) ──────────────────────────

def _fetch_anthropic_estimate(token: str, since_date: str) -> list:
    """
    Reads Content Creation Log and estimates Claude API cost from run counts.
    Returns list of row dicts: {date, pipeline, runs, est_input_k, est_output_k, est_cost_usd}
    """
    rows = _sheets_get(token, IDEAS_INBOX_ID, f"'{CONTENT_LOG_TAB}'!A:M")
    if len(rows) < 2:
        return []

    header = rows[0]
    col = {h: i for i, h in enumerate(header)}
    date_col = col.get("DATE", 0)
    pipe_col = col.get("PIPELINE", 2)
    stat_col = col.get("STATUS", 7)

    from collections import defaultdict
    daily = defaultdict(lambda: defaultdict(int))
    for row in rows[1:]:
        row = row + [""] * max(0, len(header) - len(row))
        row_date = row[date_col].strip()
        if row_date < since_date:
            continue
        pipeline = row[pipe_col].strip() or "default"
        status   = row[stat_col].strip().lower()
        if status in ("success", "failed"):
            daily[row_date][pipeline] += 1

    results = []
    for d in sorted(daily):
        for pipe, count in daily[d].items():
            tok_in, tok_out = PIPELINE_TOKEN_ESTIMATES.get(pipe, PIPELINE_TOKEN_ESTIMATES["default"])
            price_in, price_out = ANTHROPIC_DEFAULT_PRICE
            for model, p in ANTHROPIC_PRICING.items():
                if "haiku" in pipe or pipe == "photo_catalog" or pipe == "content_creator":
                    price_in, price_out = ANTHROPIC_PRICING.get("claude-haiku-4-5-20251001", ANTHROPIC_DEFAULT_PRICE)
                    break
            total_in  = tok_in  * count
            total_out = tok_out * count
            est_cost  = (total_in / 1000 * price_in + total_out / 1000 * price_out) / 1000
            results.append({
                "date": d, "pipeline": pipe, "runs": count,
                "input_k": total_in, "output_k": total_out,
                "cost_usd": round(est_cost, 4),
            })
    return results


# ─── OPENAI ───────────────────────────────────────────────────────────────────

OPENAI_PRICING = {
    "dall-e-3":    {"standard-1024x1024": 0.04, "hd-1024x1024": 0.08,
                    "standard-1024x1792": 0.08, "hd-1024x1792": 0.12},
    "gpt-image-1": {"low": 0.011, "medium": 0.042, "high": 0.167},
    "whisper-1":   {"per_minute": 0.006},
    "gpt-4o":      {"input_1m": 2.50, "output_1m": 10.00},
    "gpt-4o-mini": {"input_1m": 0.15, "output_1m": 0.60},
}


def _fetch_openai_usage(since_date: str) -> list:
    if not OPENAI_KEY:
        print("  [OpenAI] OPENAI_API_KEY not set — skipping")
        return []
    results = []
    d = datetime.strptime(since_date, "%Y-%m-%d").date()
    today = date.today()
    while d <= today:
        ds = d.strftime("%Y-%m-%d")
        try:
            url = f"https://api.openai.com/v1/usage?date={ds}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {OPENAI_KEY}"})
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            data = resp.get("data", [])
            for entry in data:
                model = entry.get("snapshot_id", "unknown")
                n_ctx = entry.get("n_context_tokens_total", 0)
                n_gen = entry.get("n_generated_tokens_total", 0)
                n_req = entry.get("n_requests", 0)
                pricing = OPENAI_PRICING.get(model, {})
                in_price  = pricing.get("input_1m", 0)
                out_price = pricing.get("output_1m", 0)
                cost = (n_ctx / 1_000_000 * in_price + n_gen / 1_000_000 * out_price)
                results.append({
                    "date": ds, "model": model, "requests": n_req,
                    "input_tokens": n_ctx, "output_tokens": n_gen,
                    "cost_usd": round(cost, 4),
                })
        except Exception as e:
            print(f"  [OpenAI] {ds}: {e}")
        d += timedelta(days=1)
    return results


# ─── APIFY ────────────────────────────────────────────────────────────────────

def _fetch_apify_usage() -> dict:
    if not APIFY_KEY:
        print("  [Apify] APIFY_API_KEY not set — skipping")
        return {}
    try:
        url = f"https://api.apify.com/v2/users/me?token={APIFY_KEY}"
        resp = json.loads(urllib.request.urlopen(url, timeout=30).read())
        data = resp.get("data", {})
        plan = data.get("plan", {})
        limits = data.get("limits", {})
        usage = data.get("monthlyUsage", {})
        return {
            "plan": plan.get("name", "unknown"),
            "monthly_compute_units_limit": limits.get("monthlyApifyActorComputeUnits", 0),
            "monthly_compute_units_used": usage.get("monthlyActorComputeUnits", 0),
            "monthly_requests_used": usage.get("monthlyExternalDataTransferGbytes", 0),
            "monthly_usd_spent": usage.get("monthlyUsdSpentOnActorRuns", 0),
        }
    except Exception as e:
        print(f"  [Apify] Error: {e}")
        return {}


# ─── GITHUB ACTIONS ───────────────────────────────────────────────────────────

def _fetch_github_billing() -> dict:
    if not GH_TOKEN:
        print("  [GitHub] GH_TOKEN not set — skipping")
        return {}
    owner, repo = GH_REPO.split("/", 1) if "/" in GH_REPO else (GH_REPO, GH_REPO)
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/billing/usage"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return {
            "total_minutes_used": resp.get("total_minutes_used", 0),
            "included_minutes":   resp.get("included_minutes", 0),
            "minutes_used_breakdown": resp.get("minutes_used_breakdown", {}),
            "total_paid_minutes_used": resp.get("total_paid_minutes_used", 0),
        }
    except Exception as e:
        print(f"  [GitHub] {e}")
        return {}


# ─── DASHBOARD SUMMARY ────────────────────────────────────────────────────────

def _write_dashboard(token: str, week_label: str, totals: dict):
    rows = _sheets_get(token, COST_TRACKER_SHEET_ID, f"'{COST_TAB}'!A:F")
    # Find or append summary row for this week
    week_rows = [r for r in rows if r and r[0] == week_label]
    if week_rows:
        return  # already written
    new_row = [
        week_label,
        round(totals.get("anthropic_usd", 0), 4),
        round(totals.get("openai_usd", 0), 4),
        round(totals.get("apify_usd", 0), 4),
        round(totals.get("github_usd", 0), 4),
        round(sum(totals.values()), 4),
    ]
    _sheets_append(token, COST_TRACKER_SHEET_ID, COST_TAB, [new_row])


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    token = _get_token()
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    week_label = f"{week_ago} → {today.isoformat()}"

    # Ensure Cost Tracker tab exists
    _ensure_tab(token, COST_TRACKER_SHEET_ID, COST_TAB, [
        "WEEK", "CLAUDE (est USD)", "OPENAI USD", "APIFY USD", "GITHUB USD", "TOTAL USD"
    ])

    # Ensure per-source tabs
    _ensure_tab(token, COST_TRACKER_SHEET_ID, "Claude (estimated)", [
        "DATE", "PIPELINE", "RUNS", "INPUT TOKENS (K)", "OUTPUT TOKENS (K)", "EST COST USD"
    ])
    _ensure_tab(token, COST_TRACKER_SHEET_ID, "OpenAI", [
        "DATE", "MODEL", "REQUESTS", "INPUT TOKENS", "OUTPUT TOKENS", "COST USD"
    ])
    _ensure_tab(token, COST_TRACKER_SHEET_ID, "Apify", [
        "WEEK", "PLAN", "COMPUTE UNITS LIMIT", "COMPUTE UNITS USED", "MONTHLY USD SPENT"
    ])
    _ensure_tab(token, COST_TRACKER_SHEET_ID, "GitHub Actions", [
        "WEEK", "TOTAL MINUTES USED", "INCLUDED MINUTES", "LINUX MINUTES", "MACOS MINUTES", "PAID MINUTES"
    ])

    totals = {}

    # ── Anthropic (estimated from logs)
    print("[cost] Fetching Anthropic estimates from Content Creation Log...")
    ant_rows = _fetch_anthropic_estimate(token, week_ago)
    if ant_rows:
        sheet_rows = [[r["date"], r["pipeline"], r["runs"],
                       r["input_k"], r["output_k"], r["cost_usd"]] for r in ant_rows]
        _sheets_append(token, COST_TRACKER_SHEET_ID, "Claude (estimated)", sheet_rows)
        totals["anthropic_usd"] = sum(r["cost_usd"] for r in ant_rows)
        print(f"  {len(ant_rows)} rows — est. ${totals['anthropic_usd']:.4f}")
    else:
        totals["anthropic_usd"] = 0
        print("  No data")

    # ── OpenAI
    print("[cost] Fetching OpenAI usage...")
    oai_rows = _fetch_openai_usage(week_ago)
    if oai_rows:
        sheet_rows = [[r["date"], r["model"], r["requests"],
                       r["input_tokens"], r["output_tokens"], r["cost_usd"]] for r in oai_rows]
        _sheets_append(token, COST_TRACKER_SHEET_ID, "OpenAI", sheet_rows)
        totals["openai_usd"] = sum(r["cost_usd"] for r in oai_rows)
        print(f"  {len(oai_rows)} entries — ${totals['openai_usd']:.4f}")
    else:
        totals["openai_usd"] = 0
        print("  No data / key not set")

    # ── Apify
    print("[cost] Fetching Apify usage...")
    apify = _fetch_apify_usage()
    if apify:
        _sheets_append(token, COST_TRACKER_SHEET_ID, "Apify", [[
            week_label, apify.get("plan"),
            apify.get("monthly_compute_units_limit"),
            apify.get("monthly_compute_units_used"),
            apify.get("monthly_usd_spent", 0),
        ]])
        totals["apify_usd"] = float(apify.get("monthly_usd_spent", 0))
        print(f"  Plan: {apify.get('plan')} — ${totals['apify_usd']:.4f}/mo")
    else:
        totals["apify_usd"] = 0

    # ── GitHub Actions
    print("[cost] Fetching GitHub Actions billing...")
    gh = _fetch_github_billing()
    if gh:
        breakdown = gh.get("minutes_used_breakdown", {})
        _sheets_append(token, COST_TRACKER_SHEET_ID, "GitHub Actions", [[
            week_label,
            gh.get("total_minutes_used", 0),
            gh.get("included_minutes", 0),
            breakdown.get("UBUNTU", 0),
            breakdown.get("MACOS", 0),
            gh.get("total_paid_minutes_used", 0),
        ]])
        paid_min = gh.get("total_paid_minutes_used", 0)
        totals["github_usd"] = round(paid_min * 0.008, 4)  # $0.008/min Linux
        print(f"  {gh.get('total_minutes_used', 0)} total min — ${totals['github_usd']:.4f}")
    else:
        totals["github_usd"] = 0

    # ── Dashboard summary row
    _write_dashboard(token, week_label, totals)
    grand = sum(totals.values())
    print(f"\n[cost] Weekly total: ${grand:.4f}")
    print(f"  Claude: ${totals['anthropic_usd']:.4f} | OpenAI: ${totals['openai_usd']:.4f} | "
          f"Apify: ${totals['apify_usd']:.4f} | GitHub: ${totals['github_usd']:.4f}")


if __name__ == "__main__":
    main()
