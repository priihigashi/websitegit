#!/usr/bin/env python3
"""
Drive Map Builder + Daily Scanner
Creates and maintains Google Spreadsheets that log every folder, doc, and sheet
across all shared drives.

Creates 9 spreadsheets total:
  - Drive Map — ALL DRIVES   (master hub, lives in Marketing)
  - Drive Map — OPC          (only OPC items, lives in OPC drive)
  - Drive Map — News         (only News items, lives in News drive)
  - etc. for each shared drive

Usage:
  python drive_map_builder.py --init   # Create all spreadsheets + full scan
  python drive_map_builder.py --scan   # Append new items to all spreadsheets (daily 6 PM)
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ── Credentials ──────────────────────────────────────────────────────────────
TOKEN_FILE = os.environ.get(
    "SHEETS_TOKEN_PATH",
    "/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json",
)

# ── Shared drives to scan ─────────────────────────────────────────────────────
DRIVES = {
    "0AIPzwsJD_qqzUk9PVA": "Marketing",
    "0AJp3Phs0wIBOUk9PVA": "OPC",
    "0AH7_C87G0ZwgUk9PVA": "News",
    "0AF6S_f8PH2_aUk9PVA": "Stocks",
    "0ACJVarTjgmFUUk9PVA": "AI Content",
    "0AEz0NlGr3tlLUk9PVA": "UGC",
    "0AN7aea2IZzE0Uk9PVA": "Higashi",
    "0AAWPgG39HXocUk9PVA": "Big Crazy Ideas",
}

# Where to create the Drive Map spreadsheet (Marketing drive root)
MARKETING_DRIVE_ID = "0AIPzwsJD_qqzUk9PVA"
MARKETING_ROOT_FOLDER = "0AIPzwsJD_qqzUk9PVA"  # root of shared drive = drive ID

# State file — stores the Drive Map spreadsheet ID so scanner can find it
STATE_FILE = os.path.join(os.path.dirname(__file__), "drive_map_state.json")

# ── MIME type → tab mapping ────────────────────────────────────────────────────
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC    = "application/vnd.google-apps.document"
MIME_SHEET  = "application/vnd.google-apps.spreadsheet"

MIME_TO_TAB = {
    MIME_FOLDER: "Folders",
    MIME_DOC:    "Docs",
    MIME_SHEET:  "Sheets",
}

# ── Design constants ──────────────────────────────────────────────────────────
HEADER_BG   = {"red": 0.176, "green": 0.176, "blue": 0.176}   # #2D2D2D
HEADER_FG   = {"red": 1.0,   "green": 1.0,   "blue": 1.0}     # white
ROW_EVEN_BG = {"red": 0.965, "green": 0.965, "blue": 0.965}   # #F6F6F6
ROW_ODD_BG  = {"red": 1.0,   "green": 1.0,   "blue": 1.0}     # white
ID_COL_FG   = {"red": 0.6,   "green": 0.6,   "blue": 0.6}     # gray for ID col

HEADERS = ["NAME", "CATEGORY", "PATH", "DESCRIPTION", "ACTION ITEMS", "COMMENTS", "ITEM ID", "CREATED", "LAST MODIFIED"]
COL_WIDTHS = [260, 130, 370, 320, 200, 200, 190, 120, 130]   # px per column

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_services():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    sheets = build("sheets", "v4", credentials=creds)
    drive  = build("drive",  "v3", credentials=creds)
    return sheets, drive

# ── State helpers ─────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ── Drive helpers ─────────────────────────────────────────────────────────────
_parent_cache = {}   # id → name

def get_item_name(drive_svc, item_id):
    if item_id in _parent_cache:
        return _parent_cache[item_id]
    try:
        f = drive_svc.files().get(
            fileId=item_id,
            fields="name",
            supportsAllDrives=True,
        ).execute()
        _parent_cache[item_id] = f.get("name", item_id)
    except Exception:
        _parent_cache[item_id] = item_id
    return _parent_cache[item_id]

def build_path(drive_svc, parents, drive_label):
    """Recursively build path string from parent IDs. Stops at drive root."""
    if not parents:
        return drive_label
    parts = []
    for pid in parents:
        if pid in DRIVES:
            parts.append(DRIVES[pid])
        else:
            parts.append(get_item_name(drive_svc, pid))
    # Follow first parent up the chain (max 8 levels to avoid infinite loops)
    chain = []
    current_id = parents[0]
    depth = 0
    while current_id and depth < 8:
        if current_id in DRIVES:
            chain.insert(0, DRIVES[current_id])
            break
        name = get_item_name(drive_svc, current_id)
        chain.insert(0, name)
        try:
            f = drive_svc.files().get(
                fileId=current_id,
                fields="parents",
                supportsAllDrives=True,
            ).execute()
            plist = f.get("parents", [])
            current_id = plist[0] if plist else None
        except Exception:
            break
        depth += 1
    return " > ".join(chain) if chain else drive_label

def list_all_files(drive_svc, drive_id, drive_label, mime_types):
    """List all files of given mime types in a shared drive."""
    results = []
    q_mimes = " or ".join(f"mimeType='{m}'" for m in mime_types)
    query = f"({q_mimes}) and trashed=false"
    page_token = None

    while True:
        resp = drive_svc.files().list(
            q=query,
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            fields="nextPageToken, files(id, name, mimeType, parents, createdTime, modifiedTime)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()

        for f in resp.get("files", []):
            path = build_path(drive_svc, f.get("parents", []), drive_label)
            created  = f.get("createdTime",  "")[:10] if f.get("createdTime")  else ""
            modified = f.get("modifiedTime", "")[:10] if f.get("modifiedTime") else ""
            results.append({
                "id":       f["id"],
                "name":     f["name"],
                "category": drive_label,
                "path":     path,
                "mime":     f["mimeType"],
                "created":  created,
                "modified": modified,
            })

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return results

# ── Spreadsheet creation ──────────────────────────────────────────────────────
def create_spreadsheet(sheets_svc, drive_svc, title, target_drive_id):
    """Create a Drive Map spreadsheet and move it to the given shared drive."""
    print(f"  Creating: {title}")
    body = {
        "properties": {"title": title},
        "sheets": [
            {"properties": {"title": "Folders", "index": 0}},
            {"properties": {"title": "Docs",    "index": 1}},
            {"properties": {"title": "Sheets",  "index": 2}},
        ],
    }
    ss = sheets_svc.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    ss_id = ss["spreadsheetId"]

    # Move to target shared drive
    file_meta = drive_svc.files().get(
        fileId=ss_id, fields="parents", supportsAllDrives=True
    ).execute()
    prev_parents = ",".join(file_meta.get("parents", []))
    drive_svc.files().update(
        fileId=ss_id,
        addParents=target_drive_id,
        removeParents=prev_parents,
        supportsAllDrives=True,
        fields="id,parents",
    ).execute()
    print(f"    ID: {ss_id}  →  drive: {target_drive_id}")
    return ss_id

def get_sheet_id(sheets_svc, ss_id, tab_name):
    """Get numeric sheetId for a tab name."""
    ss = sheets_svc.spreadsheets().get(spreadsheetId=ss_id).execute()
    for s in ss["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    return None

# ── Formatting ────────────────────────────────────────────────────────────────
def apply_formatting(sheets_svc, ss_id, tab_name, row_count):
    sheet_id = get_sheet_id(sheets_svc, ss_id, tab_name)
    if sheet_id is None:
        return
    n_cols = len(HEADERS)
    requests = []

    # 1. Header row background + text color + bold + font
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": n_cols},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HEADER_BG,
                    "textFormat": {
                        "foregroundColor": HEADER_FG,
                        "bold": True,
                        "fontSize": 10,
                        "fontFamily": "Arial",
                    },
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "CLIP",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }
    })

    # 2. Freeze first row
    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # 3. Alternating row colors (data rows)
    if row_count > 1:
        for i in range(1, row_count):
            bg = ROW_EVEN_BG if i % 2 == 0 else ROW_ODD_BG
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": i, "endRowIndex": i + 1,
                              "startColumnIndex": 0, "endColumnIndex": n_cols},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg,
                            "textFormat": {"fontSize": 9, "fontFamily": "Arial"},
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
                }
            })

    # 4. ITEM ID column (col G = index 6) — smaller, gray text
    if row_count > 1:
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count,
                          "startColumnIndex": 6, "endColumnIndex": 7},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"fontSize": 8, "foregroundColor": ID_COL_FG, "fontFamily": "Roboto Mono"},
                        "wrapStrategy": "CLIP",
                    }
                },
                "fields": "userEnteredFormat(textFormat,wrapStrategy)",
            }
        })

    # 5. Column widths
    for i, w in enumerate(COL_WIDTHS):
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                          "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": w},
                "fields": "pixelSize",
            }
        })

    # 6. Row height — header
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 36},
            "fields": "pixelSize",
        }
    })

    # 7. Add basic filter
    requests.append({
        "setBasicFilter": {
            "filter": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0,
                          "startColumnIndex": 0, "endColumnIndex": n_cols}
            }
        }
    })

    # 8. NAME column bold (col A = index 0)
    if row_count > 1:
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count,
                          "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 9}}},
                "fields": "userEnteredFormat.textFormat",
            }
        })

    if requests:
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=ss_id,
            body={"requests": requests},
        ).execute()

# ── Write rows ────────────────────────────────────────────────────────────────
def write_rows(sheets_svc, ss_id, tab_name, items):
    """Write header + data rows to a tab, sorted by PATH then NAME."""
    sorted_items = sorted(items, key=lambda x: (x["path"].lower(), x["name"].lower()))

    values = [HEADERS]
    for item in sorted_items:
        values.append([
            item["name"],
            item["category"],
            item["path"],
            "",                # DESCRIPTION — blank (to be filled)
            "",                # ACTION ITEMS
            "",                # COMMENTS
            item["id"],
            item["created"],
            item["modified"],
        ])

    sheets_svc.spreadsheets().values().update(
        spreadsheetId=ss_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
    return len(values)

# ── Incremental scan (daily) ──────────────────────────────────────────────────
def get_existing_ids(sheets_svc, ss_id, tab_name):
    """Return set of ITEM IDs already in a tab."""
    try:
        resp = sheets_svc.spreadsheets().values().get(
            spreadsheetId=ss_id,
            range=f"'{tab_name}'!G:G",
        ).execute()
        vals = resp.get("values", [])
        return {row[0] for row in vals[1:] if row}  # skip header
    except Exception:
        return set()

def append_new_rows(sheets_svc, ss_id, tab_name, new_items):
    """Append only items not already in the sheet."""
    existing = get_existing_ids(sheets_svc, ss_id, tab_name)
    to_add = [i for i in new_items if i["id"] not in existing]
    if not to_add:
        print(f"  {tab_name}: no new items")
        return 0
    sorted_new = sorted(to_add, key=lambda x: (x["path"].lower(), x["name"].lower()))
    values = []
    for item in sorted_new:
        values.append([
            item["name"], item["category"], item["path"],
            "", "", "",
            item["id"], item["created"], item["modified"],
        ])
    sheets_svc.spreadsheets().values().append(
        spreadsheetId=ss_id,
        range=f"'{tab_name}'!A:I",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    print(f"  {tab_name}: added {len(to_add)} new items")
    return len(to_add)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true", help="Create spreadsheet + full scan")
    parser.add_argument("--scan", action="store_true", help="Daily scan — append new items only")
    args = parser.parse_args()

    if not args.init and not args.scan:
        print("Use --init (first run) or --scan (daily update)")
        sys.exit(1)

    sheets_svc, drive_svc = get_services()
    state = load_state()

    # ── INIT: create all spreadsheets ────────────────────────────────────────
    if args.init:
        print("Creating spreadsheets...")
        # Master hub (all drives) → lives in Marketing
        master_id = create_spreadsheet(
            sheets_svc, drive_svc,
            "Drive Map — ALL DRIVES",
            MARKETING_DRIVE_ID,
        )
        state["master"] = master_id

        # Per-drive spreadsheets
        state["per_drive"] = {}
        for drive_id, label in DRIVES.items():
            ss_id = create_spreadsheet(
                sheets_svc, drive_svc,
                f"Drive Map — {label}",
                drive_id,
            )
            state["per_drive"][drive_id] = ss_id

        save_state(state)
        print(f"\n9 spreadsheets created.\n")

    master_id = state.get("master")
    per_drive  = state.get("per_drive", {})
    if not master_id:
        print("ERROR: No spreadsheet IDs found. Run --init first.")
        sys.exit(1)

    # ── Scan all drives — collect items per drive ─────────────────────────────
    drive_items = {}   # drive_id → {folders, docs, sheets}
    all_folders, all_docs, all_sheets = [], [], []

    for drive_id, label in DRIVES.items():
        print(f"Scanning {label}...")
        items = list_all_files(
            drive_svc, drive_id, label,
            [MIME_FOLDER, MIME_DOC, MIME_SHEET],
        )
        folders = [i for i in items if i["mime"] == MIME_FOLDER]
        docs    = [i for i in items if i["mime"] == MIME_DOC]
        sheets  = [i for i in items if i["mime"] == MIME_SHEET]
        drive_items[drive_id] = {"folders": folders, "docs": docs, "sheets": sheets}
        all_folders += folders
        all_docs    += docs
        all_sheets  += sheets
        print(f"  {label}: {len(folders)} folders, {len(docs)} docs, {len(sheets)} sheets")

    print(f"\nTotal — Folders: {len(all_folders)} | Docs: {len(all_docs)} | Sheets: {len(all_sheets)}")

    def populate(ss_id, folders, docs, sheets, label=""):
        if args.init:
            print(f"\n  Writing {label or ss_id}...")
            rc = write_rows(sheets_svc, ss_id, "Folders", folders)
            apply_formatting(sheets_svc, ss_id, "Folders", rc)
            rc = write_rows(sheets_svc, ss_id, "Docs", docs)
            apply_formatting(sheets_svc, ss_id, "Docs", rc)
            rc = write_rows(sheets_svc, ss_id, "Sheets", sheets)
            apply_formatting(sheets_svc, ss_id, "Sheets", rc)
        elif args.scan:
            append_new_rows(sheets_svc, ss_id, "Folders", folders)
            append_new_rows(sheets_svc, ss_id, "Docs",    docs)
            append_new_rows(sheets_svc, ss_id, "Sheets",  sheets)

    # ── Write master hub (everything) ─────────────────────────────────────────
    print("\n── Master hub (ALL DRIVES) ──")
    populate(master_id, all_folders, all_docs, all_sheets, "ALL DRIVES")

    # ── Write per-drive spreadsheets ──────────────────────────────────────────
    print("\n── Per-drive spreadsheets ──")
    for drive_id, label in DRIVES.items():
        ss_id = per_drive.get(drive_id)
        if not ss_id:
            continue
        d = drive_items[drive_id]
        populate(ss_id, d["folders"], d["docs"], d["sheets"], label)

    # Save last run timestamp
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    print(f"\nDone.")
    print(f"Master hub: https://docs.google.com/spreadsheets/d/{master_id}")
    for drive_id, label in DRIVES.items():
        ss_id = per_drive.get(drive_id)
        if ss_id:
            print(f"  {label:20s}: https://docs.google.com/spreadsheets/d/{ss_id}")

if __name__ == "__main__":
    main()
