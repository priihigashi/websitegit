"""person_evidence_runner.py — orchestrator for --mode person_evidence_mining.

Pipeline:
  1. Transcribe seed URL (excerpt for context)
  2. Generate query buckets via Haiku
  3. Search YouTube + Instagram for candidates
  4. Dedupe candidates
  5. For each candidate: transcribe + score via Haiku rubric
  6. Build evidence_manifest.json
  7. Write to local /tmp + upload to Drive (Brazil/Captures/clipmine_<person>_<topic>/)
  8. Write rows: Inspiration Library (update seed) + Clip Collections (verified) + Content Queue (Needs Research)
  9. Email summary

Phase 1: NO render. Manifest only. Manual review gate.
"""

from __future__ import annotations
import json
import os
import re
import sys
import traceback
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Make this importable both as `research.person_evidence_runner` (when
# scripts/ is on sys.path) and as a script. Add own dir + parent dir to path.
_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
for _p in (_HERE, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from transcription import transcribe_url  # noqa: E402
from candidate_collectors import collect_candidates  # noqa: E402
from evidence_scoring import (  # noqa: E402
    score_candidate, validate_score, build_manifest, write_manifest,
    slugify, ALLOWED_SAME_PERSON_METHODS,
)
import routing  # noqa: E402

# ── env + constants ──────────────────────────────────────────────────────────
SHEETS_TOKEN_RAW = os.environ.get("SHEETS_TOKEN", "")
GHA_RUN_ID       = os.environ.get("GITHUB_RUN_ID", "")
IDEAS_INBOX_ID   = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
RUN_LOG_LINES: list[str] = []
PIPELINE_FAILURES: list[dict] = []  # mirrored to youtube_research.PIPELINE_FAILURES on exit


def _log(msg: str):
    print(msg, flush=True)
    RUN_LOG_LINES.append(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")


def _fail(stage: str, error):
    err_str = str(error)[:500]
    PIPELINE_FAILURES.append({"stage": stage, "error": err_str})
    _log(f"  ❌ FAILURE [{stage}]: {err_str[:200]}")
    # Best-effort write to 🚨 Pipeline Failures tab
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        if not SHEETS_TOKEN_RAW:
            return
        creds = _creds_from_token()
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
                "video-research.yml (person_evidence_mining)",
                GHA_RUN_ID,
                stage,
                err_str,
                run_url,
                "",
                "",
            ]]},
        ).execute()
    except Exception as e:
        _log(f"  (failure-log write itself failed: {e})")


def _creds_from_token():
    """Build google credentials from SHEETS_TOKEN secret (OAuth user creds JSON)."""
    if not SHEETS_TOKEN_RAW:
        return None
    try:
        from google.oauth2.credentials import Credentials
        info = json.loads(SHEETS_TOKEN_RAW)
        return Credentials.from_authorized_user_info(info, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
    except Exception as e:
        _log(f"  Could not load SHEETS_TOKEN creds: {e}")
        return None


# ── Drive upload ─────────────────────────────────────────────────────────────

def _drive_upload_folder(parent_id: str, folder_name: str) -> str:
    from googleapiclient.discovery import build
    creds = _creds_from_token()
    if not creds:
        return ""
    svc = build("drive", "v3", credentials=creds)
    # Reuse existing folder if present
    try:
        q = (f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' "
             f"and '{parent_id}' in parents and trashed = false")
        res = svc.files().list(q=q, fields="files(id,name)", supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute()
        if res.get("files"):
            return res["files"][0]["id"]
        f = svc.files().create(
            body={"name": folder_name,
                  "mimeType": "application/vnd.google-apps.folder",
                  "parents": [parent_id]},
            supportsAllDrives=True, fields="id",
        ).execute()
        return f["id"]
    except Exception as e:
        _fail("drive_create_folder", e)
        return ""


def _drive_upload_file(parent_id: str, local_path: str, mimetype: str = "application/json") -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = _creds_from_token()
    if not creds:
        return ""
    svc = build("drive", "v3", credentials=creds)
    try:
        f = svc.files().create(
            body={"name": os.path.basename(local_path), "parents": [parent_id]},
            media_body=MediaFileUpload(local_path, mimetype=mimetype),
            supportsAllDrives=True, fields="id,webViewLink",
        ).execute()
        return f.get("webViewLink", "")
    except Exception as e:
        _fail(f"drive_upload:{os.path.basename(local_path)}", e)
        return ""


# ── Sheet writes ─────────────────────────────────────────────────────────────

def _sheet_svc():
    from googleapiclient.discovery import build
    creds = _creds_from_token()
    return build("sheets", "v4", credentials=creds) if creds else None


def _norm_url(u: str) -> str:
    """Stable key for dedup — strip query string + trailing slash, lowercase."""
    return (u or "").split("?")[0].rstrip("/").lower()


def _read_existing_rows(svc, sheet_id: str, tab: str, headers: list[str]) -> list[dict]:
    """Return existing rows as dicts keyed by header. Empty list on any error."""
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{tab}'!A:Z"
        ).execute()
        rows = res.get("values", []) or []
        out = []
        for r in rows[1:]:
            d = {h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)}
            out.append(d)
        return out
    except Exception as e:
        _log(f"  ({tab}) read for dedup failed: {e}")
        return []


def _ensure_columns(svc, sheet_id: str, tab: str, required: list[str]) -> list[str]:
    """Guarantee `required` columns exist as headers. Adds at the end if missing.
    Returns full ordered header list."""
    try:
        res = svc.spreadsheets().values().get(spreadsheetId=sheet_id, range=f"'{tab}'!1:1").execute()
        headers = res.get("values", [[]])[0] if res.get("values") else []
        missing = [c for c in required if c not in headers]
        if missing:
            new_headers = headers + missing
            svc.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{tab}'!1:1",
                valueInputOption="USER_ENTERED",
                body={"values": [new_headers]},
            ).execute()
            return new_headers
        return headers
    except Exception as e:
        _fail(f"ensure_columns:{tab}", e)
        return []


def _write_clip_collections(verified: list[dict], person_name: str,
                            niche: str, manifest_url: str):
    """Append verified clips to 📋 Clip Collections tab in Ideas & Inbox."""
    svc = _sheet_svc()
    if not svc:
        _fail("clip_collections", "no_sheets_creds")
        return
    tab = "📋 Clip Collections"
    required = [
        "DATE", "NICHE", "TOPIC", "SOURCE", "URL", "TITLE",
        "QUOTE", "TIMESTAMP_START", "TIMESTAMP_END",
        "MATCH_SCORE", "CLAIM_TYPE", "SAFE_TO_USE", "MANIFEST_URL",
        "STATUS", "NOTES",
    ]
    headers = _ensure_columns(svc, IDEAS_INBOX_ID, tab, required)
    if not headers:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    topic = f"{person_name} evidence clip set"

    # Idempotency guard — key = (normalized URL, person/topic). On retry,
    # skip rows that already exist for this same clip + person.
    existing = _read_existing_rows(svc, IDEAS_INBOX_ID, tab, headers)
    person_norm = (person_name or "").strip().lower()
    seen_keys = set()
    for ex in existing:
        if (ex.get("SOURCE", "").strip() == "person_evidence_mining"
                and person_norm in (ex.get("TOPIC", "") or "").lower()):
            seen_keys.add(_norm_url(ex.get("URL", "")))

    rows = []
    skipped_dup = 0
    for v in verified:
        c = v.get("candidate", {})
        s = v.get("score", {})
        url_key = _norm_url(c.get("url", ""))
        if url_key and url_key in seen_keys:
            skipped_dup += 1
            continue
        seen_keys.add(url_key)
        # Build a row keyed by header name (SCRIPTS ADD NEVER DELETE rule)
        row_dict = {
            "DATE": today,
            "NICHE": niche,
            "TOPIC": topic,
            "SOURCE": "person_evidence_mining",
            "URL": c.get("url", ""),
            "TITLE": c.get("title", "")[:200],
            "QUOTE": s.get("best_quote", "")[:500],
            "TIMESTAMP_START": s.get("timestamp_start", ""),
            "TIMESTAMP_END": s.get("timestamp_end", ""),
            "MATCH_SCORE": s.get("match_score", 0.0),
            "CLAIM_TYPE": s.get("claim_type", ""),
            "SAFE_TO_USE": s.get("safe_to_use", False),
            "MANIFEST_URL": manifest_url,
            "STATUS": "verified",
            "NOTES": s.get("why", "")[:300],
        }
        rows.append([row_dict.get(h, "") for h in headers])
    if skipped_dup:
        _log(f"  Clip Collections: skipped {skipped_dup} duplicate clip(s)")
    if not rows:
        _log("  Clip Collections: nothing new to append")
        return
    try:
        svc.spreadsheets().values().append(
            spreadsheetId=IDEAS_INBOX_ID,
            range=f"'{tab}'!A:Z",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        _log(f"  Clip Collections: appended {len(rows)} verified clips")
    except Exception as e:
        _fail("clip_collections_append", e)


def _write_content_queue(person_name: str, niche: str, manifest_url: str,
                         verified_count: int, target_count: int):
    """Append a row to 📋 Content Queue with status=Needs Research."""
    svc = _sheet_svc()
    if not svc:
        return
    tab = "📋 Content Queue"
    required = [
        "DATE", "NICHE", "TITLE", "SOURCE", "STATUS",
        "MANIFEST_URL", "VERIFIED_COUNT", "TARGET_COUNT", "NOTES",
    ]
    headers = _ensure_columns(svc, IDEAS_INBOX_ID, tab, required)
    if not headers:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    title = f"{person_name} — evidence clip set (Phase 1 manifest)"
    row_dict = {
        "DATE": today,
        "NICHE": niche,
        "TITLE": title,
        "SOURCE": "person_evidence_mining",
        "STATUS": "Needs Research",
        "MANIFEST_URL": manifest_url,
        "VERIFIED_COUNT": verified_count,
        "TARGET_COUNT": target_count,
        "NOTES": "Phase 1 manifest only. Render gate: manual approval after review.",
    }
    row = [row_dict.get(h, "") for h in headers]

    # Idempotency guard — key = (SOURCE=person_evidence_mining, TITLE, NICHE).
    # On retry, UPDATE the existing row (refresh manifest_url + counts +
    # status), don't append a duplicate. Skip update if status is Rejected.
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=IDEAS_INBOX_ID, range=f"'{tab}'!A:Z"
        ).execute()
        existing_rows = res.get("values", []) or []
        target_row_idx = None  # 1-based row in sheet
        for ri, r in enumerate(existing_rows[1:], start=2):
            r_dict = {h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)}
            if (r_dict.get("SOURCE", "").strip() == "person_evidence_mining"
                    and r_dict.get("TITLE", "").strip() == title
                    and r_dict.get("NICHE", "").strip() == niche
                    and r_dict.get("STATUS", "").strip().lower() != "rejected"):
                target_row_idx = ri
                break
    except Exception as e:
        _log(f"  Content Queue: dedup read failed ({e}) — will append")
        target_row_idx = None

    try:
        if target_row_idx is not None:
            svc.spreadsheets().values().update(
                spreadsheetId=IDEAS_INBOX_ID,
                range=f"'{tab}'!A{target_row_idx}:Z{target_row_idx}",
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
            _log(f"  Content Queue: row {target_row_idx} updated (Needs Research, retry)")
        else:
            svc.spreadsheets().values().append(
                spreadsheetId=IDEAS_INBOX_ID,
                range=f"'{tab}'!A:Z",
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
            _log("  Content Queue: row appended (Needs Research)")
    except Exception as e:
        _fail("content_queue_append", e)


def _update_inspiration_library(seed_url: str, manifest_url: str):
    """Find seed_url row in 📥 Inspiration Library, update STATUS + add MANIFEST_URL."""
    svc = _sheet_svc()
    if not svc:
        return
    tab = "📥 Inspiration Library"
    required = ["MANIFEST_URL", "STATUS"]
    headers = _ensure_columns(svc, IDEAS_INBOX_ID, tab, required)
    if not headers:
        return
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=IDEAS_INBOX_ID, range=f"'{tab}'!A:Z"
        ).execute()
        rows = res.get("values", [])
        url_col = None
        for i, h in enumerate(rows[0] if rows else []):
            if h.upper() in ("URL", "LINK", "SOURCE_URL"):
                url_col = i
                break
        if url_col is None:
            _fail("inspiration_lib", "no_url_column")
            return
        target_row = None
        clean_seed = seed_url.split("?")[0].rstrip("/").lower()
        for ri, row in enumerate(rows[1:], start=2):
            if len(row) > url_col:
                if row[url_col].split("?")[0].rstrip("/").lower() == clean_seed:
                    target_row = ri
                    break
        if not target_row:
            _log("  Seed URL not found in Inspiration Library — skipping update")
            return
        manifest_idx = headers.index("MANIFEST_URL") + 1
        status_idx = headers.index("STATUS") + 1
        col_letter = lambda n: chr(64 + n) if n <= 26 else chr(64 + (n - 1) // 26) + chr(65 + (n - 1) % 26)
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=IDEAS_INBOX_ID,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": f"'{tab}'!{col_letter(manifest_idx)}{target_row}",
                     "values": [[manifest_url]]},
                    {"range": f"'{tab}'!{col_letter(status_idx)}{target_row}",
                     "values": [["Researched"]]},
                ],
            },
        ).execute()
        _log(f"  Inspiration Library row {target_row} updated (manifest + status)")
    except Exception as e:
        _fail("inspiration_lib_update", e)


# ── Email summary ────────────────────────────────────────────────────────────

def _send_email_summary(person_name: str, niche: str, seed_url: str,
                        manifest_url: str, verified: list[dict], rejected: list[dict],
                        candidates_collected: int):
    import smtplib
    from email.message import EmailMessage
    pwd = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")
    if not pwd:
        _log("  PRI_OP_GMAIL_APP_PASSWORD not set — skipping email")
        return
    subject = f"[SH-104] Evidence manifest ready — {person_name} — {niche}"
    top3_lines = []
    for v in verified[:3]:
        s = v.get("score", {})
        top3_lines.append(
            f"  • [{s.get('claim_type','?')}] \"{s.get('best_quote','')[:140]}…\""
            f" ({s.get('timestamp_start','?')}-{s.get('timestamp_end','?')})"
            f" safe_to_use={s.get('safe_to_use', False)}"
        )
    body = f"""Phase 1 evidence manifest ready for review.

Person: {person_name}
Niche: {niche}
Seed: {seed_url}

Candidates collected: {candidates_collected}
Verified: {len(verified)}
Rejected: {len(rejected)}

Top verified quotes:
""" + ("\n".join(top3_lines) if top3_lines else "  (none)") + f"""

Manifest: {manifest_url}

────────────────────────────────────────────
REPLY COMMANDS (just reply to this email with one line):

  APPROVE MANIFEST          — accept manifest, ready for render
  RENDER CAROUSEL           — render carousel from verified clips
  RENDER REMOTION           — render Remotion video compilation
  NEEDS MORE EVIDENCE       — pipeline re-runs with broader queries
  REJECT MANIFEST           — discard, do not render

Phase 3 render gate: APPROVE MANIFEST first, then RENDER CAROUSEL or
RENDER REMOTION. Final preview approval still required before posting.
────────────────────────────────────────────

NO render triggered. NO Buffer scheduling.

— SH-104 / FLOW_person_evidence_mining
"""
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = "priscila@oakpark-construction.com"
        msg["To"] = "priscila@oakpark-construction.com"
        msg.set_content(body)
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        s.login("priscila@oakpark-construction.com", pwd)
        s.send_message(msg)
        s.quit()
        _log("  Email summary sent")
    except Exception as e:
        _fail("email_summary", e)


# ── main entry ───────────────────────────────────────────────────────────────

def run_person_evidence_mining(seed_url: str, person_name: str,
                               evidence_requirement: str,
                               target_clip_count: int = 6,
                               niche: str = "brazil") -> int:
    """Returns process exit code (0 ok, 1 if any failures recorded)."""
    _log(f"=== person_evidence_mining ===")
    _log(f"  seed_url: {seed_url}")
    _log(f"  person_name: {person_name}")
    _log(f"  niche: {niche}")
    _log(f"  target_clip_count: {target_clip_count}")
    _log(f"  evidence_requirement: {evidence_requirement[:200]}")

    # 1) Transcribe seed
    seed_transcript = ""
    try:
        seed_result = transcribe_url(seed_url)
        seed_transcript = seed_result.get("transcript", "")
        _log(f"  Seed transcribed via {seed_result.get('source','?')}: "
             f"{len(seed_transcript)} chars")
        if seed_result.get("error"):
            trace = seed_result.get("error_trace")
            stage = f"seed_transcribe:{trace.get('stage')}" if trace else "seed_transcribe"
            detail = trace.get("error") if trace else seed_result["error"]
            _fail(stage, detail)
    except Exception as e:
        _fail("seed_transcribe", e)
    seed_excerpt = seed_transcript[:1500]

    # 2+3+4) Generate queries + collect candidates
    candidates, queries = collect_candidates(
        person_name=person_name,
        requirement=evidence_requirement,
        seed_excerpt=seed_excerpt,
        target_count=target_clip_count,
        on_failure=_fail,
    )
    _log(f"  Total candidates: {len(candidates)}")

    # Cap how many we actually transcribe to keep costs bounded
    cap = max(target_clip_count * 4, 20)
    candidates = candidates[:cap]

    # Working dir for transcripts/manifests
    work_root = Path(f"/tmp/clipmine_{slugify(person_name)}_{slugify(evidence_requirement)[:30]}")
    transcripts_dir = work_root / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    # 5) Transcribe + score each candidate
    verified: list[dict] = []
    rejected: list[dict] = []
    transcribed_count = 0
    for i, cand in enumerate(candidates, 1):
        _log(f"\n[{i}/{len(candidates)}] {cand.get('platform')} — {cand.get('title','')[:80]}")
        try:
            t = transcribe_url(cand["url"])
        except Exception as e:
            _fail(f"transcribe_candidate:{cand.get('id','')}", e)
            rejected.append({"candidate": cand, "reason": f"transcribe_exception: {e}"})
            continue
        transcript = t.get("transcript", "")
        if not transcript.strip():
            # Distinguish "Whisper/yt-dlp failure" from "video had no speech".
            # error_trace from transcription.py captures the last tier error
            # so reviewers can tell infra-down from genuinely-empty.
            trace = t.get("error_trace") or {}
            stage_detail = trace.get("stage", "")
            err_detail = trace.get("error", "")
            reason = t.get("error") or "no_transcript"
            if stage_detail:
                reason = f"{reason} [{stage_detail}: {err_detail}]"
                # Surface infra failures (Whisper/Apify/yt-dlp down) to the
                # 🚨 Pipeline Failures tab so they're not silent.
                if stage_detail in ("whisper", "apify_yt", "apify_ig", "ytdlp_audio"):
                    _fail(f"transcribe_candidate:{stage_detail}", err_detail or reason)
            rejected.append({"candidate": cand, "reason": reason,
                             "error_trace": trace or None})
            continue
        transcribed_count += 1
        # Save transcript
        try:
            tfile = transcripts_dir / f"{cand.get('platform','x')}_{cand.get('id','x')}.txt"
            tfile.write_text(transcript, encoding="utf-8")
        except Exception:
            pass

        try:
            score = score_candidate(
                candidate=cand,
                transcript=transcript,
                person_name=person_name,
                requirement=evidence_requirement,
                seed_excerpt=seed_excerpt,
                person_passed_by_user=bool(person_name),
            )
        except Exception as e:
            _fail(f"score_candidate:{cand.get('id','')}", e)
            rejected.append({"candidate": cand, "reason": f"score_exception: {e}"})
            continue

        ok, reasons = validate_score(score)
        # Honor safe_to_use too — even if validation passes, unsafe goes to rejected
        if ok and score.get("safe_to_use"):
            verified.append({"candidate": cand, "score": score})
            _log(f"  ✅ verified [{score['claim_type']}] match={score['match_score']:.2f}")
        else:
            rejected.append({
                "candidate": cand,
                "reason": "; ".join(reasons) if reasons else "unsafe_or_unverified",
                "score": score,
            })
            _log(f"  ⊘ rejected: {reasons or score.get('why','')[:80]}")

    # 6) Build manifest
    person_method = "user_passed" if person_name else "metadata"
    manifest = build_manifest(
        seed_url=seed_url, person_name=person_name,
        person_confidence=1.0 if person_name else 0.5,
        person_method=person_method,
        requirement=evidence_requirement, niche=niche,
        queries=queries,
        candidates_collected=len(candidates),
        candidates_transcribed=transcribed_count,
        verified=verified, rejected=rejected,
        seed_excerpt=seed_excerpt,
        run_id=GHA_RUN_ID,
        target_count=target_clip_count,
    )
    manifest_path = work_root / "evidence_manifest.json"
    write_manifest(str(manifest_path), manifest)
    _log(f"\n  Manifest written: {manifest_path}")

    # Also write scored_candidates.json (verified + rejected combined for full audit)
    scored_path = work_root / "scored_candidates.json"
    with open(scored_path, "w") as f:
        json.dump({
            "verified": verified,
            "rejected": rejected,
            "queries": queries,
        }, f, indent=2, ensure_ascii=False)

    # Run log
    log_path = work_root / "run.log"
    log_path.write_text("\n".join(RUN_LOG_LINES), encoding="utf-8")

    # 7) Drive upload
    parent_capture_id = routing.capture_folder(niche) or routing.capture_folder("brazil")
    folder_name = f"clipmine_{slugify(person_name)}_{slugify(evidence_requirement)[:30]}"
    drive_folder_id = _drive_upload_folder(parent_capture_id, folder_name)
    manifest_link = ""
    if drive_folder_id:
        manifest_link = _drive_upload_file(drive_folder_id, str(manifest_path), "application/json")
        _drive_upload_file(drive_folder_id, str(scored_path), "application/json")
        _drive_upload_file(drive_folder_id, str(log_path), "text/plain")
        # transcripts subfolder
        transcripts_drive = _drive_upload_folder(drive_folder_id, "transcripts")
        if transcripts_drive:
            for tfile in transcripts_dir.glob("*.txt"):
                _drive_upload_file(transcripts_drive, str(tfile), "text/plain")
        _log(f"  Drive folder: https://drive.google.com/drive/folders/{drive_folder_id}")
    else:
        _fail("drive_upload_root", "no_drive_folder_created")

    # 8) Sheet writes
    _write_clip_collections(verified, person_name, niche, manifest_link or "")
    _write_content_queue(person_name, niche, manifest_link or "",
                         len(verified), target_clip_count)
    _update_inspiration_library(seed_url, manifest_link or "")

    # 9) Email summary
    _send_email_summary(person_name, niche, seed_url, manifest_link or "(no link)",
                        verified, rejected, len(candidates))

    _log(f"\n=== DONE ===")
    _log(f"  verified={len(verified)} rejected={len(rejected)} "
         f"candidates={len(candidates)} transcribed={transcribed_count}")
    _log(f"  Phase 1 manifest only. Manual review required before render.")

    return 1 if PIPELINE_FAILURES else 0
