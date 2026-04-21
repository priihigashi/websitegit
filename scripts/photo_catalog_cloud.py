#!/usr/bin/env python3
"""
photo_catalog_cloud.py — Cloud version for GitHub Actions
Same logic as photo_catalog.py but reads credentials from env vars.
Env vars injected by GitHub Actions workflow:
  CLAUDE_KEY_4_CONTENT    — Anthropic API key
  SHEETS_TOKEN_PATH    — path to sheets_token.json written from secret
  SHEET_ID             — Google Sheet ID (optional override)
"""

import os, sys
from pathlib import Path

# Patch paths for cloud environment
TOKEN_FILE_PATH = os.environ.get("SHEETS_TOKEN_PATH", "")
if not TOKEN_FILE_PATH or not Path(TOKEN_FILE_PATH).exists():
    print("❌ SHEETS_TOKEN_PATH not set or file not found")
    sys.exit(1)

# Monkey-patch the constants before importing the main module
import importlib, types

# Inline the full catalog logic (avoids file path issues in CI)
import json, base64, io, time
from datetime import date
import urllib.request, urllib.parse

SHEET_ID    = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"  # Ideas & Inbox — hardcoded, never override
CATALOG_TAB = "📸 Photo Catalog"
INSPO_TAB   = "📥 Inspiration Library"
MAX_PER_RUN  = 50
IDEAS_PER_RUN = 10  # max photos to generate ideas for per run (controls Claude cost)

IMAGE_MIMES = {"image/jpeg", "image/png", "image/heic", "image/webp", "image/tiff"}

SERVICE_KEYWORDS = {
    "kitchen": "Kitchen Remodel", "bath": "Bathroom", "roof": "Roofing",
    "concrete": "Concrete", "driveway": "Driveway", "pergola": "Pergola",
    "addition": "Addition", "exterior": "Exterior", "interior": "Interior",
    "bedroom": "Bedroom", "living": "Living Room", "basement": "Basement",
}

def get_credentials():
    from google.oauth2.credentials import Credentials
    token_data = json.loads(Path(TOKEN_FILE_PATH).read_text())
    data = urllib.parse.urlencode({
        "client_id":     token_data["client_id"],
        "client_secret": token_data["client_secret"],
        "refresh_token": token_data["refresh_token"],
        "grant_type":    "refresh_token"
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    resp = json.loads(urllib.request.urlopen(req).read())
    return Credentials(
        token=resp["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data.get("scopes", [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets"
        ])
    )

def get_cataloged_filenames(sheets):
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{CATALOG_TAB}'!D:D"
        ).execute()
        rows = result.get("values", [])
        return {r[0] for r in rows[1:] if r}
    except Exception:
        return set()

def append_to_catalog(sheets, rows):
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID, range=f"'{CATALOG_TAB}'!A:T",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()

def ensure_catalog_tab(sheets):
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if CATALOG_TAB not in tabs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": CATALOG_TAB}}}]}
        ).execute()
        header = [["Date Added","Project Name","Service Type","Filename","Drive URL",
                   "AI Description","Phase","Quality ⭐","Enhanced?","Used In Post?",
                   "Date Taken","Ideas Generated?","Suggested Post Date",
                   "Content Type","Times Used",
                   "Room","Trade","Materials","Quality Flag","Client Visible"]]
        append_to_catalog(sheets, header)

def ensure_catalog_columns(sheets):
    """Add the 5 new metadata columns to an existing catalog header if missing. Idempotent."""
    NEW_COLS = ["Room", "Trade", "Materials", "Quality Flag", "Client Visible"]
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{CATALOG_TAB}'!1:1"
    ).execute()
    existing = [c for row in result.get("values", []) for c in row]
    missing = [c for c in NEW_COLS if c not in existing]
    if not missing:
        return
    next_col_idx = len(existing)
    start_col = chr(ord('A') + next_col_idx)
    end_col = chr(ord('A') + next_col_idx + len(missing) - 1)
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"'{CATALOG_TAB}'!{start_col}1:{end_col}1",
        valueInputOption="RAW",
        body={"values": [missing]}
    ).execute()
    print(f"[catalog] Migrated header: added columns {missing}")

def list_folder_children(drive, folder_id, mime_filter=None):
    q = f"'{folder_id}' in parents and trashed=false"
    if mime_filter:
        q += f" and mimeType='{mime_filter}'"
    result = drive.files().list(
        q=q, corpora="allDrives",
        includeItemsFromAllDrives=True, supportsAllDrives=True,
        fields="files(id,name,mimeType,createdTime)"
    ).execute()
    return result.get("files", [])

def get_all_images(drive, drive_id):
    images = []
    top_folders = list_folder_children(drive, drive_id, "application/vnd.google-apps.folder")
    mikes_folder = next((f for f in top_folders
                         if "mike" in f["name"].lower() or "photo" in f["name"].lower()), None)
    if not mikes_folder:
        drives_resp = drive.drives().list(pageSize=20, fields="drives(id,name)").execute()
        for d in drives_resp.get("drives", []):
            if "construction" in d["name"].lower():
                top_folders2 = list_folder_children(drive, d["id"], "application/vnd.google-apps.folder")
                mikes_folder = next((f for f in top_folders2
                                     if "mike" in f["name"].lower() or "photo" in f["name"].lower()), None)
                if mikes_folder:
                    break
    if not mikes_folder:
        print("⚠️  Could not find Mikes Photos & Videos")
        return images

    service_folders = list_folder_children(drive, mikes_folder["id"], "application/vnd.google-apps.folder")
    for svc_folder in service_folders:
        service_type = svc_folder["name"].strip()
        children = list_folder_children(drive, svc_folder["id"])
        project_subfolders = [c for c in children if c["mimeType"] == "application/vnd.google-apps.folder"]
        direct_images = [c for c in children if c["mimeType"] in IMAGE_MIMES]
        for img in direct_images:
            images.append({"id": img["id"], "name": img["name"],
                           "service_type": service_type, "project_name": "General",
                           "created": img.get("createdTime", "")})
        for proj_folder in project_subfolders:
            project_name = proj_folder["name"].strip()
            proj_imgs = [c for c in list_folder_children(drive, proj_folder["id"])
                         if c["mimeType"] in IMAGE_MIMES]
            for img in proj_imgs:
                images.append({"id": img["id"], "name": img["name"],
                               "service_type": service_type, "project_name": project_name,
                               "created": img.get("createdTime", "")})
    return images

def describe_image(file_id, drive, anthropic_key):
    import anthropic as anthropic_sdk
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, drive.files().get_media(
        fileId=file_id, supportsAllDrives=True))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    raw = buf.getvalue()
    # Always resize + compress — handles both Anthropic limits:
    #   dimension: hard limit 8000px per side
    #   size:      hard limit 5 MB
    # 1500px max: sufficient for Claude Vision; guarantees <1MB at q=85 for any image
    MAX_PIXELS = 1500
    MAX_BYTES  = 4_900_000  # safe margin below 5 MB
    try:
        from PIL import Image as PILImage
        img_pil = PILImage.open(io.BytesIO(raw)).convert("RGB")
        img_pil.thumbnail((MAX_PIXELS, MAX_PIXELS), PILImage.LANCZOS)  # always, no condition
        out = io.BytesIO()
        img_pil.save(out, format="JPEG", quality=85, optimize=True)
        for q in [70, 55, 40, 30, 20]:
            if len(out.getvalue()) <= MAX_BYTES:
                break
            out = io.BytesIO()
            img_pil.save(out, format="JPEG", quality=q, optimize=True)
        raw = out.getvalue()
    except ImportError:
        raise RuntimeError("Pillow not installed — cannot resize image for Anthropic API")
    img_b64 = base64.b64encode(raw).decode()
    client = anthropic_sdk.Anthropic(api_key=anthropic_key)
    resp = client.messages.create(
        model="claude-opus-4-6", max_tokens=800,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": (
                "Analyze this construction/renovation photo for content planning.\n"
                "Return ONLY valid JSON — no prose, no markdown fences, no explanation.\n\n"
                "Required format:\n"
                "{\n"
                '  "description": "2-3 sentence description. Specific about room, materials, state of work.",\n'
                '  "nickname": "3-6 word project nickname from visual features",\n'
                '  "phase": "before|during|after|progress",\n'
                '  "quality": 3,\n'
                '  "room": "kitchen|bathroom|exterior|garage|living|dining|office|other",\n'
                '  "trade": "tile|concrete|framing|electrical|plumbing|drywall|painting|flooring|roofing|other",\n'
                '  "materials": ["material1", "material2"],\n'
                '  "quality_flag": false,\n'
                '  "client_visible": true\n'
                "}\n\n"
                "quality is integer 1-5.\n"
                "quality_flag is true when quality < 3 (blurry, dark, cluttered — needs review).\n"
                "client_visible is true when photo is clean, professional, and suitable for portfolio."
            )}
        ]}]
    )
    text = resp.content[0].text
    parsed = {}
    try:
        raw_text = text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        parsed = json.loads(raw_text.strip())
    except (json.JSONDecodeError, IndexError):
        # Fallback: old line-by-line parser so existing runs never hard-fail
        for line in text.splitlines():
            if line.startswith("DESCRIPTION:"): parsed["description"] = line.replace("DESCRIPTION:", "").strip()
            elif line.startswith("NICKNAME:"): parsed["nickname"] = line.replace("NICKNAME:", "").strip()
            elif line.startswith("PHASE:"): parsed["phase"] = line.replace("PHASE:", "").strip().lower()
            elif line.startswith("QUALITY:"): parsed["quality"] = line.replace("QUALITY:", "").strip()
    quality_val = parsed.get("quality", 3)
    try:
        quality_int = int(float(str(quality_val)))
    except (ValueError, TypeError):
        quality_int = 3
    materials = parsed.get("materials", [])
    if isinstance(materials, list):
        materials_str = ", ".join(str(m) for m in materials)
    else:
        materials_str = str(materials)
    return {
        "desc":           parsed.get("description", ""),
        "nickname":       parsed.get("nickname", ""),
        "phase":          str(parsed.get("phase", "unknown")).lower(),
        "quality":        str(quality_int),
        "room":           str(parsed.get("room", "other")),
        "trade":          str(parsed.get("trade", "other")),
        "materials":      materials_str,
        "quality_flag":   "Yes" if parsed.get("quality_flag", quality_int < 3) else "No",
        "client_visible": "Yes" if parsed.get("client_visible", quality_int >= 3) else "No",
    }

def _get_token_from_file() -> str:
    td = json.loads(Path(TOKEN_FILE_PATH).read_text())
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    ).read())
    return resp["access_token"]


def _sheets_append(token: str, tab: str, rows: list):
    enc = urllib.parse.quote(f"'{tab}'!A:Z", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    body = json.dumps({"values": rows}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req).read()


def _sheets_update(token: str, cell_range: str, value):
    enc = urllib.parse.quote(cell_range, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}?valueInputOption=USER_ENTERED"
    body = json.dumps({"values": [[value]]}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    urllib.request.urlopen(req).read()


def generate_ideas_for_photo(desc: str, service_type: str, project_name: str,
                              phase: str, quality: str, api_key: str) -> list:
    """
    Calls Claude Haiku to generate 3 content ideas from a photo description.
    Returns list of dicts: [{type, title, brief, hook, format}]
    """
    import anthropic as anthropic_sdk
    client = anthropic_sdk.Anthropic(api_key=api_key)
    prompt = f"""You are a content strategist for Oak Park Construction, a South Florida renovation contractor.

Photo details:
- Service: {service_type}
- Project: {project_name}
- Phase: {phase}
- Quality: {quality}/5
- Description: {desc}

Generate exactly 3 content ideas for Instagram. Each idea must be ORIGINAL — topics inspired by this photo, not about the photo itself.

Rules:
- NEVER promise what OPC "always does" or "guarantees"
- Stats must include source and range (not exact numbers without context)
- Educational tone — teach homeowners, not sell services
- One idea per type: CAROUSEL, REEL, POST

For each idea output exactly:
TYPE: CAROUSEL | REEL | POST
TITLE: [3-8 word title]
HOOK: [first line / hook sentence]
BRIEF: [2-3 sentence description of what the post covers]
FORMAT: [e.g. "5-slide tip carousel" or "30s talking head" or "single before/after image"]
---"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text
    ideas = []
    for block in text.split("---"):
        block = block.strip()
        if not block:
            continue
        idea = {}
        for line in block.splitlines():
            for key in ["TYPE", "TITLE", "HOOK", "BRIEF", "FORMAT"]:
                if line.startswith(f"{key}:"):
                    idea[key.lower()] = line[len(key)+1:].strip()
        if "type" in idea and "title" in idea:
            ideas.append(idea)
    return ideas[:3]


def generate_ideas_from_catalog(sheets, api_key: str):
    """
    Reads Photo Catalog rows where 'Ideas Generated?' (col L) == 'No',
    generates 3 ideas per photo via Claude Haiku,
    appends them to the Inspiration Library tab,
    and marks 'Ideas Generated?' = 'Yes' in the catalog.
    """
    if not api_key:
        print("[ideas] CLAUDE_KEY_4_CONTENT not set — skipping idea generation")
        return

    result = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{CATALOG_TAB}'!A:T"
    ).execute()
    all_rows = result.get("values", [])
    if len(all_rows) < 2:
        print("[ideas] Photo Catalog empty — nothing to process")
        return

    header = all_rows[0]
    data_rows = all_rows[1:]

    col = {h: i for i, h in enumerate(header)}
    pending = []
    for row_idx, row in enumerate(data_rows):
        row_padded = row + [""] * (len(header) - len(row))
        ideas_done = row_padded[col.get("Ideas Generated?", 11)].strip()
        quality_raw = row_padded[col.get("Quality ⭐", 7)].strip()
        try:
            quality_int = int(float(quality_raw))
        except (ValueError, TypeError):
            quality_int = 0
        if ideas_done.upper() != "YES" and quality_int >= 3:
            pending.append({
                "sheet_row": row_idx + 2,  # 1-indexed + header
                "project_name": row_padded[col.get("Project Name", 1)],
                "service_type": row_padded[col.get("Service Type", 2)],
                "filename":     row_padded[col.get("Filename", 3)],
                "drive_url":    row_padded[col.get("Drive URL", 4)],
                "description":  row_padded[col.get("AI Description", 5)],
                "phase":        row_padded[col.get("Phase", 6)],
                "quality":      quality_raw,
            })

    print(f"[ideas] {len(pending)} photos need ideas (max {IDEAS_PER_RUN} this run)")
    if not pending:
        return

    token = _get_token_from_file()
    today = date.today().isoformat()
    processed = 0

    for photo in pending[:IDEAS_PER_RUN]:
        print(f"  → {photo['service_type']} / {photo['project_name']} — {photo['filename']}")
        try:
            ideas = generate_ideas_for_photo(
                photo["description"], photo["service_type"],
                photo["project_name"], photo["phase"], photo["quality"], api_key
            )
            inspo_rows = []
            for idea in ideas:
                content_type = idea.get("type", "Post").capitalize()
                inspo_rows.append([
                    today,                              # A Date Added
                    "OPC Photos",                       # B Platform
                    photo["drive_url"],                 # C URL (the photo)
                    "Oak Park Construction",            # D Creator/Account
                    content_type,                       # E Content Type
                    idea.get("brief", ""),              # F Description
                    "",                                 # G Transcription
                    "",                                 # H Original Caption
                    idea.get("hook", ""),               # I Visual Hook
                    "Photo",                            # J Hook Type
                    "",                                 # K Views
                    "",                                 # L Content Hub Link
                    "",                                 # M Engagement Comments
                    "",                                 # N Saves/Shares
                    "",                                 # O What's Working
                    "",                                 # P A/B Test
                    idea.get("brief", ""),              # Q Brief/Angle
                    idea.get("format", ""),             # R Format
                    "Idea",                             # S Status
                    idea.get("title", ""),              # T Topic/Title
                    "OPC",                              # U Niche
                    "",                                 # V Comments
                    photo["quality"],                   # W AI Score (1-5)
                    today,                              # X Date Status Changed
                    "",                                 # Y Drive Folder Path
                    f"From Photo Catalog — {photo['project_name']} ({photo['phase']})",  # Z My Raw Notes
                ])

            if inspo_rows:
                _sheets_append(token, INSPO_TAB, inspo_rows)
                print(f"     ✅ {len(inspo_rows)} ideas → Inspiration Library")

            # Mark Ideas Generated? = Yes in catalog col L
            _sheets_update(token, f"'{CATALOG_TAB}'!L{photo['sheet_row']}", "Yes")
            processed += 1
            time.sleep(1)  # brief pause between Claude calls

        except Exception as e:
            print(f"     ⚠️  Error generating ideas: {e}")

    print(f"[ideas] Done — {processed} photos processed, ideas added to Inspiration Library")


def main():
    from googleapiclient.discovery import build
    api_key = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    print(f"\n🏗️  Photo Catalog — Cloud Run — {date.today()}")
    creds = get_credentials()
    drive  = build("drive",  "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    ensure_catalog_tab(sheets)
    ensure_catalog_columns(sheets)
    already_done = get_cataloged_filenames(sheets)
    print(f"📊 Already cataloged: {len(already_done)}")

    # Find shared drive
    drives_resp = drive.drives().list(pageSize=20, fields="drives(id,name)").execute()
    drive_id = next((d["id"] for d in drives_resp.get("drives", [])
                     if "construction" in d["name"].lower()), None)
    if not drive_id:
        print("❌ Could not find Oak Park Construction shared drive")
        return

    all_images = get_all_images(drive, drive_id)
    new_images = [img for img in all_images if img["name"] not in already_done]
    print(f"📊 New images to process: {len(new_images)} (max {MAX_PER_RUN})")

    new_rows = []
    for img in new_images[:MAX_PER_RUN]:
        print(f"  🖼️  {img['service_type']} / {img['project_name']} — {img['name']}")
        try:
            meta = describe_image(img["id"], drive, api_key)
            drive_url = f"https://drive.google.com/file/d/{img['id']}/view"
            project_name = img["project_name"]
            if project_name == "General" and meta["nickname"]:
                project_name = meta["nickname"]
            date_taken = img["created"][:10] if img["created"] else ""
            new_rows.append([
                date.today().isoformat(),  # A Date Added
                project_name,              # B Project Name
                img["service_type"],       # C Service Type
                img["name"],               # D Filename
                drive_url,                 # E Drive URL
                meta["desc"],              # F AI Description
                meta["phase"],             # G Phase
                meta["quality"],           # H Quality ⭐
                "No",                      # I Enhanced?
                "No",                      # J Used In Post?
                date_taken,                # K Date Taken
                "No",                      # L Ideas Generated?
                "",                        # M Suggested Post Date
                "",                        # N Content Type
                "0",                       # O Times Used
                meta["room"],              # P Room
                meta["trade"],             # Q Trade
                meta["materials"],         # R Materials
                meta["quality_flag"],      # S Quality Flag
                meta["client_visible"],    # T Client Visible
            ])
            print(f"     ✅ {project_name} | {meta['phase']} | Q{meta['quality']} | {meta['room']} | flag={meta['quality_flag']}")
            time.sleep(0.5)
        except Exception as e:
            print(f"     ⚠️  Error: {e}")

    if new_rows:
        append_to_catalog(sheets, new_rows)
        print(f"\n✅ Added {len(new_rows)} photos to catalog")
    else:
        print("\n✅ No new photos to add")

    # Generate content ideas for cataloged photos that haven't been processed yet
    print("\n🧠 Generating content ideas from catalog...")
    generate_ideas_from_catalog(sheets, api_key)

if __name__ == "__main__":
    main()
