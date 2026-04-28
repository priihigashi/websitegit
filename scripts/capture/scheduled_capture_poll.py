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
  CAPTURE_DAILY_DISPATCH_LIMIT — default: 6 (total queued by this poller per UTC day)
  CAPTURE_OPC_FIRST_COUNT   — default: 2 (how many OPC rows to prioritize first)

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
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Header-name lookup so column moves can't silently corrupt the sheet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.sheet_schema import INSPO_COLS, make_col_pos  # noqa: E402

SHEET_ID = os.getenv("CAPTURE_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
DROP_TAB = os.getenv("CAPTURE_DROP_TAB", "🎯 Drop Links")
FALLBACK_TAB = os.getenv("CAPTURE_FALLBACK_TAB", "📥 Inspiration Library")
MAX_DISPATCH = int(os.getenv("CAPTURE_MAX_DISPATCH", "5"))
DAILY_DISPATCH_LIMIT = int(os.getenv("CAPTURE_DAILY_DISPATCH_LIMIT", "6"))
OPC_FIRST_COUNT = int(os.getenv("CAPTURE_OPC_FIRST_COUNT", "2"))

REPO = os.getenv("GITHUB_REPOSITORY", "priihigashi/oak-park-ai-hub")
GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
WORKFLOW_FILE = "capture_pipeline.yml"
REF = "main"

NICHE_TO_PROJECT = {
    "brazil news": "brazil",
    "brazil": "brazil",
    "usa news": "usa",
    "usa": "usa",
    "news": "brazil",
    "opc": "opc",
    "higashi": "higashi",
    "ugc": "ugc",
    "ai content": "opc",
    "stocks": "stocks",
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
            spreadsheetId=SHEET_ID, range=f"'{tab}'!A2:AC"
        ).execute()
        return resp.get("values", [])
    except Exception as e:
        print(f"  could not read tab '{tab}': {e}")
        return None


def _read_headers(svc, tab: str) -> dict:
    """Return {lowercase_header: index} for the live tab. Empty dict on failure."""
    try:
        resp = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{tab}'!1:1"
        ).execute()
        header_row = resp.get("values", [[]])[0]
        return make_col_pos(header_row)
    except Exception as e:
        print(f"  could not read header of '{tab}': {e}")
        return {}


def _col_letter(idx: int) -> str:
    """0-indexed column number → A1 letter (e.g. 0→A, 27→AB)."""
    s = ""
    n = idx
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


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


def _write_status(svc, tab: str, row: int, status: str, note: str, col_pos: dict):
    """Resolve target columns by header name. Refuses to write if header is missing
    so we never overwrite a URL column by accident again."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    if tab == FALLBACK_TAB:
        status_name = INSPO_COLS["status"]      # 'status'
        note_name   = INSPO_COLS["my_comments"] # 'comments' (col V on current schema)
    else:
        # Drop Links legacy mapping (tab no longer exists, but kept for parity)
        status_name, note_name = "status", "date / dispatched"

    status_idx = col_pos.get(status_name)
    note_idx   = col_pos.get(note_name)
    if status_idx is None or note_idx is None:
        print(f"  WARN: header lookup failed on '{tab}' "
              f"(status='{status_name}'→{status_idx}, note='{note_name}'→{note_idx}) — skipping writeback")
        return

    status_col = _col_letter(status_idx)
    note_col   = _col_letter(note_idx)
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"'{tab}'!{status_col}{row}", "values": [[status]]},
                {"range": f"'{tab}'!{note_col}{row}",   "values": [[f"{ts} — {note}"]]},
            ],
        },
    ).execute()


def _note_column_name(tab: str) -> str:
    if tab == FALLBACK_TAB:
        return INSPO_COLS["my_comments"]
    return "date / dispatched"


def _count_today_queued(rows: list, tab: str, col_pos: dict) -> int:
    """Best-effort count of rows this poller queued today.

    We match rows with Status=Queued and today's UTC date in the note field.
    """
    status_name = INSPO_COLS["status"] if tab == FALLBACK_TAB else "status"
    note_name = _note_column_name(tab)
    status_idx = col_pos.get(status_name)
    note_idx = col_pos.get(note_name)
    if status_idx is None or note_idx is None:
        return 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0
    for row in rows:
        max_idx = max(status_idx, note_idx)
        row = row + [""] * (max_idx + 1 - len(row))
        status = row[status_idx].strip().lower()
        note = row[note_idx].strip().lower()
        if status == "queued" and today in note and WORKFLOW_FILE in note:
            total += 1
    return total


def main():
    svc = _sheets()

    rows = _read_rows(svc, DROP_TAB)
    active_tab = DROP_TAB
    if rows is None:
        print(f"Drop tab missing — falling back to '{FALLBACK_TAB}'")
        rows = _read_rows(svc, FALLBACK_TAB) or []
        active_tab = FALLBACK_TAB

    # Resolve column positions by header name (refuse to operate if missing)
    col_pos = _read_headers(svc, active_tab)
    if active_tab == FALLBACK_TAB:
        url_idx    = col_pos.get(INSPO_COLS["url"])
        status_idx = col_pos.get(INSPO_COLS["status"])
        niche_idx  = col_pos.get(INSPO_COLS["niche"])
    else:
        # Drop Links legacy mapping (tab no longer exists)
        url_idx, status_idx, niche_idx = (
            col_pos.get("url"), col_pos.get("status"), col_pos.get("niche override")
        )
    if url_idx is None or status_idx is None or niche_idx is None:
        print(f"ERROR: required headers missing on '{active_tab}' "
              f"(url={url_idx}, status={status_idx}, niche={niche_idx}). Aborting.")
        sys.exit(1)

    today_queued = _count_today_queued(rows, active_tab, col_pos)
    remaining_today = max(0, DAILY_DISPATCH_LIMIT - today_queued)
    run_cap = min(MAX_DISPATCH, remaining_today)

    print(f"Scanning {len(rows)} rows in '{active_tab}' (max dispatch: {MAX_DISPATCH})")
    print(f"  Header positions: url=col {_col_letter(url_idx)}, "
          f"status=col {_col_letter(status_idx)}, niche=col {_col_letter(niche_idx)}")
    print(f"  Daily cap: {DAILY_DISPATCH_LIMIT} | already queued today: {today_queued} | remaining today: {remaining_today}")

    if run_cap <= 0:
        print("Daily dispatch cap reached; nothing to do.")
        print("Done. Dispatched 0 capture runs.")
        return

    candidates = []
    for i, row in enumerate(rows):
        max_idx = max(url_idx, status_idx, niche_idx)
        row = row + [""] * (max_idx + 1 - len(row))
        url = row[url_idx].strip()
        status = row[status_idx].strip().lower()
        niche = row[niche_idx].strip()
        if not url or not url.startswith("http"):
            if url:
                print(f"  row {i+2}: skipping non-URL in {INSPO_COLS['url']!r} col: {url[:40]!r}")
            continue
        if status in SKIP_STATUS:
            continue
        project = _project_for(niche)
        candidates.append({
            "row_i": i,
            "sheet_row": i + 2,
            "url": url,
            "niche": niche,
            "project": project,
        })

    opc_first = [c for c in candidates if c["project"] == "opc"][:OPC_FIRST_COUNT]
    opc_sheet_rows = {c["sheet_row"] for c in opc_first}
    ordered_rest = [c for c in candidates if c["sheet_row"] not in opc_sheet_rows]
    to_dispatch = (opc_first + ordered_rest)[:run_cap]

    print(
        f"  Selection: OPC-first={len(opc_first)} (target {OPC_FIRST_COUNT}), "
        f"then row order. Dispatching up to {len(to_dispatch)} this run."
    )

    dispatched = 0
    for item in to_dispatch:
        if dispatched >= run_cap:
            print("Dispatch cap reached; stopping.")
            break
        url = item["url"]
        project = item["project"]
        ok, info = _dispatch(url, project)
        sheet_row = item["sheet_row"]
        if ok:
            _write_status(svc, active_tab, sheet_row, "Queued", info, col_pos)
            dispatched += 1
            print(f"  row {sheet_row}: Queued ({project}) → {url[:60]}")
            time.sleep(1)
        else:
            _write_status(svc, active_tab, sheet_row, "Error", info[:120], col_pos)
            print(f"  row {sheet_row}: Error — {info}")

    print(f"Done. Dispatched {dispatched} capture runs.")


if __name__ == "__main__":
    main()
