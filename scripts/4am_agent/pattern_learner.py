"""
pattern_learner.py — Reads recent Runs Log entries, detects recurring issues,
and automatically creates skill files in GitHub or Calendar tasks to prevent them.

This runs at the END of every 4AM agent execution.
It answers the question: "What keeps going wrong, and how do we stop it?"
"""
import os, json, base64, requests
import pytz
import anthropic
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2 import service_account

SPREADSHEET_ID  = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
GITHUB_REPO     = "priihigashi/oak-park-ai-hub"
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SA_KEY   = os.environ["GOOGLE_SA_KEY"]
SCOPES          = ["https://www.googleapis.com/auth/spreadsheets",
                   "https://www.googleapis.com/auth/calendar"]
et              = pytz.timezone("America/New_York")

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

def detect_patterns(logs):
    """
    Sends recent logs to Claude and asks it to identify recurring issues
    and generate skill files or tasks to prevent them.
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

    prompt = f"""You are analyzing run logs for an automated content agent (Oak Park Construction).
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
        model="claude-opus-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    return json.loads(text)


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


def run(notifier_fn=None):
    """Main entry point — call this from main.py after logging the run."""
    print("[pattern_learner] Reading recent logs...")
    logs     = read_recent_logs(n=14)
    patterns = detect_patterns(logs)

    if not patterns:
        print("[pattern_learner] No patterns detected.")
        return []

    print(f"[pattern_learner] {len(patterns)} pattern(s) found — applying fixes...")
    return apply_patterns(patterns, notifier_fn=notifier_fn)
