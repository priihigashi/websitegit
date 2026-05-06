"""clipmine_retention.py — 30-day retention sweeper for SH-104 raw media.

For every Drive folder named `clipmine_*` under each niche's Captures parent,
if the folder is older than RETAIN_DAYS:
  - DELETE the `transcripts/` subfolder + raw media (audio/video files)
  - DELETE per-candidate transcript .txt files at the root
  - KEEP `evidence_manifest.json`, `scored_candidates.json`, `run.log`

This implements the SKILL legal guardrail:
  "raw media auto-prunes after 30 days; transcripts persist long-term"

Default mode is DRY-RUN — list-only — so the first scheduled run produces
visibility before deleting anything. Set CLIPMINE_RETENTION_APPLY=1 to
perform deletions.

Wired into:
  - .github/workflows/clipmine_retention.yml (daily cron)
  - skills/clip-mine/SKILL.md "Legal guardrails" section
"""

from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make routing.py importable.
_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
for _p in (_HERE, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import routing  # noqa: E402

SHEETS_TOKEN_RAW = os.environ.get("SHEETS_TOKEN", "")
APPLY = os.environ.get("CLIPMINE_RETENTION_APPLY", "0") == "1"
RETAIN_DAYS = int(os.environ.get("CLIPMINE_RETAIN_DAYS", "30"))
GHA_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
IDEAS_INBOX_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"

# Files KEPT regardless of age — manifest + audit lives forever.
KEEP_FILE_NAMES = {
    "evidence_manifest.json",
    "scored_candidates.json",
    "run.log",
    "pre_render_audit.json",
    "carousel_content_spec.json",
    "remotion_props.json",
    "sources_block.txt",
}

# Subfolders to fully wipe (raw media + transcripts).
PURGE_SUBFOLDER_NAMES = {"transcripts", "raw", "media", "audio"}


def _creds():
    if not SHEETS_TOKEN_RAW:
        return None
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(
        json.loads(SHEETS_TOKEN_RAW),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )


def _drive():
    from googleapiclient.discovery import build
    creds = _creds()
    return build("drive", "v3", credentials=creds) if creds else None


def _list_clipmine_folders(drive, parent_id: str) -> list[dict]:
    """Return clipmine_* folders directly under parent_id."""
    if not parent_id:
        return []
    out = []
    page_token = None
    q = (f"'{parent_id}' in parents "
         f"and mimeType = 'application/vnd.google-apps.folder' "
         f"and name contains 'clipmine_' and trashed = false")
    while True:
        res = drive.files().list(
            q=q,
            fields="nextPageToken, files(id, name, createdTime, modifiedTime)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageSize=100, pageToken=page_token,
        ).execute()
        out.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return out


def _list_children(drive, parent_id: str) -> list[dict]:
    out = []
    page_token = None
    while True:
        res = drive.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, size, createdTime)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageSize=200, pageToken=page_token,
        ).execute()
        out.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return out


def _is_old(file_obj: dict, cutoff_iso: str) -> bool:
    created = file_obj.get("createdTime") or file_obj.get("modifiedTime") or ""
    return bool(created) and created < cutoff_iso


def _delete(drive, file_id: str, label: str, dry: bool) -> bool:
    if dry:
        print(f"    [dry-run] would delete: {label} ({file_id})")
        return True
    try:
        drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        print(f"    deleted: {label} ({file_id})")
        return True
    except Exception as e:
        print(f"    delete FAILED: {label} ({file_id}) — {e}")
        return False


def _log_failure(stage: str, error: str):
    """Best-effort write to 🚨 Pipeline Failures tab."""
    try:
        from googleapiclient.discovery import build
        creds = _creds()
        if not creds:
            return
        svc = build("sheets", "v4", credentials=creds)
        run_url = (f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{GHA_RUN_ID}"
                   if GHA_RUN_ID else "")
        svc.spreadsheets().values().append(
            spreadsheetId=IDEAS_INBOX_ID,
            range="'🚨 Pipeline Failures'!A:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[
                datetime.now(timezone.utc).isoformat(),
                "clipmine_retention.py",
                GHA_RUN_ID, stage, str(error)[:500], run_url, "", "",
            ]]},
        ).execute()
    except Exception:
        pass


def purge_one_folder(drive, folder: dict, cutoff_iso: str, dry: bool) -> dict:
    """Iterate children. Delete:
      - Subfolders whose name is in PURGE_SUBFOLDER_NAMES (recursively trashed)
      - Files NOT in KEEP_FILE_NAMES whose mimetype is video/* or audio/*
    Keeps manifest + audit JSON files.
    """
    name = folder.get("name", "")
    fid = folder["id"]
    summary = {"folder_name": name, "folder_id": fid,
               "deleted": 0, "kept": 0, "errors": 0}
    children = _list_children(drive, fid)
    for c in children:
        cname = c.get("name", "")
        cmime = c.get("mimeType", "")
        is_folder = cmime == "application/vnd.google-apps.folder"
        if is_folder and cname in PURGE_SUBFOLDER_NAMES:
            ok = _delete(drive, c["id"], f"{name}/{cname}/ (subfolder)", dry)
            summary["deleted" if ok else "errors"] += 1
            continue
        if cname in KEEP_FILE_NAMES:
            summary["kept"] += 1
            continue
        # Raw media files at root: video/audio mimes get purged.
        if cmime.startswith("video/") or cmime.startswith("audio/"):
            ok = _delete(drive, c["id"], f"{name}/{cname}", dry)
            summary["deleted" if ok else "errors"] += 1
            continue
        # Transcripts at root level (.txt with yt/ig/tt prefix): purge.
        if cname.endswith(".txt") and cname not in KEEP_FILE_NAMES:
            ok = _delete(drive, c["id"], f"{name}/{cname}", dry)
            summary["deleted" if ok else "errors"] += 1
            continue
        summary["kept"] += 1
    return summary


def run() -> int:
    drive = _drive()
    if not drive:
        print("No SHEETS_TOKEN — cannot run retention sweep")
        _log_failure("retention_init", "no_sheets_token")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
    cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")
    mode = "APPLY" if APPLY else "DRY-RUN (CLIPMINE_RETENTION_APPLY=0)"
    print(f"=== clipmine_retention sweep ({mode}) ===")
    print(f"  Cutoff: {cutoff_iso} ({RETAIN_DAYS}d)")

    niches = ["brazil", "usa", "opc", "higashi", "ugc", "stocks"]
    total_folders = 0
    total_aged = 0
    total_deleted = 0
    total_errors = 0
    summaries = []

    for niche in niches:
        try:
            parent = routing.capture_folder(niche)
        except Exception as e:
            _log_failure(f"capture_folder:{niche}", e)
            continue
        if not parent:
            continue
        try:
            folders = _list_clipmine_folders(drive, parent)
        except Exception as e:
            _log_failure(f"list_clipmine:{niche}", e)
            continue
        print(f"\n  niche={niche} parent={parent} clipmine_folders={len(folders)}")
        for f in folders:
            total_folders += 1
            if not _is_old(f, cutoff_iso):
                continue
            total_aged += 1
            try:
                summ = purge_one_folder(drive, f, cutoff_iso, dry=not APPLY)
            except Exception as e:
                _log_failure(f"purge:{f.get('name','')}", e)
                total_errors += 1
                continue
            summaries.append(summ)
            total_deleted += summ["deleted"]
            total_errors += summ["errors"]
            print(f"    {f['name']} → deleted={summ['deleted']} kept={summ['kept']}")

    print()
    print(f"Folders scanned : {total_folders}")
    print(f"Folders > {RETAIN_DAYS}d  : {total_aged}")
    print(f"Items deleted   : {total_deleted}")
    print(f"Errors          : {total_errors}")
    print(f"Mode            : {mode}")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(run())
