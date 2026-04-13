"""
pattern_learner.py — Reads recent Runs Log entries, detects recurring issues,
and automatically creates skill files in GitHub or Calendar tasks to prevent them.

This runs at the END of every 4AM agent execution.
It answers TWO questions:
  1. "What keeps going wrong, and how do we stop it?" (log-based)
  2. "What changed in our master plans, and what should Claude learn?" (plan-based, 3-tier)

3-TIER PLAN SELF-IMPROVEMENT:
  Tier 1 (Sheets only, zero LLM): compare Flow Plans Tracker timestamps vs last_seen.json
  Tier 2 (Drive preview, zero LLM): check if change is meaningful (not just a date update)
  Tier 3 (Haiku LLM, only if meaningful): extract actionable rules → write to Claude Rules tab
  Result: 90%+ of nights = zero LLM cost on plan improvement path.
"""
import os, json, base64, requests
import context_reader
import pytz
import anthropic
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2 import service_account

SPREADSHEET_ID        = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
FLOW_PLANS_TRACKER_ID = "1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k"
LAST_SEEN_PATH        = ".github/agent_state/last_seen.json"
GITHUB_REPO           = "priihigashi/oak-park-ai-hub"
GITHUB_TOKEN          = os.environ.get("GITHUB_TOKEN", "")
ANTHROPIC_KEY         = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SA_KEY         = os.environ["GOOGLE_SA_KEY"]
SCOPES                = ["https://www.googleapis.com/auth/spreadsheets",
                         "https://www.googleapis.com/auth/calendar"]
DRIVE_SCOPES          = ["https://www.googleapis.com/auth/spreadsheets",
                         "https://www.googleapis.com/auth/drive.readonly",
                         "https://www.googleapis.com/auth/calendar"]
et                    = pytz.timezone("America/New_York")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def _sheets():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SA_KEY), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def _calendar():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SA_KEY), scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)


def _github_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
    }


# ─── Read recent logs ─────────────────────────────────────────────────────────

def read_recent_logs(n=14):
    """Read last N rows from Runs Log tab."""
    result = _sheets().spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="📊 Runs Log!A:M",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return []
    headers  = rows[0]
    data_rows = rows[1:][-n:]  # last n entries
    return [dict(zip(headers, row)) for row in data_rows]


# ─── Detect patterns with Claude ─────────────────────────────────────────────

def detect_patterns(logs, extra_context=""):
    """
    Sends recent logs to Claude and asks it to identify recurring issues
    and generate skill files or tasks to prevent them.
    extra_context: optional string from context_reader (recent Claude Rules).
    """
    if not logs:
        return []

    failed = [l for l in logs if l.get("Status", "").lower() == "fail"]
    errors = [l.get("Error Message", "") for l in failed if l.get("Error Message")]
    lessons = [l.get("Lessons Learned / Issues Noticed", "") for l in logs if l.get("Lessons Learned / Issues Noticed")]

    if not errors and not lessons:
        print("[pattern_learner] No errors or lessons in recent logs — nothing to learn.")
        return []

    summary = {
        "total_runs": len(logs),
        "failed_runs": len(failed),
        "errors": errors,
        "lessons_learned": lessons,
    }

    context_block = f"\n\n{extra_context}\n" if extra_context else ""

    prompt = f"""You are analyzing run logs for an automated content agent (Oak Park Construction).{context_block}
Here is a summary of recent runs:

{json.dumps(summary, indent=2)}

Identify any RECURRING patterns — errors that happened more than once, or lessons that suggest
a systemic problem. For each pattern:

1. Is this something that can be fixed with a new skill/automation rule? (yes/no)
2. If yes — write the full .md skill file content that would prevent it
3. If no — write a Google Calendar task description that tells the human what to do

Return a JSON array (can be empty [] if no patterns found):
[
  {{
    "pattern": "short description of what keeps happening",
    "occurrences": 2,
    "can_automate": true,
    "skill_filename": "SKILL_fix_apify_timeout.md",
    "skill_content": "# SKILL: Fix Apify Timeout\\n...",
    "calendar_task_title": null,
    "calendar_task_description": null
  }},
  {{
    "pattern": "...",
    "occurrences": 1,
    "can_automate": false,
    "skill_filename": null,
    "skill_content": null,
    "calendar_task_title": "Review Pexels quota — agent hit rate limit twice",
    "calendar_task_description": "The 4AM agent hit Pexels API rate limits on 2 recent runs. Consider upgrading the Pexels plan or adding retry logic. Check: https://www.pexels.com/api/"
  }}
]"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("[pattern_learner] JSON parse failed — retrying with tighter prompt...")
        resp2 = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=5000,
            messages=[{"role": "user", "content": prompt + "\n\nReturn ONLY the JSON array. No explanation, no markdown."}],
        )
        text2 = resp2.content[0].text.strip()
        if "```json" in text2: text2 = text2.split("```json")[1].split("```")[0].strip()
        elif "```" in text2:   text2 = text2.split("```")[1].split("```")[0].strip()
        try:
            return json.loads(text2)
        except json.JSONDecodeError:
            print("[pattern_learner] Retry also failed — skipping pattern detection.")
            return []


# ─── Act on patterns ─────────────────────────────────────────────────────────

def _create_skill_in_github(filename, content):
    """Create or update a skill .md file in the GitHub repo."""
    path     = f"skills/{filename}"
    api_url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    b64      = base64.b64encode(content.encode()).decode()
    now_et   = datetime.now(et).strftime("%Y-%m-%d %H:%M ET")

    # Check if file exists (need SHA to update)
    existing = requests.get(api_url, headers=_github_headers())
    payload  = {
        "message": f"auto: add skill {filename} from pattern learner [{now_et}]",
        "content": b64,
    }
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]

    resp = requests.put(api_url, headers=_github_headers(), json=payload)
    return resp.status_code in (200, 201)


def _create_calendar_task(title, description):
    """Create an all-day Google Calendar event as a task reminder."""
    try:
        now_et   = datetime.now(et)
        date_str = now_et.strftime("%Y-%m-%d")
        _calendar().events().insert(
            calendarId="primary",
            body={
                "summary":     f"🤖 {title}",
                "description": description,
                "start":       {"date": date_str},
                "end":         {"date": date_str},
                "colorId":     "5",  # banana yellow
            },
        ).execute()
        return True
    except Exception as e:
        print(f"[pattern_learner] Calendar task failed: {e}")
        return False


def apply_patterns(patterns, notifier_fn=None):
    """
    For each detected pattern:
    - If automatable → create skill file in GitHub
    - If not → create Google Calendar task
    Always send a push notification.
    """
    results = []
    for p in patterns:
        if p.get("can_automate") and p.get("skill_filename") and p.get("skill_content"):
            ok = _create_skill_in_github(p["skill_filename"], p["skill_content"])
            action = "skill_created" if ok else "skill_failed"
            if notifier_fn:
                notifier_fn(p["skill_filename"], p["pattern"])
            print(f"[pattern_learner] Skill {'created' if ok else 'FAILED'}: {p['skill_filename']}")
        else:
            title = p.get("calendar_task_title", f"Review pattern: {p['pattern'][:60]}")
            desc  = p.get("calendar_task_description", p["pattern"])
            ok    = _create_calendar_task(title, desc)
            action = "calendar_task_created" if ok else "calendar_task_failed"
            if notifier_fn:
                from notifier import notify_skill_task
                notify_skill_task(title, desc)
            print(f"[pattern_learner] Calendar task {'created' if ok else 'FAILED'}: {title}")

        results.append({"pattern": p["pattern"], "action": action})

    return results


# ─── 3-Tier Plan Self-Improvement ────────────────────────────────────────────

def load_last_seen():
    """Load previous doc timestamps from GitHub state file."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{LAST_SEEN_PATH}"
    r = requests.get(url, headers=_github_headers())
    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode()
        return json.loads(content)
    return {}


def save_last_seen(state):
    """Save current doc timestamps back to GitHub state file."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{LAST_SEEN_PATH}"
    b64 = base64.b64encode(json.dumps(state, indent=2).encode()).decode()
    existing = requests.get(url, headers=_github_headers())
    payload = {
        "message": f"agent: update last_seen [{datetime.now(et).strftime('%Y-%m-%d')}]",
        "content": b64,
    }
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]
    r = requests.put(url, headers=_github_headers(), json=payload)
    if r.status_code not in (200, 201):
        print(f"[pattern_learner] WARNING: Could not save last_seen: {r.status_code}")


def read_flow_plans_tracker():
    """Tier 1: Read All Docs tab → dict {doc_id: last_updated}. Zero LLM cost."""
    result = _sheets().spreadsheets().values().get(
        spreadsheetId=FLOW_PLANS_TRACKER_ID,
        range="All Docs!A:I",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return {}
    headers = rows[0]
    try:
        doc_id_col  = headers.index("DOC_ID")
        updated_col = headers.index("LAST UPDATED")
    except ValueError:
        doc_id_col, updated_col = 6, 8  # fallback positions
    result_map = {}
    for row in rows[1:]:
        if len(row) > doc_id_col and row[doc_id_col]:
            val = row[updated_col] if len(row) > updated_col else ""
            result_map[row[doc_id_col]] = val
    return result_map


def fetch_doc_preview(doc_id, drive_service):
    """Tier 2: Fetch first 600 chars of a Drive doc. Zero LLM cost."""
    try:
        content = drive_service.files().export(
            fileId=doc_id, mimeType="text/plain"
        ).execute()
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        return text[:600]
    except Exception as e:
        print(f"[pattern_learner] Tier 2: Could not preview doc {doc_id}: {e}")
        return None


def _is_trivial_change(preview):
    """Return True if the change looks like just a date/metadata update."""
    if not preview or len(preview) < 50:
        return True
    lower = preview.lower()
    # If the meaningful content is just dates/timestamps, skip
    date_indicators = sum(1 for w in ["2026-0", "last updated", "updated:", "created:"] if w in lower)
    return date_indicators >= 3 and len(preview) < 300


def run_plan_improvement(notifier_fn=None):
    """
    3-tier plan self-improvement gate.
    Returns list of rules written, or empty list if nothing changed.
    """
    # TIER 1: Sheets only — detect changed docs
    print("[pattern_learner] Tier 1: Reading Flow Plans Tracker...")
    current_state = read_flow_plans_tracker()
    last_seen = load_last_seen()

    changed = {
        doc_id: updated
        for doc_id, updated in current_state.items()
        if last_seen.get(doc_id) != updated
    }

    if not changed:
        print("[pattern_learner] Tier 1: No doc changes. Zero tokens used.")
        return []

    print(f"[pattern_learner] Tier 1: {len(changed)} changed doc(s). Starting Tier 2...")

    # TIER 2: Drive preview — filter trivial changes
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SA_KEY), scopes=DRIVE_SCOPES
    )
    drive_service = build("drive", "v3", credentials=creds)

    meaningful = {}
    for doc_id in changed:
        preview = fetch_doc_preview(doc_id, drive_service)
        if not preview:
            continue
        if _is_trivial_change(preview):
            print(f"[pattern_learner] Tier 2: Doc {doc_id[:20]}... trivial — skipping.")
            continue
        meaningful[doc_id] = preview

    if not meaningful:
        print("[pattern_learner] Tier 2: All changes trivial. Zero LLM tokens used.")
        save_last_seen(current_state)
        return []

    # TIER 3: Haiku LLM — extract actionable rules only
    print(f"[pattern_learner] Tier 3: {len(meaningful)} meaningful change(s) — calling Haiku...")

    prompt = f"""You analyze workflow documentation changes for Oak Park Construction AI automation.

These plan docs recently changed. First 600 chars of each:

{json.dumps(meaningful, indent=2)}

For each doc, answer: "What new rule or workflow change should Claude follow in future sessions?"

Be extremely selective. Only include if there is a clear behavioral change needed.
Output ONLY a JSON array (empty [] if nothing actionable):
[
  {{"doc_id": "...", "rule": "One-sentence rule Claude should follow."}}
]"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    rules = json.loads(text)

    if rules:
        print(f"[pattern_learner] {len(rules)} new rule(s) extracted — writing to Claude Rules tab...")
        _write_rules_to_sheet(rules)
        if notifier_fn:
            notifier_fn("plan_improvement", f"{len(rules)} new rule(s) from updated docs")
    else:
        print("[pattern_learner] Tier 3: No actionable rules found.")

    save_last_seen(current_state)
    return rules


def _write_rules_to_sheet(rules):
    """Append new auto-learned rules to Claude Rules tab."""
    now = datetime.now(et).strftime("%Y-%m-%d")
    rows = [[now, r["doc_id"], r["rule"], "auto-learned"] for r in rules]
    _sheets().spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="📋 Claude Rules!A:D",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    print(f"[pattern_learner] Wrote {len(rows)} rule(s) to Claude Rules tab.")


# ─── Main entry point ─────────────────────────────────────────────────────────

def run(notifier_fn=None):
    """Main entry point — call this from main.py after logging the run."""
    print("[pattern_learner] === Log-based pattern detection ===")
    logs = read_recent_logs(n=14)

    print("[pattern_learner] Reading Claude Rules for context...")
    extra_context = context_reader.get_context_summary()
    if extra_context:
        print(f"[pattern_learner] Context loaded ({len(extra_context)} chars)")

    patterns = detect_patterns(logs, extra_context=extra_context)

    if not patterns:
        print("[pattern_learner] No log patterns detected.")
    else:
        print(f"[pattern_learner] {len(patterns)} pattern(s) found — applying fixes...")
        apply_patterns(patterns, notifier_fn=notifier_fn)

    print("[pattern_learner] === Plan self-improvement (3-tier) ===")
    run_plan_improvement(notifier_fn=notifier_fn)

    return patterns
