"""Resource router for capture notes/transcripts.

Converts note_parser() output into a stable resource manifest that later stages
can download, research, upload, and attach to carousel version folders.

Two flows (per docs/pipeline-fix/resource-routing-flow-2026-05-14.md):
  Flow A — URL in notes  → yt-dlp download → clips.json status=STAGED →
                          upload to Drive capture folder.
  Flow B — Research mode → ytsearch5:<topic> → top 3 → clips.json status=CANDIDATE →
                          upload to Drive + email Priscila for approval.

Public entry points:
  - build_resource_manifest()    pure: notes → manifest dict
  - write_resource_manifest()    pure: manifest dict → local JSON
  - route_capture_resources()    parse + write manifest (no side-effects beyond JSON)
  - execute_resource_jobs()      EXECUTES manifest jobs (download, upload, email)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CAPTURE_DIR = Path(__file__).resolve().parent
CONTENT_CREATOR_DIR = Path(__file__).resolve().parents[1] / "content_creator"
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for _p in (CAPTURE_DIR, CONTENT_CREATOR_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from note_parser import note_parser  # noqa: E402

try:
    import video_downloader  # noqa: E402
    import clips_manifest  # noqa: E402
except ImportError:  # pragma: no cover - import errors surface at runtime only
    video_downloader = None  # type: ignore
    clips_manifest = None  # type: ignore


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug or "resource")[:max_len]


def build_resource_manifest(
    *,
    story_id: str,
    project: str,
    notes: str,
    transcript: str = "",
    seed_url: str = "",
    topic: str = "",
    use_llm: bool = False,
) -> dict[str, Any]:
    """Create a normalized resource manifest from notes and transcript text.

    Notes are primary because they represent Priscila's instruction. Transcript
    labeling can be added later as a second pass, but this function already
    preserves a transcript excerpt so future transcript-derived jobs have a home.
    """
    intent = note_parser(notes or "", project=project or "", use_llm=use_llm)
    resource_requests = intent.get("resource_requests") or []
    items = []
    jobs = []

    for idx, req in enumerate(resource_requests, start=1):
        kind = req.get("kind", "video_clip")
        ext = ".mp4" if kind == "video_clip" else ".jpg"
        item_id = f"{'clip' if kind == 'video_clip' else 'image'}_{idx:03d}"
        local_path = f"{req.get('target', 'resources/clips')}/{item_id}{ext}"
        item = {
            "id": item_id,
            "kind": kind,
            "source": "notes",
            "source_url": req.get("source_url", ""),
            "local_path": local_path,
            "drive_file_id": "",
            "slide_hint": req.get("slide_hint", ""),
            "target_slide": req.get("target_slide"),
            "cut_hint": req.get("cut_hint", ""),
            "usage": req.get("role", req.get("type", "")),
            "required": True,
            "status": "pending",
        }
        items.append(item)
        jobs.append({
            "id": f"job_{idx:03d}",
            "type": req.get("type", ""),
            "kind": kind,
            "status": "pending",
            "request": req,
            "output_item_id": item_id,
        })

    return {
        "story_id": story_id or "",
        "topic": topic or _derive_topic(notes, transcript, seed_url),
        "niche": project or "",
        "seed_url": seed_url or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "notes",
        "intent": {
            "action": intent.get("action"),
            "build_now": intent.get("build_now"),
            "intent_labels": intent.get("intent_labels", []),
            "required_functions": intent.get("required_functions", []),
            "reviewer_blockers": intent.get("reviewer_blockers", []),
            "research_required": intent.get("research_required", False),
            "clip_required": intent.get("clip_required", False),
            "image_required": intent.get("image_required", False),
        },
        "notes_excerpt": (notes or "")[:1000],
        "transcript_excerpt": (transcript or "")[:1000],
        "items": items,
        "jobs": jobs,
    }


def write_resource_manifest(manifest: dict[str, Any], output_dir: str | os.PathLike[str]) -> Path:
    """Write manifest JSON and return the path."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    story_slug = slugify(manifest.get("story_id") or manifest.get("topic") or "story", 80)
    path = out_dir / f"{story_slug}_resource_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def route_capture_resources(
    *,
    story_id: str,
    project: str,
    notes: str,
    transcript: str = "",
    seed_url: str = "",
    topic: str = "",
    output_dir: str | os.PathLike[str] = "transcripts",
    use_llm: bool = False,
) -> dict[str, Any]:
    """Build and write a resource manifest for a capture run."""
    manifest = build_resource_manifest(
        story_id=story_id,
        project=project,
        notes=notes,
        transcript=transcript,
        seed_url=seed_url,
        topic=topic,
        use_llm=use_llm,
    )
    path = write_resource_manifest(manifest, output_dir)
    manifest["manifest_path"] = str(path)
    return manifest


def _derive_topic(notes: str, transcript: str, seed_url: str) -> str:
    text = (notes or "").strip() or (transcript or "").strip() or seed_url or "resource request"
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] or "resource request"


# ---------------------------------------------------------------------------
# Job execution — downloads, Drive upload, email dispatch
# ---------------------------------------------------------------------------

def _capture_folder_for(project: str) -> str:
    """Return per-niche capture Drive folder ID via routing.py."""
    try:
        from routing import capture_folder
        return capture_folder(project) or ""
    except Exception:
        return ""


def _get_drive_service():
    """OAuth Drive service. Same auth path used by capture_pipeline.py."""
    token_path = (
        os.environ.get("SHEETS_TOKEN_PATH")
        or "/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json"
    )
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        # In CI, SHEETS_TOKEN env var holds JSON contents (not a path).
        sheets_token_env = os.environ.get("SHEETS_TOKEN", "")
        if sheets_token_env and sheets_token_env.strip().startswith("{"):
            import tempfile
            tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            tmp.write(sheets_token_env)
            tmp.flush()
            tmp.close()
            token_path = tmp.name
        if not os.path.exists(token_path):
            return None
        scopes = [
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_authorized_user_file(token_path, scopes=scopes)
        return build("drive", "v3", credentials=creds)
    except Exception as exc:
        print(f"  [resource_router] Drive auth failed: {exc}")
        return None


def _get_sheets_service():
    """OAuth Sheets service for Pipeline Failures logging."""
    token_path = (
        os.environ.get("SHEETS_TOKEN_PATH")
        or "/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json"
    )
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        sheets_token_env = os.environ.get("SHEETS_TOKEN", "")
        if sheets_token_env and sheets_token_env.strip().startswith("{"):
            import tempfile
            tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            tmp.write(sheets_token_env)
            tmp.flush()
            tmp.close()
            token_path = tmp.name
        if not os.path.exists(token_path):
            return None
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_authorized_user_file(token_path, scopes=scopes)
        return build("sheets", "v4", credentials=creds)
    except Exception as exc:
        print(f"  [resource_router] Sheets auth failed: {exc}")
        return None


def log_pipeline_failure(*, story_id: str, project: str, stage: str,
                         error: str, source_url: str = "") -> bool:
    """Append a resource-router failure row to Ideas & Inbox Pipeline Failures."""
    spreadsheet_id = os.environ.get(
        "IDEAS_INBOX_SPREADSHEET_ID",
        "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU",
    )
    tab = os.environ.get("PIPELINE_FAILURES_TAB", "🚨 Pipeline Failures")
    service = _get_sheets_service()
    if not service:
        return False
    row = [
        datetime.now(timezone.utc).isoformat(),
        "resource_router",
        story_id or "",
        project or "",
        stage or "",
        source_url or "",
        (error or "")[:1000],
    ]
    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A:G",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()
        return True
    except Exception as exc:
        print(f"  [resource_router] Pipeline Failures log failed: {exc}")
        return False


def upload_clip_to_drive(local_path: str, parent_folder_id: str, *, drive=None) -> dict:
    """Upload a local clip file to a SHARED-DRIVE folder.

    Returns {"id": str, "webViewLink": str} on success, {} on failure.
    NEVER uses MCP create_file (silently empty). Always OAuth + MediaFileUpload +
    supportsAllDrives=True per NONNEGOTIABLES.
    """
    if not local_path or not os.path.exists(local_path):
        return {}
    if not parent_folder_id:
        print("  [resource_router] no parent_folder_id — skipping Drive upload")
        return {}
    drive = drive or _get_drive_service()
    if not drive:
        return {}
    try:
        from googleapiclient.http import MediaFileUpload
        name = os.path.basename(local_path)
        ext = os.path.splitext(name)[1].lower()
        mime = {
            ".mp4": "video/mp4",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".json": "application/json",
        }.get(ext, "application/octet-stream")
        media = MediaFileUpload(local_path, mimetype=mime, resumable=True)
        meta = {"name": name, "parents": [parent_folder_id]}
        resp = drive.files().create(
            body=meta,
            media_body=media,
            supportsAllDrives=True,
            fields="id,name,webViewLink",
        ).execute()
        return {"id": resp.get("id", ""), "webViewLink": resp.get("webViewLink", "")}
    except Exception as exc:
        print(f"  [resource_router] Drive upload failed for {local_path}: {exc}")
        return {}


def _ensure_subfolder(parent_id: str, name: str, *, drive=None) -> str:
    """Find or create a folder by name under parent_id (shared drive aware)."""
    if not parent_id or not name:
        return parent_id
    drive = drive or _get_drive_service()
    if not drive:
        return parent_id
    try:
        q = (
            f"name = '{name}' and '{parent_id}' in parents "
            f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        resp = drive.files().list(
            q=q,
            spaces="drive",
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
        ).execute()
        files = resp.get("files", [])
        if files:
            return files[0]["id"]
        # create
        created = drive.files().create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            supportsAllDrives=True,
            fields="id,name",
        ).execute()
        return created.get("id", parent_id)
    except Exception as exc:
        print(f"  [resource_router] _ensure_subfolder failed: {exc}")
        return parent_id


def _trigger_send_email(*, to: str, subject: str, body: str) -> bool:
    """Fire send_email.yml via ~/bin/gh — actually delivers via PRI_OP_GMAIL_APP_PASSWORD."""
    gh = os.environ.get("GH_CLI") or ("/Users/priscilahigashi/bin/gh"
                                     if os.path.exists("/Users/priscilahigashi/bin/gh") else "gh")
    cmd = [
        gh, "workflow", "run", "send_email.yml",
        "--repo", "priihigashi/oak-park-ai-hub",
        "-f", f"to={to}",
        "-f", f"subject={subject}",
        "-f", f"body={body}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            print(f"  [resource_router] email dispatched: {subject}")
            return True
        print(f"  [resource_router] gh send_email failed rc={proc.returncode}: "
              f"{(proc.stderr or '')[:200]}")
    except FileNotFoundError:
        print("  [resource_router] gh CLI not found — cannot send email")
    except Exception as exc:
        print(f"  [resource_router] send_email exception: {exc}")
    return False


def _format_candidate_email(*, story_id: str, project: str, query: str,
                            clips: list[dict], folder_link: str) -> tuple[str, str]:
    subj = f"[ResourceRouter] {story_id or project or 'capture'} — {len(clips)} clip candidates need approval"
    rows = []
    for i, c in enumerate(clips, start=1):
        title = c.get("title") or c.get("source_url", "")
        rows.append(
            f"{i}. {title}\n"
            f"   URL:      {c.get('source_url','')}\n"
            f"   Duration: {c.get('duration_sec',0):.1f}s\n"
            f"   Drive:    {c.get('drive_view_link','(not uploaded)')}\n"
        )
    body = (
        f"Story:   {story_id or '(auto)'}\n"
        f"Niche:   {project or '(unrouted)'}\n"
        f"Query:   {query}\n"
        f"Folder:  {folder_link or '(no Drive folder)'}\n\n"
        f"Top {len(clips)} ytsearch candidates (status CANDIDATE):\n\n"
        + "\n".join(rows)
        + "\nReply APPROVE 1,3 or APPROVE ALL or REJECT ALL — approval handler will route the picks.\n"
    )
    return subj, body


def execute_resource_jobs(
    manifest: dict,
    *,
    project: str = "",
    clips_dir: Path | str | None = None,
    drive_parent_id: str = "",
    send_emails: bool = True,
) -> dict:
    """Execute the jobs encoded in a manifest.

    Flow A (download_note_link) → yt-dlp download → upload → clips.json STAGED
    Flow B (research_videos)    → ytsearch5 → top 3 → upload → clips.json CANDIDATE → email

    Returns the manifest with `items[*].status` and `jobs[*].status` updated.
    """
    if video_downloader is None or clips_manifest is None:
        print("  [resource_router] video_downloader/clips_manifest unavailable — skipping execution")
        return manifest

    story_id = manifest.get("story_id") or "story"
    niche = project or manifest.get("niche") or ""

    # Local staging dir for downloads
    staging_root = Path(clips_dir) if clips_dir else video_downloader.staging_dir_for(story_id)
    staging_root.mkdir(parents=True, exist_ok=True)

    # Drive parent for uploads
    drive_parent_id = drive_parent_id or _capture_folder_for(niche)
    drive = _get_drive_service()
    drive_subfolder_id = ""
    drive_folder_link = ""
    if drive and drive_parent_id:
        # Group all clips for this story under a per-story subfolder for sanity
        sub_name = f"resources_{re.sub(r'[^A-Za-z0-9._-]+', '_', story_id)}"
        drive_subfolder_id = _ensure_subfolder(drive_parent_id, sub_name, drive=drive)
        if drive_subfolder_id:
            drive_folder_link = f"https://drive.google.com/drive/folders/{drive_subfolder_id}"

    items_by_id = {it["id"]: it for it in manifest.get("items", [])}
    candidate_entries_for_email: list[dict] = []
    research_query_for_email = ""

    for job in manifest.get("jobs", []):
        req = job.get("request") or {}
        job_type = job.get("type") or req.get("type")
        item_id = job.get("output_item_id")
        item = items_by_id.get(item_id, {})

        # ── Flow A — direct URL download ──────────────────────────────────────
        if job_type == "download_note_link":
            url = req.get("source_url") or item.get("source_url", "")
            if not url:
                job["status"] = "error"
                job["error"] = "no source_url"
                continue
            print(f"  [resource_router] Flow A: download {url}")
            res = video_downloader.download_url(
                url,
                staging=staging_root,
                filename_hint=item_id or "clip",
            )
            entry_kwargs = {
                "source_url": url,
                "story_id": story_id,
                "local_path": res.get("local_path", ""),
                "duration_sec": res.get("duration_sec", 0.0),
                "title": res.get("title", ""),
                "flow": "A",
                "target_slide": item.get("target_slide") or req.get("target_slide"),
                "suggested_cut_start": None,
                "search_query": "",
            }
            if res.get("ok"):
                upload = upload_clip_to_drive(
                    res["local_path"], drive_subfolder_id or drive_parent_id, drive=drive
                )
                entry_kwargs["status"] = "STAGED"
                entry_kwargs["drive_file_id"] = upload.get("id", "")
                entry_kwargs["drive_view_link"] = upload.get("webViewLink", "")
                job["status"] = "done"
                if item:
                    item["status"] = "downloaded"
                    item["local_path"] = res["local_path"]
                    item["drive_file_id"] = upload.get("id", "")
                    item["duration_sec"] = res.get("duration_sec", 0.0)
            else:
                entry_kwargs["status"] = "DOWNLOAD_FAILED"
                entry_kwargs["error"] = res.get("error", "")
                job["status"] = "error"
                job["error"] = res.get("error", "")
                log_pipeline_failure(
                    story_id=story_id,
                    project=niche,
                    stage="download_note_link",
                    source_url=url,
                    error=res.get("error", ""),
                )
                if item:
                    item["status"] = "download_failed"
                    item["error"] = res.get("error", "")
            try:
                clips_manifest.upsert(staging_root, clips_manifest.make_entry(**entry_kwargs))
            except Exception as exc:
                print(f"  [resource_router] clips.json write failed: {exc}")

        # ── Flow B — research mode (ytsearch5 → top 3) ────────────────────────
        elif job_type == "research_videos":
            query = (req.get("query") or "").strip()
            if not query:
                job["status"] = "error"
                job["error"] = "no query"
                continue
            print(f"  [resource_router] Flow B: ytsearch for '{query[:80]}'")
            research_query_for_email = query
            search_results = video_downloader.search_youtube(
                query,
                n_results=3,
                search_size=5,
                staging=staging_root / "search",
            )
            for res in search_results:
                if not res.get("ok"):
                    print(f"  [resource_router] candidate failed: {res.get('error','')[:120]}")
                    continue
                upload = upload_clip_to_drive(
                    res["local_path"], drive_subfolder_id or drive_parent_id, drive=drive
                )
                entry = clips_manifest.make_entry(
                    source_url=res.get("source_url", ""),
                    story_id=story_id,
                    local_path=res.get("local_path", ""),
                    duration_sec=res.get("duration_sec", 0.0),
                    status="CANDIDATE",
                    flow="B",
                    title=res.get("title", ""),
                    search_query=query,
                    target_slide=None,
                    suggested_cut_start=None,
                    drive_file_id=upload.get("id", ""),
                    drive_view_link=upload.get("webViewLink", ""),
                )
                try:
                    clips_manifest.upsert(staging_root, entry)
                except Exception as exc:
                    print(f"  [resource_router] clips.json write failed: {exc}")
                candidate_entries_for_email.append(entry)
            job["status"] = "done" if candidate_entries_for_email else "no_results"
            if not candidate_entries_for_email:
                log_pipeline_failure(
                    story_id=story_id,
                    project=niche,
                    stage="research_videos",
                    source_url="",
                    error="no successful candidates downloaded",
                )

        else:
            # research_images / unsupported — leave for later
            job["status"] = job.get("status") or "skipped"
            job["error"] = job.get("error") or f"unsupported job type: {job_type}"

    # Upload the full clips.json itself to Drive for visibility
    try:
        local_manifest_path = clips_manifest.manifest_path(staging_root)
        if local_manifest_path.exists() and (drive_subfolder_id or drive_parent_id):
            upload_clip_to_drive(
                str(local_manifest_path),
                drive_subfolder_id or drive_parent_id,
                drive=drive,
            )
    except Exception as exc:
        print(f"  [resource_router] clips.json upload failed: {exc}")

    # Email approval for Flow B candidates
    if send_emails and candidate_entries_for_email:
        to_addr = os.environ.get("RESOURCE_APPROVAL_EMAIL", "priscila@oakpark-construction.com")
        subj, body = _format_candidate_email(
            story_id=story_id,
            project=niche,
            query=research_query_for_email,
            clips=candidate_entries_for_email,
            folder_link=drive_folder_link,
        )
        _trigger_send_email(to=to_addr, subject=subj, body=body)

    manifest["execution"] = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "staging_dir": str(staging_root),
        "drive_folder_id": drive_subfolder_id or drive_parent_id or "",
        "drive_folder_link": drive_folder_link,
        "candidates_emailed": len(candidate_entries_for_email),
    }
    return manifest


def route_and_execute(
    *,
    story_id: str,
    project: str,
    notes: str,
    transcript: str = "",
    seed_url: str = "",
    topic: str = "",
    output_dir: str | os.PathLike[str] = "transcripts",
    use_llm: bool = False,
    send_emails: bool = True,
    clips_dir: Path | str | None = None,
) -> dict:
    """Build manifest, write it, then execute the jobs."""
    manifest = route_capture_resources(
        story_id=story_id,
        project=project,
        notes=notes,
        transcript=transcript,
        seed_url=seed_url,
        topic=topic,
        output_dir=output_dir,
        use_llm=use_llm,
    )
    if not manifest.get("jobs"):
        manifest["execution"] = {"ran_at": datetime.now(timezone.utc).isoformat(),
                                 "note": "no jobs to execute"}
        return manifest
    execute_resource_jobs(
        manifest,
        project=project,
        clips_dir=clips_dir,
        send_emails=send_emails,
    )
    # Re-write manifest with execution metadata
    write_resource_manifest(manifest, output_dir)
    return manifest


def _has_failed_jobs(result: dict) -> bool:
    jobs = result.get("jobs") or []
    if not jobs:
        return False
    failed_statuses = {"error", "download_failed", "no_results"}
    return any((job.get("status") or "").lower() in failed_statuses for job in jobs)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--story-id", required=True)
    p.add_argument("--project", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--transcript", default="")
    p.add_argument("--seed-url", default="")
    p.add_argument("--topic", default="")
    p.add_argument("--output-dir", default="transcripts")
    p.add_argument("--execute", action="store_true",
                   help="Actually download/upload/email — not just write the manifest")
    p.add_argument("--no-email", action="store_true")
    p.add_argument("--clips-dir", default="",
                   help="Override staging dir for downloads (default /tmp/clips/<story_id>)")
    args = p.parse_args()

    if args.execute:
        result = route_and_execute(
            story_id=args.story_id,
            project=args.project,
            notes=args.notes,
            transcript=args.transcript,
            seed_url=args.seed_url,
            topic=args.topic,
            output_dir=args.output_dir,
            use_llm=False,
            send_emails=not args.no_email,
            clips_dir=args.clips_dir or None,
        )
    else:
        result = route_capture_resources(
            story_id=args.story_id,
            project=args.project,
            notes=args.notes,
            transcript=args.transcript,
            seed_url=args.seed_url,
            topic=args.topic,
            output_dir=args.output_dir,
            use_llm=False,
        )

    print(json.dumps({
        "manifest_path": result.get("manifest_path"),
        "jobs": len(result.get("jobs", [])),
        "items": len(result.get("items", [])),
        "labels": result.get("intent", {}).get("intent_labels", []),
        "execution": result.get("execution"),
    }, indent=2))

    if args.execute and _has_failed_jobs(result):
        log_pipeline_failure(
            story_id=args.story_id,
            project=args.project,
            stage="resource_router_cli",
            source_url=args.seed_url,
            error="one or more resource jobs failed",
        )
        sys.exit(1)
