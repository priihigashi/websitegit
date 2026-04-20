"""
loose_end_detector.py — Proactive loose-end detection. Zero LLM cost.
Checks 3 sources:
  A) Content Queue: rows stuck at "Pending" 5+ days
  B) Calendar: past-due events without DONE prefix
  C) carry_forwards.json: items needing human decision
"""
import os, json
import urllib.request, urllib.parse
import pytz
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SPREADSHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CARRIES_FILE   = ".github/agent_state/carry_forwards.json"
et             = pytz.timezone("America/New_York")
STALE_DAYS     = 5


def _creds():
    raw = os.environ["SHEETS_TOKEN"]
    td  = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        "https://oauth2.googleapis.com/token", data=data
    ).read())
    return Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )


def _check_stale_content_queue(sheets_svc):
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="📋 Content Queue!A:L",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return []

    headers = rows[0]
    hmap = {h.strip().lower(): i for i, h in enumerate(headers)}

    def _v(row, name):
        idx = hmap.get(name)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    stale  = []
    cutoff = datetime.now(et) - timedelta(days=STALE_DAYS)
    for i, row in enumerate(rows[1:], start=2):
        date_str = _v(row, "date created")
        status   = _v(row, "status")
        topic    = _v(row, "hook") or "(no topic)"
        if status.lower() == "pending" and date_str:
            try:
                created  = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=et)
                days_old = (datetime.now(et) - created).days
                if days_old >= STALE_DAYS:
                    stale.append({"row": i, "topic": topic, "created": date_str, "days_stale": days_old})
            except ValueError:
                pass
    return stale


def _check_overdue_calendar(calendar_svc):
    now   = datetime.now(et)
    start = (now - timedelta(days=14)).isoformat()
    end   = now.isoformat()
    try:
        events = calendar_svc.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute().get("items", [])
    except Exception as e:
        print(f"[loose_end_detector] Calendar read failed: {e}")
        return []

    overdue = []
    for ev in events:
        summary = ev.get("summary", "")
        if summary.upper().startswith("DONE"):
            continue
        if summary.startswith(("🔄 CARRY:", "⚠️", "🔴", "📋")):
            continue  # skip agent-created tasks
        start_dt = ev.get("start", {}).get("date") or ev.get("start", {}).get("dateTime", "")
        overdue.append({"title": summary, "date": start_dt[:10], "event_id": ev.get("id")})
    return overdue


def _create_calendar_task(calendar_svc, title, description):
    date_str = datetime.now(et).strftime("%Y-%m-%d")
    try:
        calendar_svc.events().insert(
            calendarId="primary",
            body={
                "summary":     title,
                "description": description,
                "start":       {"date": date_str},
                "end":         {"date": date_str},
                "colorId":     "11",
            },
        ).execute()
        return True
    except Exception as e:
        print(f"[loose_end_detector] Calendar create failed: {e}")
        return False


def _check_inspiration_library_failures(sheets_svc):
    """Find rows in Inspiration Library with Status=failed/error → re-trigger candidates."""
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="'📥 Inspiration Library'!A:S",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        return []
    headers = rows[0]
    hmap = {h.strip().lower(): i for i, h in enumerate(headers)}

    def _v(row, name):
        idx = hmap.get(name)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    failed = []
    for i, row in enumerate(rows[1:], start=2):
        url      = _v(row, "url")
        comments = _v(row, "comments")
        status   = _v(row, "status").lower()
        if status in ("failed", "error", "capture_failed") and url:
            failed.append({"row": i, "url": url[:120], "comments": comments[:100]})
    return failed


def run(carry_forwards=None):
    """Main entry. carry_forwards = result from chat_log_reader (optional)."""
    creds        = _creds()
    sheets_svc   = build("sheets",   "v4", credentials=creds)
    calendar_svc = build("calendar", "v3", credentials=creds)
    tasks_created = 0

    # A) Stale Content Queue
    stale = _check_stale_content_queue(sheets_svc)
    print(f"[loose_end_detector] Stale Content Queue rows: {len(stale)}")
    for item in stale:
        title = f"⚠️ STALE: {item['topic'][:60]} ({item['days_stale']}d pending)"
        desc  = f"Content Queue row {item['row']} stuck at Pending since {item['created']}.\nReview, approve, or delete it."
        if _create_calendar_task(calendar_svc, title, desc):
            tasks_created += 1

    # B) Overdue Calendar events
    overdue = _check_overdue_calendar(calendar_svc)
    print(f"[loose_end_detector] Overdue Calendar events: {len(overdue)}")
    for item in overdue[:5]:  # cap at 5 to avoid noise
        title = f"⚠️ OVERDUE: {item['title'][:60]}"
        desc  = f"Task from {item['date']} was never marked DONE.\nComplete it or rename with DONE prefix."
        if _create_calendar_task(calendar_svc, title, desc):
            tasks_created += 1

    # C) Non-auto carry-forwards (need human decision)
    carries = carry_forwards or []
    if not carries and os.path.exists(CARRIES_FILE):
        with open(CARRIES_FILE) as f:
            carries = json.load(f)

    human_carries = [c for c in carries if not c.get("auto_actionable")]
    print(f"[loose_end_detector] Human-needed carry-forwards: {len(human_carries)}")
    for c in human_carries:
        title = f"📋 CARRY-FORWARD: {c['task'][:60]}"
        desc  = f"{c.get('context', '')}\n\nFrom: {c.get('source_log', 'chat log')}\nNeeds your decision."
        if _create_calendar_task(calendar_svc, title, desc):
            tasks_created += 1

    # D) Failed captures in Inspiration Library → calendar task with re-trigger command
    failed_captures = _check_inspiration_library_failures(sheets_svc)
    print(f"[loose_end_detector] Failed captures in Inspiration Library: {len(failed_captures)}")
    for item in failed_captures[:5]:  # cap at 5 to avoid noise
        title = f"🔴 CAPTURE FAILED: {item['url'][:55]}"
        desc  = (
            f"Capture pipeline failed for:\n{item['url']}\n\n"
            f"Notes: {item['comments']}\n"
            f"Inspiration Library row: {item['row']}\n\n"
            f"Re-trigger command:\n"
            f"gh workflow run 'Capture Pipeline v2' "
            f"--repo priihigashi/oak-park-ai-hub -f url=\"{item['url']}\""
        )
        if _create_calendar_task(calendar_svc, title, desc):
            tasks_created += 1

    print(f"[loose_end_detector] Done. {tasks_created} tasks created.")
    return {
        "stale_content":     len(stale),
        "overdue_calendar":  len(overdue),
        "failed_captures":   len(failed_captures),
        "tasks_created":     tasks_created,
    }
