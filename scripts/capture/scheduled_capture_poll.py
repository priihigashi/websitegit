"""
Scheduled capture poller — Phase 1 of the Drop Links flow.

Reads new URLs from the Ideas & Inbox spreadsheet (🎯 Drop Links tab, falling
back to 📥 Inspiration Library) and dispatches `capture_pipeline.yml` for each
unprocessed URL, writing the status + run URL back to the sheet.

Env vars:
  SHEETS_TOKEN              — OAuth refresh token JSON (required for Sheets)
  GITHUB_TOKEN              — token with `repo` scope to dispatch workflows
  GITHUB_REPOSITORY         — owner/repo (e.g. priihigashi/oak-park-ai-hub)
  CAPTURE_SHEET_ID          — default: 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU
  CAPTURE_DROP_TAB          — default: '🎯 Drop Links'
  CAPTURE_FALLBACK_TAB      — default: '📥 Inspiration Library'
  CAPTURE_MAX_DISPATCH      — default: 5 (soft cap per run to protect quota)

Sheet columns used (1-indexed):
  A = URL
  C = Status           (we write "Queued" after dispatch)
  D = Date / Dispatched timestamp (we write ISO timestamp)
  E = Niche override (optional; maps to project arg)
"""
import os
import sys
import json
import time
import urllib.request, urllib.parse
import requests
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SHEET_ID = os.getenv("CAPTURE_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
DROP_TAB = os.getenv("CAPTURE_DROP_TAB", "🎯 Drop Links")
FALLBACK_TAB = os.getenv("CAPTURE_FALLBACK_TAB", "📥 Inspiration Library")
MAX_DISPATCH = int(os.getenv("CAPTURE_MAX_DISPATCH", "5"))

REPO = os.getenv("GITHUB_REPOSITORY", "priihigashi/oak-park-ai-hub")
GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
WORKFLOW_FILE = "capture_pipeline.yml"
REF = "main"

NICHE_TO_PROJECT = {
    "brazil news": "sovereign",
    "usa news": "sovereign",
    "news": "sovereign",
    "opc": "content",
    "higashi": "content",
    "ugc": "content",
    "ai content": "content",
    "stocks": "book",
}

SKIP_STATUS = {"captured", "queued", "error", "skip", "done"}


def _sheets():
    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        print("ERROR: SHEETS_TOKEN not set")
        sys.exit(1)
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    creds = Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )
    return build("sheets", "v4", credentials=creds)


def _read_rows(svc, tab: str):
    try:
        resp = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{tab}'!A2:O"
        ).execute()
        return resp.get("values", [])
    except Exception as e:
        print(f"  could not read tab '{tab}': {e}")
        return None


def _dispatch(url: str, project: str) -> tuple[bool, str]:
    """Fire workflow_dispatch. Returns (ok, run_url_or_error)."""
    if not GH_TOKEN:
        return False, "GITHUB_TOKEN not set"
    api = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    r = requests.post(
        api,
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json={"ref": REF, "inputs": {"url": url, "project": project}},
        timeout=15,
    )
    if r.status_code not in (201, 204):
        return False, f"HTTP {r.status_code} {r.text[:160]}"
    # GitHub doesn't return the run ID from dispatch; link to the workflow page
    return True, f"https://github.com/{REPO}/actions/workflows/{WORKFLOW_FILE}"


def _project_for(niche: str) -> str:
    return NICHE_TO_PROJECT.get((niche or "").strip().lower(), "content")


def _write_status(svc, tab: str, row: int, status: str, note: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"'{tab}'!C{row}", "values": [[status]]},
                {"range": f"'{tab}'!D{row}", "values": [[f"{ts} — {note}"]]},
            ],
        },
    ).execute()


def main():
    svc = _sheets()

    rows = _read_rows(svc, DROP_TAB)
    active_tab = DROP_TAB
    if rows is None:
        print(f"Drop tab missing — falling back to '{FALLBACK_TAB}'")
        rows = _read_rows(svc, FALLBACK_TAB) or []
        active_tab = FALLBACK_TAB

    print(f"Scanning {len(rows)} rows in '{active_tab}' (max dispatch: {MAX_DISPATCH})")

    dispatched = 0
    for i, row in enumerate(rows):
        if dispatched >= MAX_DISPATCH:
            print("Dispatch cap reached; stopping.")
            break
        row = row + [""] * (15 - len(row))
        # Inspiration Library has URL in col D (index 3); Drop Links has URL in col A (index 0)
        if active_tab == FALLBACK_TAB:
            url    = row[3].strip()   # col D = URL
            status = row[2].strip().lower()   # col C = Status
            niche  = ""               # Inspiration Library has no dedicated niche column; let pipeline classify
        else:
            url    = row[0].strip()   # col A = URL in Drop Links
            status = row[2].strip().lower()   # col C = Status
            niche  = row[4].strip()   # col E = Niche override
        if not url or not url.startswith("http"):
            if url:
                print(f"  row {i+2}: skipping non-URL in expected col: {url[:40]!r}")
            continue
        if status in SKIP_STATUS:
            continue

        project = _project_for(niche)
        ok, info = _dispatch(url, project)
        sheet_row = i + 2
        if ok:
            _write_status(svc, active_tab, sheet_row, "Queued", info)
            dispatched += 1
            print(f"  row {sheet_row}: Queued ({project}) → {url[:60]}")
            time.sleep(1)
        else:
            _write_status(svc, active_tab, sheet_row, "Error", info[:120])
            print(f"  row {sheet_row}: Error — {info}")

    print(f"Done. Dispatched {dispatched} capture runs.")


if __name__ == "__main__":
    main()
