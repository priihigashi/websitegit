#!/usr/bin/env python3
"""
opc_content_inventory.py — Backfill + daily sync for '🎬 In Production' tab.

Scans OPC _TEMPLATE_CAROUSEL folder in Drive, reads post status from
📸 Project Content Catalog, and writes/updates rows in Content Control's
'🎬 In Production' tab so Priscila always has one place to see what needs review.

Runs via: opc_inventory.yml (daily 3:00 AM ET, before 4AM agent)
Also safe to run manually: python3 scripts/opc_content_inventory.py

Env vars:
  SHEETS_TOKEN        — Google OAuth token JSON
  CONTENT_SHEET_ID    — Ideas & Inbox sheet (default: 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU)
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path
import pytz

ET = pytz.timezone("America/New_York")

# OPC _TEMPLATE_CAROUSEL folder (Marketing drive)
OPC_TEMPLATE_CAROUSEL_ID = "1PWrZfuOvyHUbTRlFNqYxdhtg-Zvv_bXb"

IDEAS_SHEET_ID   = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
CATALOG_TAB      = "📸 Project Content Catalog"
CC_SHEET_ID      = "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQc"  # overridden below
PUBLISHED_TAB    = "✅ Published"
IN_PROD_TAB      = "🎬 In Production"

# Content Control sheet ID (hardcoded — content_tracker.py has the authoritative value)
_CC_SHEET_ID = "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg"


# ── Auth ──────────────────────────────────────────────────────────────────────
_tok_cache = {}

def _token():
    if _tok_cache.get("t") and __import__("time").time() < _tok_cache.get("e", 0):
        return _tok_cache["t"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        p = Path(__file__).parent.parent / "ClaudeWorkspace/Credentials/sheets_token.json"
        if not p.exists():
            p = Path.home() / "ClaudeWorkspace/Credentials/sheets_token.json"
        if p.exists():
            raw = p.read_text()
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN available")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _tok_cache["t"] = resp["access_token"]
    _tok_cache["e"] = __import__("time").time() + resp.get("expires_in", 3500) - 60
    return resp["access_token"]


def _drive():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        p = Path.home() / "ClaudeWorkspace/Credentials/sheets_token.json"
        if p.exists():
            raw = p.read_text()
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    creds = Credentials(
        token=resp["access_token"], refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"], client_secret=td["client_secret"],
    )
    return build("drive", "v3", credentials=creds)


def _sheets_get(sheet_id, range_str):
    token = _token()
    enc = urllib.parse.quote(range_str, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read()).get("values", [])


# ── Step 1: Scan OPC _TEMPLATE_CAROUSEL for version folders ──────────────────
def get_opc_version_folders(drive):
    """List all v<N>_<slug> subfolders under OPC _TEMPLATE_CAROUSEL."""
    resp = drive.files().list(
        q=(f"'{OPC_TEMPLATE_CAROUSEL_ID}' in parents "
           f"and mimeType='application/vnd.google-apps.folder' "
           f"and trashed=false"),
        fields="files(id,name,createdTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
        orderBy="createdTime desc",
        pageSize=200,
    ).execute()
    folders = []
    import re
    for f in resp.get("files", []):
        name = f["name"]
        # Only version folders: v<N>_<slug>
        if re.match(r"^v\d+_.+$", name):
            folders.append({
                "id": f["id"],
                "name": name,
                "created": f.get("createdTime", "")[:10],
                "link": f"https://drive.google.com/drive/folders/{f['id']}",
            })
    return folders


# ── Step 2: Read catalog status map (post_id → status) ───────────────────────
def get_catalog_status_map():
    """Build {drive_folder_link: {status, topic, motion_link}} from Project Content Catalog."""
    rows = _sheets_get(IDEAS_SHEET_ID, f"'{CATALOG_TAB}'!A:O")
    if len(rows) < 2:
        return {}
    header = [h.strip().lower() for h in rows[0]]
    def ci(name): return next((i for i, h in enumerate(header) if name in h), None)

    result = {}
    for row in rows[1:]:
        def v(col):
            i = ci(col)
            return row[i].strip() if i is not None and i < len(row) else ""
        static = v("static folder") or (row[8] if len(row) > 8 else "")
        if not static:
            continue
        result[static.strip()] = {
            "status": v("status") or "pending_approval",
            "topic":  v("topic") or (row[13] if len(row) > 13 else ""),
            "motion": v("motion folder") or (row[9] if len(row) > 9 else ""),
            "niche":  v("niche"),
            "series": v("series"),
        }
    return result


# ── Step 3: Read Published tab links ─────────────────────────────────────────
def get_published_links():
    """Return set of Drive links that appear in Published tab."""
    rows = _sheets_get(_CC_SHEET_ID, f"'{PUBLISHED_TAB}'!A:H")
    links = set()
    for row in rows[1:]:
        for cell in row:
            if "drive.google.com" in cell:
                links.add(cell.strip())
    return links


# ── Step 4: Read existing In Production rows (dedup) ─────────────────────────
def get_existing_in_production():
    """Return {drive_folder_link: row_number} for rows already in In Production.
    New layout: A=#Reviews B=Title C=PostType D=Format E=ContentType F=Status G=DriveLink ...
    Drive Folder Link is now col G (index 6).
    """
    rows = _sheets_get(_CC_SHEET_ID, f"'{IN_PROD_TAB}'!A:K")
    result = {}
    for i, row in enumerate(rows):
        if len(row) > 6 and "drive.google.com" in row[6]:
            result[row[6].strip()] = i + 1  # 1-based row number
    return result


# ── Step 5: Write rows to In Production ──────────────────────────────────────
def _sheets_batch_update(data_list):
    token = _token()
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": data_list}).encode()
    urllib.request.urlopen(urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{_CC_SHEET_ID}/values:batchUpdate",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )).read()


def _sheets_append(values_list):
    token = _token()
    enc = urllib.parse.quote(f"'{IN_PROD_TAB}'!A:K", safe="!:'")
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{_CC_SHEET_ID}/values/{enc}"
           f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    payload = json.dumps({"values": values_list}).encode()
    urllib.request.urlopen(urllib.request.Request(
        url, data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )).read()


def _slug_to_title(slug):
    """Convert v1_walnut-kitchen-tips → Walnut Kitchen Tips"""
    import re
    clean = re.sub(r"^v\d+_", "", slug)
    return " ".join(w.capitalize() for w in clean.replace("-", " ").split())


def sync_in_production(dry_run=False):
    print(f"\n[opc_inventory] Starting — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")

    drive = _drive()
    folders = get_opc_version_folders(drive)
    catalog = get_catalog_status_map()
    published_links = get_published_links()
    existing = get_existing_in_production()

    print(f"  Drive folders found: {len(folders)}")
    print(f"  Catalog entries: {len(catalog)}")
    print(f"  Published links: {len(published_links)}")
    print(f"  Existing In Production rows: {len(existing)}")

    added = 0
    updated = 0

    for folder in folders:
        link = folder["link"]
        name = folder["name"]
        created = folder["created"]

        # Determine status: check published first, then catalog, then default
        if link in published_links or any(link in pl for pl in published_links):
            status = "Published"
        elif link in catalog:
            raw = catalog[link]["status"]
            # Normalize to human-readable
            status_map = {
                "pending_approval": "Built",
                "in_review": "Built",
                "email_sent": "Built",
                "approved": "Approved",
                "needs_revision": "Needs Revision",
                "scheduled": "Scheduled",
                "skipped": "Built",
                "built": "Built",
                "published": "Published",
            }
            status = status_map.get(raw.lower().replace(" ", "_"), raw.title())
        else:
            status = "Built"

        title = catalog.get(link, {}).get("topic") or _slug_to_title(name)
        motion = catalog.get(link, {}).get("motion", "")

        if link in existing:
            row_num = existing[link]
            if not dry_run:
                # New layout: F=Status, J=Output Link
                _sheets_batch_update([
                    {"range": f"'{IN_PROD_TAB}'!F{row_num}", "values": [[status]]},
                    {"range": f"'{IN_PROD_TAB}'!J{row_num}", "values": [[motion]]},
                ])
            print(f"  UPDATE row {row_num}: {name[:40]} → {status}")
            updated += 1
        else:
            # New layout: #Reviews | Title | PostType | Format | ContentType | Status | DriveLink | Caption | Hashtags | OutputLink | Date
            row = [1, title[:100], "", "", "Carousel", status, link, "", "", motion, created]
            if not dry_run:
                _sheets_append([row])
            print(f"  ADD: {name[:40]} → {status}")
            added += 1

    print(f"\n[opc_inventory] Done — {added} added, {updated} updated")
    return {"added": added, "updated": updated, "total": len(folders)}


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    if dry:
        print("[opc_inventory] DRY RUN — no writes")
    result = sync_in_production(dry_run=dry)
    print(json.dumps(result))
