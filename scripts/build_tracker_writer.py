#!/usr/bin/env python3
"""
build_tracker_writer.py — called by every GitHub Actions workflow after it runs.

Usage:
  python3 scripts/build_tracker_writer.py \
    --workflow content_creator.yml \
    --status success \          # or: failure / cancelled
    --error "optional error message"

Finds the row in '🔧 Build Tracker' that matches the workflow filename (col G),
then updates Last Run (col H) and Last Status (col I).
If no row exists for that workflow, appends one.

Auth: SHEETS_TOKEN env var (refresh token JSON) — same pattern as all other scripts.
"""
import os, sys, json, argparse, urllib.request, urllib.parse
from datetime import datetime
import pytz

ET = pytz.timezone("America/New_York")
SHEET_ID = "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg"
TAB = "🔧 Build Tracker"


def _get_token():
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        p = os.path.expanduser("~/ClaudeWorkspace/Credentials/sheets_token.json")
        if os.path.exists(p):
            with open(p) as f:
                raw = f.read()
    if not raw:
        print("  build_tracker_writer: no SHEETS_TOKEN — skipping")
        return ""
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    try:
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
        ).read())
        return resp.get("access_token", "")
    except Exception as e:
        print(f"  build_tracker_writer: token refresh failed: {e}")
        return ""


def _sheets_get(token, range_name):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
           f"{urllib.parse.quote(range_name)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read())


def _sheets_put(token, range_name, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
           f"{urllib.parse.quote(range_name)}?valueInputOption=USER_ENTERED")
    body = json.dumps({"values": values}).encode()
    req = urllib.request.Request(url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return json.loads(urllib.request.urlopen(req).read())


def _sheets_append(token, range_name, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
           f"{urllib.parse.quote(range_name)}:append"
           f"?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    body = json.dumps({"values": values}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return json.loads(urllib.request.urlopen(req).read())


def update_build_tracker(workflow_file: str, status: str, error: str = ""):
    token = _get_token()
    if not token:
        return

    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    status_cell = "✅ success" if status == "success" else f"🔴 {status}" + (f" — {error[:120]}" if error else "")

    # Read all rows to find matching workflow file (col G = index 6)
    try:
        data = _sheets_get(token, f"'{TAB}'!A1:I200")
    except Exception as e:
        print(f"  build_tracker_writer: read failed: {e}")
        return

    rows = data.get("values", [])
    match_row = None
    for i, row in enumerate(rows, 1):
        if len(row) >= 7 and row[6].strip().lower() == workflow_file.strip().lower():
            match_row = i
            break

    if match_row:
        # Update H (Last Run) and I (Last Status) for found row
        try:
            _sheets_put(token, f"'{TAB}'!H{match_row}:I{match_row}", [[now, status_cell]])
            print(f"  build_tracker_writer: updated row {match_row} for {workflow_file} → {status_cell}")
        except Exception as e:
            print(f"  build_tracker_writer: update failed: {e}")
    else:
        # Append new row
        try:
            _sheets_append(token, f"'{TAB}'!A1", [[
                workflow_file, "GitHub Actions", status_cell, "", "",
                "", workflow_file, now, status_cell
            ]])
            print(f"  build_tracker_writer: appended new row for {workflow_file}")
        except Exception as e:
            print(f"  build_tracker_writer: append failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True, help="Workflow filename e.g. content_creator.yml")
    parser.add_argument("--status", required=True, help="success / failure / cancelled")
    parser.add_argument("--error", default="", help="Optional error message (first line)")
    args = parser.parse_args()

    update_build_tracker(args.workflow, args.status, args.error)


if __name__ == "__main__":
    main()
