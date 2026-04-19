"""
content_tracker.py
==================
Shared utility — appends one row to '📊 Content Creation Log' tab every time
a pipeline runs. Import and call log_run() at the end of any script.

Tab columns (A–M):
  A  DATE         YYYY-MM-DD
  B  TIME_ET      HH:MM ET
  C  PIPELINE     capture_pipeline | capture_queue | content_creator |
                  inspiration_scraper | photo_catalog | 4am_agent
  D  TRIGGER      manual | scheduled | queue | 4am | webhook
  E  URL          source URL (or empty)
  F  NICHE        Brazil | OPC | UGC | News | Stocks | (empty)
  G  PROJECT      content | sovereign | book | (empty)
  H  STATUS       success | failed | pending | skipped | queued
  I  SCORE        1-5 (empty if not applicable)
  J  DRIVE_PATH   folder or doc URL where content landed
  K  BRIEF_URL    content brief / Google Doc URL
  L  GH_RUN_URL   GitHub Actions run URL (auto-detected from GITHUB_SERVER_URL + GITHUB_RUN_ID)
  M  NOTES        error message, extra info, etc.

Usage:
    from content_tracker import log_run

    log_run(
        pipeline="capture_pipeline",
        trigger="manual",
        url="https://www.instagram.com/reel/...",
        niche="Brazil",
        project="sovereign",
        status="success",
        score=4,
        drive_path="https://drive.google.com/...",
        brief_url="https://docs.google.com/...",
        notes="",
    )

All fields except pipeline + status are optional — pass what you have.
Non-fatal: if Sheets API fails, prints a warning and continues. Never crashes the caller.
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import datetime
import pytz

SHEET_ID  = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
TAB_NAME  = "📊 Content Creation Log"
ET        = pytz.timezone("America/New_York")


def _access_token() -> str:
    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        return ""
    try:
        td = json.loads(raw)
        data = urllib.parse.urlencode({
            "client_id":     td["client_id"],
            "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"],
            "grant_type":    "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
        ).read())
        return resp.get("access_token", "")
    except Exception:
        return ""


def _gh_run_url() -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo   = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def log_run(
    pipeline: str,
    status: str,
    trigger: str = "scheduled",
    url: str = "",
    niche: str = "",
    project: str = "",
    score: int | None = None,
    drive_path: str = "",
    brief_url: str = "",
    notes: str = "",
) -> bool:
    """
    Append one row to Content Creation Log.
    Returns True on success, False on failure (non-fatal either way).
    """
    token = _access_token()
    if not token:
        print("[content_tracker] SKIP — no SHEETS_TOKEN")
        return False

    now_et = datetime.now(ET)
    row = [
        now_et.strftime("%Y-%m-%d"),          # A DATE
        now_et.strftime("%H:%M"),              # B TIME_ET
        pipeline,                              # C PIPELINE
        trigger,                               # D TRIGGER
        url[:200] if url else "",              # E URL
        niche,                                 # F NICHE
        project,                               # G PROJECT
        status,                                # H STATUS
        score if score is not None else "",    # I SCORE
        drive_path[:300] if drive_path else "", # J DRIVE_PATH
        brief_url[:300] if brief_url else "",   # K BRIEF_URL
        _gh_run_url(),                          # L GH_RUN_URL
        notes[:500] if notes else "",           # M NOTES
    ]

    enc = urllib.parse.quote(f"'{TAB_NAME}'!A:M", safe="!:'")
    api_url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
        f"/values/{enc}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    )
    body = json.dumps({"values": [row]}).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                api_url, data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        ).read()
        print(f"[content_tracker] ✓ logged — {pipeline} / {status}")
        return True
    except Exception as e:
        print(f"[content_tracker] WARNING — could not log: {e}")
        return False


# ── Routing — single source of truth ─────────────────────────────────────────
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).parent))
try:
    from routing import content_control as _content_control
except ImportError:
    def _content_control(niche):  # fallback if routing.py not importable
        return ("1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg", "🎬 In Production")

# ── In Production tab (Content Control sheet) ─────────────────────────────────
_CC_SHEET_ID    = "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg"
_IN_PROD_TAB    = "🎬 In Production"


def update_in_production(
    title: str,
    content_type: str,
    status: str,
    drive_folder_link: str,
    output_link: str = "",
    caption: str = "",
    date_created: str = "",
    fmt: str = "",
    post_type: str = "",
) -> bool:
    """
    Write or update a row in '🎬 In Production' tab (OPC).
    Columns: #Reviews | Title | Post Type | Format | Content Type | Status | Drive Folder Link | Caption | Hashtags | Output Link | Date Created
    Status lifecycle: Built → Approved / Needs Revision → Scheduled → Published
    Deduplicates by Drive Folder Link (col G = index 6). Auto-increments # Reviews on each update.
    Non-fatal — never crashes caller.
    """
    token = _access_token()
    if not token:
        print("[content_tracker] SKIP update_in_production — no SHEETS_TOKEN")
        return False

    if not date_created:
        date_created = datetime.now(ET).strftime("%Y-%m-%d")

    enc = urllib.parse.quote(f"'{_IN_PROD_TAB}'!A:K", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{_CC_SHEET_ID}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        ).read()).get("values", [])
    except Exception as e:
        print(f"[content_tracker] WARNING update_in_production read: {e}")
        rows = []

    # Deduplicate by Drive Folder Link (col G = index 6)
    existing_row_num = None
    existing_reviews = 1
    for i, row in enumerate(rows):
        if len(row) > 6 and row[6].strip() == drive_folder_link.strip():
            existing_row_num = i + 1
            try:
                existing_reviews = int(row[0]) if row[0] else 1
            except (ValueError, IndexError):
                existing_reviews = 1
            break

    try:
        if existing_row_num:
            batch = [
                {"range": f"'{_IN_PROD_TAB}'!A{existing_row_num}", "values": [[existing_reviews + 1]]},
                {"range": f"'{_IN_PROD_TAB}'!F{existing_row_num}", "values": [[status]]},
            ]
            if output_link:
                batch.append({"range": f"'{_IN_PROD_TAB}'!J{existing_row_num}", "values": [[output_link]]})
            if caption:
                batch.append({"range": f"'{_IN_PROD_TAB}'!H{existing_row_num}", "values": [[caption]]})
            if fmt:
                batch.append({"range": f"'{_IN_PROD_TAB}'!D{existing_row_num}", "values": [[fmt]]})
            payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": batch}).encode()
            urllib.request.urlopen(urllib.request.Request(
                f"https://sheets.googleapis.com/v4/spreadsheets/{_CC_SHEET_ID}/values:batchUpdate",
                data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )).read()
            print(f"[content_tracker] ✓ In Production updated — {title[:40]} → {status} (reviews: {existing_reviews+1})")
        else:
            # New row: #Reviews=1 | Title | PostType | Format | ContentType | Status | DriveLink | Caption | Hashtags | OutputLink | Date
            new_row = [1, title, post_type, fmt, content_type, status, drive_folder_link, caption, "", output_link, date_created]
            enc2 = urllib.parse.quote(f"'{_IN_PROD_TAB}'!A:K", safe="!:'")
            url2 = (f"https://sheets.googleapis.com/v4/spreadsheets/{_CC_SHEET_ID}/values/{enc2}"
                    f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
            payload = json.dumps({"values": [new_row]}).encode()
            urllib.request.urlopen(urllib.request.Request(
                url2, data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )).read()
            print(f"[content_tracker] ✓ In Production added — {title[:40]} → {status}")
        return True
    except Exception as e:
        print(f"[content_tracker] WARNING update_in_production write: {e}")
        return False


# ── News In Production tabs (Brazil / USA — News Content Control sheet) ────────
_NEWS_CC_SHEET_ID  = "1QFHa_xcuLOqbbYbtzeMVhb5ypfHIbAkVJyyInCKlgcM"  # News — Content Control
_NEWS_BRAZIL_TAB   = "🇧🇷 Brazil In Production"
_NEWS_USA_TAB      = "🇺🇸 USA In Production"


def update_news_in_production(
    title: str,
    niche: str,
    content_type: str,
    status: str,
    drive_folder_link: str,
    output_link: str = "",
    caption: str = "",
    date_created: str = "",
    fmt: str = "",
    post_type: str = "",
) -> bool:
    """
    Write or update a row in the News — Content Control spreadsheet.
    Routes Brazil niche → '🇧🇷 Brazil In Production', all others → '🇺🇸 USA In Production'.
    Columns match OPC exactly: #Reviews | Title | Post Type | Format | Content Type | Status |
                                Drive Folder Link | Caption | Hashtags | Output Link | Date Created
    Deduplicates by Drive Folder Link (col G = index 6). Auto-increments # Reviews.
    Non-fatal — never crashes caller.
    """
    token = _access_token()
    if not token:
        print("[content_tracker] SKIP update_news_in_production — no SHEETS_TOKEN")
        return False

    if not date_created:
        date_created = datetime.now(ET).strftime("%Y-%m-%d")

    tab = _NEWS_BRAZIL_TAB if niche.lower() == "brazil" else _NEWS_USA_TAB

    enc = urllib.parse.quote(f"'{tab}'!A:K", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{_NEWS_CC_SHEET_ID}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        ).read()).get("values", [])
    except Exception as e:
        print(f"[content_tracker] WARNING update_news_in_production read: {e}")
        rows = []

    # Deduplicate by Drive Folder Link (col G = index 6, same as OPC)
    existing_row_num = None
    existing_reviews = 1
    for i, row in enumerate(rows):
        if len(row) > 6 and row[6].strip() == drive_folder_link.strip():
            existing_row_num = i + 1
            try:
                existing_reviews = int(row[0]) if row[0] else 1
            except (ValueError, IndexError):
                existing_reviews = 1
            break

    try:
        if existing_row_num:
            batch = [
                {"range": f"'{tab}'!A{existing_row_num}", "values": [[existing_reviews + 1]]},
                {"range": f"'{tab}'!F{existing_row_num}", "values": [[status]]},
            ]
            if output_link:
                batch.append({"range": f"'{tab}'!J{existing_row_num}", "values": [[output_link]]})
            if caption:
                batch.append({"range": f"'{tab}'!H{existing_row_num}", "values": [[caption]]})
            if fmt:
                batch.append({"range": f"'{tab}'!D{existing_row_num}", "values": [[fmt]]})
            payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": batch}).encode()
            urllib.request.urlopen(urllib.request.Request(
                f"https://sheets.googleapis.com/v4/spreadsheets/{_NEWS_CC_SHEET_ID}/values:batchUpdate",
                data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )).read()
            print(f"[content_tracker] ✓ News In Production updated — {title[:40]} → {status} (reviews: {existing_reviews+1})")
        else:
            # New row: same column order as OPC
            # #Reviews | Title | PostType | Format | ContentType | Status | DriveLink | Caption | Hashtags | OutputLink | Date
            new_row = [1, title, post_type, fmt, content_type, status, drive_folder_link, caption, "", output_link, date_created]
            enc2 = urllib.parse.quote(f"'{tab}'!A:K", safe="!:'")
            url2 = (f"https://sheets.googleapis.com/v4/spreadsheets/{_NEWS_CC_SHEET_ID}/values/{enc2}"
                    f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
            payload = json.dumps({"values": [new_row]}).encode()
            urllib.request.urlopen(urllib.request.Request(
                url2, data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )).read()
            print(f"[content_tracker] ✓ News In Production added — {title[:40]} ({tab}) → {status}")
        return True
    except Exception as e:
        print(f"[content_tracker] WARNING update_news_in_production write: {e}")
        return False


# ── Generic router — works for ALL niches via routing.py ─────────────────────

def update_status_by_niche(
    niche: str,
    title: str,
    content_type: str,
    status: str,
    drive_folder_link: str,
    output_link: str = "",
    caption: str = "",
    date_created: str = "",
    fmt: str = "",
    post_type: str = "",
) -> bool:
    """
    Route to the correct Content Control spreadsheet + tab for ANY niche.
    Uses routing.py as the single source of truth.
    Replaces the niche-specific update_in_production() / update_news_in_production() calls.
    Non-fatal — never crashes caller.
    """
    niche_key = niche.lower().strip()

    # Legacy / canonical mapping handled by routing.py
    ss_id, tab = _content_control(niche_key)
    if not ss_id:
        print(f"[content_tracker] SKIP update_status_by_niche — no content control for niche '{niche}'")
        return False

    token = _access_token()
    if not token:
        print(f"[content_tracker] SKIP update_status_by_niche — no SHEETS_TOKEN")
        return False

    if not date_created:
        date_created = datetime.now(ET).strftime("%Y-%m-%d")

    enc = urllib.parse.quote(f"'{tab}'!A:K", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{ss_id}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        ).read()).get("values", [])
    except Exception as e:
        print(f"[content_tracker] WARNING update_status_by_niche read: {e}")
        rows = []

    existing_row_num = None
    existing_reviews = 1
    for i, row in enumerate(rows):
        if len(row) > 6 and row[6].strip() == drive_folder_link.strip():
            existing_row_num = i + 1
            try:
                existing_reviews = int(row[0]) if row[0] else 1
            except (ValueError, IndexError):
                existing_reviews = 1
            break

    try:
        if existing_row_num:
            batch = [
                {"range": f"'{tab}'!A{existing_row_num}", "values": [[existing_reviews + 1]]},
                {"range": f"'{tab}'!F{existing_row_num}", "values": [[status]]},
            ]
            if output_link:
                batch.append({"range": f"'{tab}'!J{existing_row_num}", "values": [[output_link]]})
            if caption:
                batch.append({"range": f"'{tab}'!H{existing_row_num}", "values": [[caption]]})
            if fmt:
                batch.append({"range": f"'{tab}'!D{existing_row_num}", "values": [[fmt]]})
            payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": batch}).encode()
            urllib.request.urlopen(urllib.request.Request(
                f"https://sheets.googleapis.com/v4/spreadsheets/{ss_id}/values:batchUpdate",
                data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )).read()
            print(f"[content_tracker] ✓ {niche} In Production updated — {title[:40]} → {status} (reviews: {existing_reviews+1})")
        else:
            new_row = [1, title, post_type, fmt, content_type, status,
                       drive_folder_link, caption, "", output_link, date_created]
            enc2 = urllib.parse.quote(f"'{tab}'!A:K", safe="!:'")
            url2 = (f"https://sheets.googleapis.com/v4/spreadsheets/{ss_id}/values/{enc2}"
                    f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
            payload = json.dumps({"values": [new_row]}).encode()
            urllib.request.urlopen(urllib.request.Request(
                url2, data=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )).read()
            print(f"[content_tracker] ✓ {niche} In Production added — {title[:40]} → {status}")
        return True
    except Exception as e:
        print(f"[content_tracker] WARNING update_status_by_niche write: {e}")
        return False
