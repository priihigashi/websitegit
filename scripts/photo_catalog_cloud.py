#!/usr/bin/env python3
"""
photo_catalog_cloud.py — Cloud version for GitHub Actions
Same logic as photo_catalog.py but reads credentials from env vars.
Env vars injected by GitHub Actions workflow:
  ANTHROPIC_API_KEY    — Anthropic API key
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

SHEET_ID_OVERRIDE = os.environ.get("SHEET_ID", "")

# Monkey-patch the constants before importing the main module
import importlib, types

# Inline the full catalog logic (avoids file path issues in CI)
import json, base64, io, time
from datetime import date
import urllib.request, urllib.parse

SHEET_ID   = SHEET_ID_OVERRIDE or "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CATALOG_TAB = "📸 Photo Catalog"
MAX_PER_RUN = 50

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
        spreadsheetId=SHEET_ID, range=f"'{CATALOG_TAB}'!A:O",
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
                   "Content Type","Times Used"]]
        append_to_catalog(sheets, header)

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
    if len(raw) > 4 * 1024 * 1024:
        try:
            from PIL import Image as PILImage
            img_pil = PILImage.open(io.BytesIO(raw)).convert("RGB")
            out = io.BytesIO()
            for q in [70, 55, 40, 30]:
                out = io.BytesIO()
                img_pil.save(out, format="JPEG", quality=q, optimize=True)
                if len(out.getvalue()) <= 4 * 1024 * 1024:
                    break
            raw = out.getvalue()
        except ImportError:
            pass
    img_b64 = base64.b64encode(raw).decode()
    client = anthropic_sdk.Anthropic(api_key=anthropic_key)
    resp = client.messages.create(
        model="claude-opus-4-6", max_tokens=600,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": (
                "Describe this construction/renovation photo for grouping with related photos.\n"
                "Be specific about room type, materials, distinctive features, state of work.\n"
                "Then create a SHORT PROJECT NICKNAME (3-6 words) from distinctive visual features.\n"
                "Format:\nDESCRIPTION: [2-3 sentence description]\n"
                "NICKNAME: [short nickname]\nPHASE: before|during|after|progress\nQUALITY: 1-5"
            )}
        ]}]
    )
    text = resp.content[0].text
    desc, nickname, phase, quality = "", "", "unknown", "3"
    for line in text.splitlines():
        if line.startswith("DESCRIPTION:"): desc = line.replace("DESCRIPTION:", "").strip()
        elif line.startswith("NICKNAME:"): nickname = line.replace("NICKNAME:", "").strip()
        elif line.startswith("PHASE:"): phase = line.replace("PHASE:", "").strip().lower()
        elif line.startswith("QUALITY:"): quality = line.replace("QUALITY:", "").strip()
    return desc, nickname, phase, quality

def main():
    from googleapiclient.discovery import build
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print(f"\n🏗️  Photo Catalog — Cloud Run — {date.today()}")
    creds = get_credentials()
    drive  = build("drive",  "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    ensure_catalog_tab(sheets)
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
            desc, nickname, phase, quality = describe_image(img["id"], drive, api_key)
            drive_url = f"https://drive.google.com/file/d/{img['id']}/view"
            project_name = img["project_name"]
            if project_name == "General" and nickname:
                project_name = nickname
            date_taken = img["created"][:10] if img["created"] else ""
            new_rows.append([
                date.today().isoformat(), project_name, img["service_type"],
                img["name"], drive_url, desc, phase, quality,
                "No", "No", date_taken, "No", "", "", "0"
            ])
            print(f"     ✅ {project_name} | {phase} | Q{quality}")
            time.sleep(0.5)
        except Exception as e:
            print(f"     ⚠️  Error: {e}")

    if new_rows:
        append_to_catalog(sheets, new_rows)
        print(f"\n✅ Added {len(new_rows)} photos to catalog")
    else:
        print("\n✅ No new photos to add")

if __name__ == "__main__":
    main()
