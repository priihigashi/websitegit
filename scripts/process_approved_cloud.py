#!/usr/bin/env python3
"""
process_approved_cloud.py — Cloud version for GitHub Actions
Reads SHEETS_TOKEN_PATH and SHEET_ID from env vars.
"""

import os, sys, json, urllib.request, urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

TOKEN_FILE_PATH = os.environ.get("SHEETS_TOKEN_PATH", "")
SHEET_ID = os.environ.get("SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB     = "📋 Content Queue"
ANALYTICS_TAB = "📊 Analytics"
POSTING_SLOTS = ["9:00 AM", "5:00 PM"]

ANALYTICS_HEADER = [
    "Date Scheduled","Scheduled Post Date","Scheduled Post Time","Project Name",
    "Service Type","Content Type","Hook","Platform","Status","Date Posted",
    "Views","Likes","Comments","Saves","Shares","Reach","Profile Visits",
    "Follows From Post","Notes","Recycled? (Y/N)","Recycle Date",
    "Recycle Views","Recycle Likes","Recycle Saves","Performance Delta (%)"
]

def get_token():
    td = json.loads(Path(TOKEN_FILE_PATH).read_text())
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token"
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    return resp["access_token"]

def api(token, method, path, body=None):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{urllib.parse.quote(path, safe='/:!?=&')}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

def ensure_analytics(token):
    meta = api(token, "GET", "")
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if ANALYTICS_TAB not in tabs:
        api(token, "POST", ":batchUpdate",
            {"requests": [{"addSheet": {"properties": {"title": ANALYTICS_TAB}}}]})
        api(token, "POST",
            f"/values/'{ANALYTICS_TAB}'!A1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
            {"values": [ANALYTICS_HEADER]})

def get_taken_slots(token):
    try:
        rows = api(token, "GET", f"/values/'{ANALYTICS_TAB}'!B:C").get("values", [])
        return {(r[0].strip(), r[1].strip()) for r in rows[1:] if len(r) >= 2}
    except Exception:
        return set()

def next_slot(taken):
    check = date.today()
    if datetime.now().hour >= 17:
        check += timedelta(days=1)
    for _ in range(60):
        for slot in POSTING_SLOTS:
            if (check.isoformat(), slot) not in taken:
                return check, slot
        check += timedelta(days=1)
    return date.today() + timedelta(days=1), POSTING_SLOTS[0]

def main():
    if not TOKEN_FILE_PATH or not Path(TOKEN_FILE_PATH).exists():
        print("❌ SHEETS_TOKEN_PATH not set"); sys.exit(1)

    print(f"\n📋 Process Approved — Cloud Run — {date.today()}")
    token = get_token()
    ensure_analytics(token)

    rows = api(token, "GET", f"/values/'{QUEUE_TAB}'").get("values", [])
    if len(rows) < 2:
        print("✅ Queue empty"); return

    header = [h.strip() for h in rows[0]]
    def ci(name):
        return next((i for i,h in enumerate(header) if name.lower() in h.lower()), None)

    approved = []
    for idx, row in enumerate(rows[1:], start=2):
        def val(n):
            i = ci(n)
            return row[i].strip() if i is not None and len(row) > i else ""
        if val("status").lower() == "approved":
            approved.append({"row": idx, "project": val("project name"),
                             "service": val("service type"), "content_type": val("content type"),
                             "hook": val("hook"), "platform": val("platform"),
                             "status_col": ci("status"), "date_col": ci("suggested post date")})

    if not approved:
        print("✅ No Approved posts found"); return

    print(f"🟢 {len(approved)} Approved post(s) to schedule")
    taken = get_taken_slots(token)
    today = date.today().isoformat()
    analytics_rows = []

    for post in approved:
        post_date, post_time = next_slot(taken)
        taken.add((post_date.isoformat(), post_time))

        # Update Status
        col = chr(ord('A') + post["status_col"])
        api(token, "PUT",
            f"/values/'{QUEUE_TAB}'!{col}{post['row']}?valueInputOption=USER_ENTERED",
            {"values": [["Scheduled"]]})
        # Update date
        if post["date_col"] is not None:
            col2 = chr(ord('A') + post["date_col"])
            api(token, "PUT",
                f"/values/'{QUEUE_TAB}'!{col2}{post['row']}?valueInputOption=USER_ENTERED",
                {"values": [[f"{post_date.isoformat()} {post_time}"]]})

        print(f"  ✅ {post['project']} → {post_date.isoformat()} {post_time}")
        analytics_rows.append([
            today, post_date.isoformat(), post_time, post["project"],
            post["service"], post["content_type"], post["hook"], post["platform"],
            "Scheduled", *[""] * 10, "N", *[""] * 4
        ])

    if analytics_rows:
        api(token, "POST",
            f"/values/'{ANALYTICS_TAB}'!A1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
            {"values": analytics_rows})

    print(f"\n✅ Scheduled {len(approved)} post(s)")

if __name__ == "__main__":
    main()
