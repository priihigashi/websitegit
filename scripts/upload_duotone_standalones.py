"""
Upload duotone/standalone HTML templates to Drive standalones folders.

Run from your Mac:
  cd /path/to/oak-park-ai-hub
  python3 scripts/upload_duotone_standalones.py

Requirements: google-auth-oauthlib google-api-python-client
Credentials:  ~/ClaudeWorkspace/Credentials/sheets_token.json
"""

import os
import sys
from pathlib import Path
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# ── Target Drive folders ────────────────────────────────────────────────────
NEWS_FOLDER_ID = "1Dp0igYURaNiCxlZZPXg_2SxXI0KBPbuG"
OPC_FOLDER_ID  = "1QD4gwEudkjwI8NKJv4UDPMxnfIOUddTq"

# ── Files to upload ─────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
FILES = [
    (REPO_ROOT / "docs/templates/news_brazil_duotone.html",   NEWS_FOLDER_ID),
    (REPO_ROOT / "docs/templates/news_usa_duotone.html",      NEWS_FOLDER_ID),
    (REPO_ROOT / "docs/templates/news_brazil_standalone.html", NEWS_FOLDER_ID),
    (REPO_ROOT / "docs/templates/news_usa_standalone.html",   NEWS_FOLDER_ID),
    (REPO_ROOT / "docs/templates/opc_duotone.html",           OPC_FOLDER_ID),
]

TOKEN_PATH = Path.home() / "ClaudeWorkspace/Credentials/sheets_token.json"


def upload(service, file_path: Path, folder_id: str) -> str:
    from googleapiclient.http import MediaFileUpload

    # Check if a file with the same name already exists — delete it first
    existing = service.files().list(
        q=f"name='{file_path.name}' and '{folder_id}' in parents and trashed=false",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id,name)",
    ).execute().get("files", [])

    for f in existing:
        service.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
        print(f"  Deleted old version: {f['name']} ({f['id']})")

    media = MediaFileUpload(str(file_path), mimetype="text/html", resumable=False)
    metadata = {"name": file_path.name, "parents": [folder_id]}
    result = service.files().create(
        body=metadata,
        media_body=media,
        supportsAllDrives=True,
        fields="id,name,webViewLink",
    ).execute()
    return result.get("webViewLink", result["id"])


def main():
    if not TOKEN_PATH.exists():
        print(f"Token not found: {TOKEN_PATH}")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
    service = build("drive", "v3", credentials=creds)

    for file_path, folder_id in FILES:
        if not file_path.exists():
            print(f"SKIP (not found): {file_path.name}")
            continue
        print(f"Uploading {file_path.name} → folder {folder_id} ...", end=" ")
        link = upload(service, file_path, folder_id)
        print(f"OK  {link}")

    print("\nAll uploads complete.")


if __name__ == "__main__":
    main()
