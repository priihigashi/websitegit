#!/usr/bin/env python3
"""
setup_personal_workspace.py
============================
One-shot script to create the full Personal workspace in Google Drive.

CREATES:
  1. Personal/ folder (in Claude Code Workspace)
  2. Book Tracking spreadsheet — with columns for price, Audible, rating, etc.
     Seeds: "A People's History of the United States" by Howard Zinn
  3. Merch Planning spreadsheet — tabs: Mugs, T-Shirts, Hats
     Organized by political ideology / topic category, US + Brazil markets
     Seeds: "Anti Christian Nationalist Club" mug
  4. Saves @getbetterwithbooks reel to Inspiration Library with credits
  5. Creates 2 Google Calendar events for Thursday to extract ideas from TickTick

USAGE:
  python scripts/setup_personal_workspace.py

REQUIRED ENV VARS:
  GOOGLE_SA_KEY   — base64-encoded service account JSON
  SHEETS_TOKEN    — OAuth token JSON (for Calendar — service account can't
                    create events on personal calendar; uses OAuth token)
"""

import os
import sys
import json
import base64
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── CONFIG ──────────────────────────────────────────────────────────────────

CLAUDE_WORKSPACE_FOLDER_ID = "1prdRT9ejOT-s-kzt0DIZ4QZuerdjv4PP"
IDEAS_INBOX_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"

# Reels to save
REELS_TO_SAVE = [
    {
        "url": "https://www.instagram.com/reel/DW7ecGnDV3s/",
        "creator": "@getbetterwithbooks",
        "niche": "Oak Park",
        "notes": (
            "Howard Zinn - A People's History of the United States. "
            "Verify factual claims (effects only, not opinions). "
            "Credit @getbetterwithbooks in caption."
        ),
        "hook": "What exactly are we defending?",
        "credits": "Credit: @getbetterwithbooks | Source: Instagram Reel | Verify facts before reposting",
    },
    {
        "url": "https://www.instagram.com/reel/DW4QK8LDbRC/",
        "creator": "",
        "niche": "Brazil",
        "notes": "Brazil content — captured via Claude Code session 2026-04-12",
        "hook": "",
        "credits": "",
    },
]

# Calendar — Thursday April 16, 2026
THURSDAY_DATE = "2026-04-16"


# ─── AUTH ────────────────────────────────────────────────────────────────────

def _get_sa_creds():
    """Service account creds for Drive + Sheets."""
    from google.oauth2.service_account import Credentials
    sa_b64 = os.getenv("GOOGLE_SA_KEY")
    if not sa_b64:
        raise RuntimeError("GOOGLE_SA_KEY not set")
    sa_info = json.loads(base64.b64decode(sa_b64))
    return Credentials.from_service_account_info(sa_info, scopes=[
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ])


def _get_oauth_creds():
    """OAuth creds for Calendar (personal calendar needs user token)."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    token_str = os.getenv("SHEETS_TOKEN")
    if not token_str:
        return None
    token_data = json.loads(token_str)
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", []),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def create_folder(drive, name, parent_id):
    resp = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder",
              "parents": [parent_id]},
        supportsAllDrives=True, fields="id,webViewLink",
    ).execute()
    url = resp.get("webViewLink", "")
    print(f"  Created folder: {name} → {url}")
    return resp["id"], url


def create_spreadsheet(drive, name, folder_id):
    resp = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.spreadsheet",
              "parents": [folder_id]},
        supportsAllDrives=True, fields="id,webViewLink",
    ).execute()
    url = resp.get("webViewLink", "")
    print(f"  Created spreadsheet: {name} → {url}")
    return resp["id"], url


def bold_freeze_header(sheets, spreadsheet_id, sheet_gid):
    """Bold header row + freeze it."""
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [
            {"repeatCell": {
                "range": {"sheetId": sheet_gid, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
                }},
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }},
            {"updateSheetProperties": {
                "properties": {"sheetId": sheet_gid,
                               "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
        ]},
    ).execute()


# ─── 1. BOOK TRACKING ───────────────────────────────────────────────────────

BOOK_HEADERS = [
    "Title", "Author", "Genre / Category", "Pages", "Year Published",
    "Paperback Price", "Kindle Price", "Audible Available", "Audible Price",
    "Audible Length", "Narrator", "Goodreads Rating", "# of Ratings",
    "Why Read This", "Key Topics / Themes", "Difficulty Level",
    "Status", "My Rating", "Notes", "Date Added", "Source / Who Recommended",
]

HOWARD_ZINN_BOOK = [
    "A People's History of the United States",
    "Howard Zinn",
    "History / Political Science / Non-Fiction",
    "729",
    "1980 (updated 2003)",
    "$15-$20",
    "$12.99",
    "Yes",
    "$35 (or 1 credit)",
    "34 hours",
    "Jeff Zinn (author's son)",
    "4.1/5",
    "~190,000+",
    "Tells US history from the perspective of marginalized groups — "
    "workers, Native Americans, enslaved people, immigrants. "
    "Challenges the standard 'great men' narrative. Controversial but "
    "widely assigned in universities.",
    "Columbus & colonization, slavery & resistance, labor movements, "
    "Civil Rights, anti-war movements, class struggle, women's rights",
    "Moderate (accessible writing, college-level content)",
    "Want to Read",
    "",
    "Recommended by @getbetterwithbooks reel. Book is debated — "
    "some historians praise its perspective, others criticize "
    "cherry-picking sources. Best read alongside traditional histories.",
    datetime.now().strftime("%Y-%m-%d"),
    "@getbetterwithbooks (Instagram Reel)",
]


def setup_book_tracking(drive, sheets, folder_id):
    print("\n[BOOK TRACKING] Creating spreadsheet...")
    sheet_id, url = create_spreadsheet(drive, "Book Tracking", folder_id)

    # Get default sheet GID
    props = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    gid = props["sheets"][0]["properties"]["sheetId"]

    # Rename tab + write headers
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [
            {"updateSheetProperties": {
                "properties": {"sheetId": gid, "title": "📚 Reading List"},
                "fields": "title",
            }},
        ]},
    ).execute()

    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="📚 Reading List!A1",
        valueInputOption="RAW", body={"values": [BOOK_HEADERS]},
    ).execute()

    bold_freeze_header(sheets, sheet_id, gid)

    # Seed Howard Zinn
    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id, range="📚 Reading List!A2",
        valueInputOption="RAW", body={"values": [HOWARD_ZINN_BOOK]},
    ).execute()
    print("  Seeded: A People's History of the United States by Howard Zinn")

    return sheet_id, url


# ─── 2. MERCH PLANNING ──────────────────────────────────────────────────────

MERCH_HEADERS = [
    "Saying / Design",
    "Category",          # Political Ideology, Humor, Motivational, Brazilian Culture
    "Market",            # US, Brazil, Both
    "Price Point",
    "Cost to Produce",
    "Supplier / Platform",  # Printful, Etsy, etc.
    "Design Status",     # Idea, Designed, Ready to List, Listed
    "Mockup Link",
    "Notes",
    "Date Added",
    "Source / Inspiration",
]

MERCH_TABS = [
    ("🍵 Mugs", MERCH_HEADERS),
    ("👕 T-Shirts", MERCH_HEADERS),
    ("🧢 Hats", MERCH_HEADERS),
    ("💡 Ideas Inbox", [
        "Idea", "Product Type", "Category", "Market", "Notes",
        "Date Added", "Source",
    ]),
]

FIRST_MUG = [
    "Anti Christian Nationalist Club",
    "Political Ideology",
    "US",
    "",
    "",
    "",
    "Idea",
    "",
    "",
    datetime.now().strftime("%Y-%m-%d"),
    "",
]


def setup_merch_planning(drive, sheets, folder_id):
    print("\n[MERCH PLANNING] Creating spreadsheet...")
    sheet_id, url = create_spreadsheet(drive, "Merch Planning", folder_id)

    props = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    default_gid = props["sheets"][0]["properties"]["sheetId"]

    # Create all tabs (rename first, add rest)
    requests_list = []

    # Rename default sheet to first tab
    requests_list.append({
        "updateSheetProperties": {
            "properties": {"sheetId": default_gid, "title": MERCH_TABS[0][0]},
            "fields": "title",
        }
    })

    # Add remaining tabs
    for i, (tab_name, _) in enumerate(MERCH_TABS[1:], start=1):
        requests_list.append({
            "addSheet": {"properties": {"title": tab_name, "index": i}}
        })

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id, body={"requests": requests_list},
    ).execute()

    # Get updated sheet GIDs
    props = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    tab_gids = {}
    for s in props["sheets"]:
        tab_gids[s["properties"]["title"]] = s["properties"]["sheetId"]

    # Write headers to each tab
    for tab_name, headers in MERCH_TABS:
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"'{tab_name}'!A1",
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()
        bold_freeze_header(sheets, sheet_id, tab_gids[tab_name])
        print(f"  Tab ready: {tab_name}")

    # Seed first mug
    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id, range=f"'🍵 Mugs'!A2",
        valueInputOption="RAW", body={"values": [FIRST_MUG]},
    ).execute()
    print("  Seeded mug: Anti Christian Nationalist Club")

    return sheet_id, url


# ─── 3. SAVE REEL TO INSPIRATION LIBRARY ────────────────────────────────────

def save_reels_to_inspiration_library(sheets):
    print("\n[REEL CAPTURE] Saving reels to Inspiration Library...")
    try:
        import gspread
        sa_b64 = os.getenv("GOOGLE_SA_KEY")
        sa_info = json.loads(base64.b64decode(sa_b64))
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(IDEAS_INBOX_ID)
        lib = sh.worksheet("📥 Inspiration Library")

        for reel in REELS_TO_SAVE:
            lib.append_row([
                datetime.now().strftime("%Y-%m-%d"),
                reel["url"],
                reel["notes"][:200],
                reel["niche"],
                "Talking Head/Expert",
                "SAVED",
                reel["notes"],
                reel["hook"],
                reel["credits"],
            ])
            print(f"  Saved: {reel['url']} (niche: {reel['niche']}, credit: {reel['creator']})")

    except Exception as e:
        print(f"  WARNING reel save: {e}")
        for reel in REELS_TO_SAVE:
            print(f"  Manual entry needed: {reel['url']} | {reel['creator']}")


# ─── 4. CALENDAR EVENTS ─────────────────────────────────────────────────────

def create_calendar_events():
    print("\n[CALENDAR] Creating Thursday events...")
    oauth_creds = _get_oauth_creds()
    if not oauth_creds:
        print("  SKIP Calendar: SHEETS_TOKEN not set (needed for personal calendar)")
        print("  Trigger manually via GitHub Actions → Google Calendar — Create Event:")
        print(f"    Event 1: 'Extract Merch Ideas from TickTick' | {THURSDAY_DATE} | 10:00-11:00")
        print(f"    Event 2: 'Extract Book List from TickTick' | {THURSDAY_DATE} | 11:00-12:00")
        return

    from googleapiclient.discovery import build
    cal = build("calendar", "v3", credentials=oauth_creds)

    events = [
        {
            "summary": "Extract Merch Ideas from TickTick → Merch Planning Spreadsheet",
            "description": (
                "TASK: Go through TickTick merch ideas list\n\n"
                "STEPS:\n"
                "1. Open TickTick → find merch ideas list\n"
                "2. Open Merch Planning spreadsheet (Personal folder)\n"
                "3. Sort each idea into the right tab: 🍵 Mugs, 👕 T-Shirts, or 🧢 Hats\n"
                "4. Categorize by: Political Ideology, Humor, Brazilian Culture, Motivational\n"
                "5. Mark market: US, Brazil, or Both\n\n"
                "First mug already seeded: 'Anti Christian Nationalist Club'\n\n"
                "Created by Claude Code automation"
            ),
            "start": {"dateTime": f"{THURSDAY_DATE}T10:00:00", "timeZone": "America/New_York"},
            "end":   {"dateTime": f"{THURSDAY_DATE}T11:00:00", "timeZone": "America/New_York"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
        },
        {
            "summary": "Extract Book List from TickTick → Book Tracking Spreadsheet",
            "description": (
                "TASK: Go through TickTick book list\n\n"
                "STEPS:\n"
                "1. Open TickTick → find book list / reading list\n"
                "2. Open Book Tracking spreadsheet (Personal folder)\n"
                "3. For each book, fill in: Title, Author, Genre, Pages, Prices\n"
                "4. Check Audible availability + price for each\n"
                "5. Look up Goodreads rating\n"
                "6. Write a short 'Why Read This' for each\n\n"
                "First book already seeded: 'A People's History of the United States' "
                "by Howard Zinn (from @getbetterwithbooks reel)\n\n"
                "Created by Claude Code automation"
            ),
            "start": {"dateTime": f"{THURSDAY_DATE}T11:00:00", "timeZone": "America/New_York"},
            "end":   {"dateTime": f"{THURSDAY_DATE}T12:00:00", "timeZone": "America/New_York"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
        },
    ]

    for event_body in events:
        result = cal.events().insert(calendarId="primary", body=event_body).execute()
        print(f"  Created: {result.get('summary')}")
        print(f"    Link: {result.get('htmlLink')}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    from googleapiclient.discovery import build
    creds = _get_sa_creds()
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    print(f"\n{'='*60}")
    print("PERSONAL WORKSPACE SETUP")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # 1. Create Personal folder
    print("\n[FOLDER] Creating 'Personal' folder...")
    personal_id, folder_url = create_folder(drive, "Personal", CLAUDE_WORKSPACE_FOLDER_ID)

    # 2. Book Tracking
    book_sheet_id, book_url = setup_book_tracking(drive, sheets, personal_id)

    # 3. Merch Planning
    merch_sheet_id, merch_url = setup_merch_planning(drive, sheets, personal_id)

    # 4. Save reel to Inspiration Library
    save_reels_to_inspiration_library(sheets)

    # 5. Calendar events for Thursday
    create_calendar_events()

    # Summary
    print(f"\n{'='*60}")
    print("ALL DONE")
    print(f"{'='*60}")
    print(f"Personal folder:    {folder_url}")
    print(f"Book Tracking:      {book_url}")
    print(f"Merch Planning:     {merch_url}")
    print(f"Reel saved:         {REEL_URL} (credit: {REEL_CREATOR})")
    print(f"Calendar events:    Thursday {THURSDAY_DATE} @ 10am + 11am ET")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
