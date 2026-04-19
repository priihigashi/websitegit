#!/usr/bin/env python3
"""
mark_posted.py — Oak Park Construction Post Status Tracker
Runs every 2 days at 10PM ET via GitHub Actions.

Reads 📋 Content Queue tab:
  - Finds rows where J (Status) = "Scheduled"
  - Checks if the scheduled date+time is at least 2 hours in the past
  - If yes → marks J = "Posted", L = "Posted"
  - Logs to 📊 Analytics tab: Date, Project, Platform, Post Date, Post Time, Drive Link, Posted At

Env vars required:
  SHEETS_TOKEN        — Google OAuth token JSON
  CONTENT_SHEET_ID    — Google Sheet ID
"""

import os, json, urllib.request, urllib.parse, sys, time
from pathlib import Path
from datetime import datetime, timedelta
import pytz

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID      = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB     = "📋 Content Queue"
ANALYTICS_TAB = "📊 Analytics"
ET            = pytz.timezone("America/New_York")

# ── Auth ──────────────────────────────────────────────────────────────────────
_token_cache = {}

def get_token():
    if _token_cache.get("token") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["token"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        path = os.environ.get("SHEETS_TOKEN_PATH", "")
        if path and Path(path).exists():
            raw = Path(path).read_text()
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN set")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["token"] = resp["access_token"]
    _token_cache["exp"]   = time.time() + resp.get("expires_in", 3500) - 60
    return resp["access_token"]

# ── Sheets helpers ─────────────────────────────────────────────────────────────
def col_letter(n):
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result

def sheet_get(token, range_str):
    enc = urllib.parse.quote(range_str, safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read())

def sheet_update_cells(token, tab_name, updates: list):
    data = [{"range": f"'{tab_name}'!{cell}", "values": [[val]]} for cell, val in updates]
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": data}).encode()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req).read()
    except Exception as e:
        print(f"  ⚠️  Sheet update error: {e}")

def sheet_append_row(token, tab_name, values: list):
    """Append a new row to a tab."""
    enc = urllib.parse.quote(f"'{tab_name}'", safe="!:'")
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
           f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    payload = json.dumps({"values": [values]}).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req).read()
    except Exception as e:
        print(f"  ⚠️  Analytics append error: {e}")

def create_sheet_tab(token, title: str):
    """Create a new tab in the spreadsheet via batchUpdate/addSheet."""
    payload = json.dumps({"requests": [{"addSheet": {"properties": {"title": title}}}]}).encode()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req).read()
        print(f"  📊 Created tab: {title}")
    except Exception as e:
        if "already exists" not in str(e):
            print(f"  ⚠️  Could not create tab '{title}': {e}")

def ensure_analytics_header(token):
    """Create Analytics tab + header row if they don't exist yet."""
    tab_exists = False
    try:
        result = sheet_get(token, f"'{ANALYTICS_TAB}'!A1:H1")
        rows = result.get("values", [])
        if rows and rows[0] and rows[0][0] == "Logged At":
            return  # Header already exists — done
        tab_exists = True  # Tab exists but header missing
    except Exception:
        pass  # Tab does not exist

    if not tab_exists:
        create_sheet_tab(token, ANALYTICS_TAB)

    header = ["Logged At", "Project", "Platform", "Post Date", "Post Time",
              "Drive Link", "Status", "Notes"]
    sheet_append_row(token, ANALYTICS_TAB, header)
    print(f"  📊 Analytics tab header written")

# ── Parse scheduled rows ───────────────────────────────────────────────────────
def get_scheduled_rows(token) -> list[dict]:
    rows = sheet_get(token, f"'{QUEUE_TAB}'").get("values", [])
    if len(rows) < 2:
        return []
    header = [h.strip() for h in rows[0]]
    def ci(name): return next((i for i, h in enumerate(header) if name.lower() in h.lower()), None)

    result = []
    for idx, row in enumerate(rows[1:], start=2):
        def v(col): i = ci(col); return row[i].strip() if i is not None and len(row) > i else ""
        if v("status").lower() != "scheduled":
            continue
        result.append({
            "row":        idx,
            "project":    v("project name"),
            "platform":   v("platform"),
            "post_date":  v("suggested post date"),
            "post_time":  v("suggested time"),
            "drive_link": v("drive folder path"),
            "status_col": col_letter(ci("status") + 1) if ci("status") is not None else "K",
        })
    return result

# ── Resolve post datetime (same logic as schedule_posts.py) ───────────────────
def parse_post_datetime(post_date_str: str, post_time_str: str):
    """Returns ET-aware datetime or None on parse failure."""
    try:
        post_date = datetime.strptime(post_date_str, "%Y-%m-%d").date()
    except Exception:
        return None
    try:
        t = datetime.strptime(post_time_str.strip(), "%I:%M %p")
    except Exception:
        try:
            t = datetime.strptime(post_time_str.strip(), "%H:%M")
        except Exception:
            return None
    return ET.localize(datetime(post_date.year, post_date.month, post_date.day,
                                t.hour, t.minute))

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now_et = datetime.now(ET)
    print(f"\n📊 Mark Posted — {now_et.strftime('%Y-%m-%d %I:%M %p ET')}")
    print("=" * 50)

    token = get_token()
    ensure_analytics_header(token)

    rows = get_scheduled_rows(token)
    if not rows:
        print("✅ No rows with Status=Scheduled found — nothing to mark.")
        return

    print(f"   Found {len(rows)} Scheduled row(s)\n")
    marked = 0

    for post in rows:
        dt = parse_post_datetime(post["post_date"], post["post_time"])
        if dt is None:
            print(f"  ⚠️  {post['project']} — could not parse date/time, skipping")
            continue

        hours_since = (now_et - dt).total_seconds() / 3600
        if hours_since < 2:
            print(f"  ⏳ {post['project']} — only {hours_since:.1f}h since post time, too soon")
            continue

        print(f"  ✅ {post['project']} — {hours_since:.1f}h past post time → marking Posted")

        # Update queue row
        sheet_update_cells(token, QUEUE_TAB, [
            (f"{post['status_col']}{post['row']}", "Posted"),
        ])

        # Mirror status to 🎬 In Production (Content Control)
        try:
            import sys
            from pathlib import Path as _Path
            sys.path.insert(0, str(_Path(__file__).parent))
            from content_tracker import update_in_production
            update_in_production(
                title=post["project"],
                content_type="Carousel",
                status="Published",
                drive_folder_link=post["drive_link"],
            )
        except Exception as _e:
            print(f"  In Production update skipped (non-fatal): {_e}")

        # Log to Analytics
        log_row = [
            now_et.strftime("%Y-%m-%d %I:%M %p ET"),
            post["project"],
            post["platform"],
            post["post_date"],
            post["post_time"],
            post["drive_link"],
            "Posted",
            f"Auto-marked {hours_since:.1f}h after scheduled time",
        ]
        sheet_append_row(token, ANALYTICS_TAB, log_row)
        marked += 1

    print(f"\n✅ Done — {marked} post(s) marked as Posted.")

if __name__ == "__main__":
    main()
