#!/usr/bin/env python3
"""
create_template_folders.py — One-time setup script
Creates Templates - Claude/ and 5 subfolders inside Content - Reels & TikTok on Google Drive.

Run once locally:
  python create_template_folders.py

After running, drop your JPEG example designs into each subfolder on Drive:
  - basic-carousel     → example of a standard 4-5 slide carousel
  - product-arrows     → example with arrow/pointer overlay style
  - panoramic-scroll   → example of wide panoramic photo split across slides
  - animated-static    → example of static post that looks motion-like
  - video              → example thumbnail or frame from a Reel template
"""

import os, json, urllib.request, urllib.parse, time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
CONTENT_FOLDER_ID = "1Y2ymfzpE4mZOFrIwWrFQHEfFeYK5sDmG"  # Content - Reels & TikTok

TEMPLATE_SUBFOLDERS = [
    "basic-carousel",
    "product-arrows",
    "panoramic-scroll",
    "animated-static",
    "video",
]

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_token():
    # Try SHEETS_TOKEN env var first (GitHub Actions style)
    raw = os.environ.get("SHEETS_TOKEN", "")

    # Fall back to local token file
    if not raw:
        local_paths = [
            Path.home() / ".oak_park" / "sheets_token.json",
            Path("/tmp/oak_park_creds/sheets_token.json"),
        ]
        for p in local_paths:
            if p.exists():
                raw = p.read_text()
                break

    if not raw:
        raise RuntimeError(
            "No credentials found.\n"
            "Set SHEETS_TOKEN env var or place sheets_token.json at ~/.oak_park/sheets_token.json"
        )

    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    return resp["access_token"]

# ── Drive helpers ─────────────────────────────────────────────────────────────
def create_folder(token, name, parent_id):
    """Create a Drive folder. Returns folder ID."""
    payload = json.dumps({
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }).encode()
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/files?supportsAllDrives=true",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["id"]

def folder_exists(token, name, parent_id):
    """Check if a folder with this name already exists under parent. Returns ID or None."""
    q = (f"name='{name}' and '{parent_id}' in parents and "
         f"mimeType='application/vnd.google-apps.folder' and trashed=false")
    url = ("https://www.googleapis.com/drive/v3/files"
           f"?q={urllib.parse.quote(q)}&fields=files(id,name)&supportsAllDrives=true&includeItemsFromAllDrives=true")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    files = resp.get("files", [])
    return files[0]["id"] if files else None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n📁 Drive Template Folder Setup")
    print("=" * 40)
    print("Location: Content - Reels & TikTok → Templates - Claude")
    print()

    print("🔐 Authenticating...")
    token = get_token()
    print("   ✅ Authenticated\n")

    # Create or find Templates - Claude parent
    templates_id = folder_exists(token, "Templates - Claude", CONTENT_FOLDER_ID)
    if templates_id:
        print(f"📂 Templates - Claude already exists (ID: {templates_id})")
    else:
        templates_id = create_folder(token, "Templates - Claude", CONTENT_FOLDER_ID)
        print(f"✅ Created: Templates - Claude (ID: {templates_id})")

    # Create each subfolder
    print()
    for subfolder in TEMPLATE_SUBFOLDERS:
        existing = folder_exists(token, subfolder, templates_id)
        if existing:
            print(f"   ⏭️  {subfolder} already exists — skipped")
        else:
            fid = create_folder(token, subfolder, templates_id)
            print(f"   ✅ Created: {subfolder} (ID: {fid})")
        time.sleep(0.3)  # gentle rate limit

    print()
    print("✅ Done! Folder structure:")
    print("   Content - Reels & TikTok")
    print("   └── Templates - Claude")
    for sf in TEMPLATE_SUBFOLDERS:
        print(f"         ├── {sf}")
    print()
    print("Next step: Drop JPEG example designs into each folder on Drive.")
    print("The carousel builder will read these as style references.")

if __name__ == "__main__":
    main()
