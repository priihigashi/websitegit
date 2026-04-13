"""
loose_end_detector.py — Proactive loose-end detection. Zero LLM cost.
Checks 3 sources:
  A) Content Queue: rows stuck at "Pending" 5+ days
  B) Calendar: past-due events without DONE prefix
  C) carry_forwards.json: items needing human decision
"""
import os, json
import pytz
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from google.oauth2 import service_account

SPREADSHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CARRIES_FILE   = ".github/agent_state/carry_forwards.json"
et             = pytz.timezone("America/New_York")
STALE_DAYS     = 5

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar",
]


def _creds():
    return service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SA_KEY"]), scopes=SCOPES
    )


def _check_stale_content_queue(sheets_svc):
    result = sheets_svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="📋 Content Queue!A:J",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return []

    stale  = []
    cutoff = datetime.now(et) - timedelta(days=STALE_DAYS)
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 10:
            continue
        date_str = row[0] if row else ""
        status   = row[9] if len(row) > 9 else ""
        topic    = row[5] if len(row) > 5 else "(no topic)"
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

    print(f"[loose_end_detector] Done. {tasks_created} tasks created.")
    return {"stale_content": len(stale), "overdue_calendar": len(overdue), "tasks_created": tasks_created}
