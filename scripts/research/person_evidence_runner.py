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
from difflib import SequenceMatcher
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
    slugify, slugify_bounded, ALLOWED_SAME_PERSON_METHODS,
)
from route_state import reset_state, get_state  # noqa: E402
import routing  # noqa: E402

# Initialise the route-state singleton from FALLBACK_MODE env at module load.
# auto = paid first, cascade on failure (default).
# strict = fail if Apify/Anthropic unavailable.
# no_paid_anthropic_apify = skip paid routes entirely, OpenAI + web/YT/manual.
reset_state()

# ── env + constants ──────────────────────────────────────────────────────────
SHEETS_TOKEN_RAW = os.environ.get("SHEETS_TOKEN", "")
GHA_RUN_ID       = os.environ.get("GITHUB_RUN_ID", "")
IDEAS_INBOX_ID   = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
RUN_LOG_LINES: list[str] = []
PIPELINE_FAILURES: list[dict] = []  # FATAL only — flips workflow exit code.
ROUTE_FAILURES: list[dict] = []     # NON-FATAL route fallbacks — logged for visibility, run still succeeds.

# B6 audit fix — per-process header memo. _ensure_columns is called 3x per run
# (Clip Collections, Content Queue, Inspiration Library). Memoizing the
# verified-headers result collapses 6 Sheets API calls into 3 on the happy
# path and avoids the read-then-update race within a single run. Cross-
# process race is not addressed here — workflow_dispatch effectively
# serialises SH-104 runs, so it's not a practical concern. Cache resets
# per process. Key = (sheet_id, tab).
# REVERT recipe: delete this dict + the cache check/set in _ensure_columns
# (commented "B6 cache hit" / "B6 cache set"). Pure additive — `git revert`
# this commit is clean, no schema migrations.
_columns_cache: dict[tuple[str, str], list[str]] = {}

# B7 — defamation surface mitigation.
# Clip Collections is a Google Sheet that may be shared with collaborators
# or accidentally surfaced. Writing CLAIM_TYPE next to a named-person URL +
# QUOTE creates a defamation-shaped data point even when our internal label
# (e.g. "hypocrisy") is non-libelous. The full-fidelity classification stays
# in evidence_manifest.json (Drive — sharable per-folder). Mode toggle:
#
#   SH104_SHEET_REDACT_CLAIM_TYPE=1  (default ON)
#       For sensitive claim_types (group-targeting / dehumanizing /
#       moral-contradiction / hypocrisy) the sheet shows
#       "[review-required: see manifest]" instead of the raw label.
#       The actual claim type is still in NOTES so reviewers know what to
#       look at — just not in a ML-scrapeable column.
#
#   SH104_SHEET_REDACT_CLAIM_TYPE=0
#       Raw label is written. Use ONLY if the sheet is not shared with
#       anyone outside Priscila + (a future) review queue.
SH104_SHEET_REDACT_CLAIM_TYPE = os.environ.get("SH104_SHEET_REDACT_CLAIM_TYPE", "1") != "0"
SENSITIVE_CLAIM_TYPES = {
    "group-targeting", "dehumanizing", "moral-contradiction", "hypocrisy",
}
SHEET_REDACT_PLACEHOLDER = "[review-required: see manifest]"


def _outcome_status(candidates_transcribed: int, verified_count: int) -> str:
    if candidates_transcribed < 3:
        return "Needs Research — Transcription Blocked"
    if verified_count < 3:
        return "Needs Research — Evidence Weak"
    return "Ready for Manifest Review"


def _normalize_quote(s: str) -> str:
    """Lowercase + strip punctuation/whitespace for similarity comparison."""
    return re.sub(r"[^a-z0-9áàâãéêíóôõúüçñ]+", " ", (s or "").lower()).strip()


def _quote_similarity(a: str, b: str) -> float:
    """Return near-duplicate similarity for verified quote gatekeeping."""
    a_norm = _normalize_quote(a)
    b_norm = _normalize_quote(b)
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def _quote_substring_overlap(a: str, b: str) -> float:
    """Return the fraction of the SHORTER quote that appears verbatim in the
    LONGER one. Catches "same core quote with extra opening/trailing context"
    cases that pure similarity ratio misses (e.g. one clip adds a preface
    sentence to the same quote)."""
    a_norm = _normalize_quote(a)
    b_norm = _normalize_quote(b)
    if not a_norm or not b_norm:
        return 0.0
    shorter, longer = (a_norm, b_norm) if len(a_norm) <= len(b_norm) else (b_norm, a_norm)
    # Find the longest matching substring of the shorter quote inside the longer.
    matcher = SequenceMatcher(None, shorter, longer)
    block = matcher.find_longest_match(0, len(shorter), 0, len(longer))
    return block.size / len(shorter) if shorter else 0.0


def _duplicate_verified_quote_reason(score: dict, verified: list[dict]) -> str:
    """Near-identical best_quote entries count once toward the 3+ review gate.

    Two-signal dedupe: full-string similarity OR substring overlap. Catches both
    "same quote rephrased" (high ratio) and "same core sentence with extra
    framing" (high overlap, lower ratio).
    """
    quote = score.get("best_quote", "") if isinstance(score, dict) else ""
    if not quote.strip():
        return ""
    for item in verified or []:
        prev = (item.get("score") or {}).get("best_quote", "")
        if _quote_similarity(quote, prev) >= 0.78:
            return "duplicate_verified_quote"
        if _quote_substring_overlap(quote, prev) >= 0.70:
            return "duplicate_verified_quote_substring"
    return ""


def _log(msg: str):
    print(msg, flush=True)
    RUN_LOG_LINES.append(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")


def _write_failure_row(stage: str, err_str: str, fatal: bool) -> None:
    """Best-effort write to 🚨 Pipeline Failures tab. Used by both _fail and
    _log_route_failure. NOTE row column order matches existing tab schema."""
    try:
        from googleapiclient.discovery import build
        if not SHEETS_TOKEN_RAW:
            return
        creds = _creds_from_token()
        if not creds:
            return
        svc = build("sheets", "v4", credentials=creds)
        run_url = (f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{GHA_RUN_ID}"
                   if GHA_RUN_ID else "")
        note = "" if fatal else "route_fallback (non-fatal — run continued)"
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
                note,
            ]]},
        ).execute()
    except Exception as e:
        _log(f"  (failure-log write itself failed: {e})")


def _fail(stage: str, error):
    """FATAL failure — flips workflow exit code via PIPELINE_FAILURES list.
    Use for: drive/sheet write errors, code exceptions, missing required infra."""
    err_str = str(error)[:500]
    PIPELINE_FAILURES.append({"stage": stage, "error": err_str})
    _log(f"  ❌ FAILURE [{stage}]: {err_str[:200]}")
    _write_failure_row(stage, err_str, fatal=True)


def _log_route_failure(stage: str, error):
    """NON-FATAL route fallback — Apify quota, Anthropic quota, SerpAPI down.
    Logged to 🚨 tab + ROUTE_FAILURES so the manifest reports which routes
    cascaded; does NOT push to PIPELINE_FAILURES, so the workflow still
    exits 0 when only route fallbacks occurred."""
    err_str = str(error)[:500]
    ROUTE_FAILURES.append({"stage": stage, "error": err_str})
    _log(f"  ⚠️  ROUTE FALLBACK [{stage}]: {err_str[:200]} (run continues)")
    _write_failure_row(stage, err_str, fatal=False)


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


def _a1_col(n: int) -> str:
    """1-based column number -> A1 column letters."""
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out or "A"


def _header_range(tab: str, headers: list[str]) -> str:
    end = _a1_col(max(1, len(headers)))
    return f"'{tab}'!A:{end}"


def _read_existing_rows(svc, sheet_id: str, tab: str, headers: list[str]) -> list[dict]:
    """Return existing rows as dicts keyed by header. Empty list on any error."""
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=_header_range(tab, headers)
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
    Returns full ordered header list.

    Concurrency note (B6): this is a read-then-update against headers row.
    workflow_dispatch effectively serialises SH-104 runs (only one at a time
    is realistic), and this script is the only writer to these specific
    columns, so a TOCTOU race is theoretical not practical. As a belt-and-
    suspenders mitigation we re-fetch headers once after the update — if a
    concurrent writer added another column meanwhile, we surface a warning
    so the next run picks up the merged set rather than silently clobbering.
    """
    # B6 cache hit — return previously verified headers if all required
    # cols are still present. Skips the headers read entirely.
    cache_key = (sheet_id, tab)
    cached = _columns_cache.get(cache_key)
    if cached is not None and all(c in cached for c in required):
        return cached
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
            # Re-read to detect concurrent column additions; if changed,
            # log and trust the latest server state.
            try:
                res2 = svc.spreadsheets().values().get(
                    spreadsheetId=sheet_id, range=f"'{tab}'!1:1"
                ).execute()
                live_headers = res2.get("values", [[]])[0] if res2.get("values") else []
                if live_headers and live_headers != new_headers:
                    _log(f"  ({tab}) header race detected — using server headers")
                    _columns_cache[cache_key] = live_headers  # B6 cache set
                    return live_headers
            except Exception:
                pass
            _columns_cache[cache_key] = new_headers  # B6 cache set
            return new_headers
        _columns_cache[cache_key] = headers  # B6 cache set
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
        # B7 — redact sensitive claim_types in the sheet (manifest keeps truth).
        raw_claim = s.get("claim_type", "") or ""
        notes_raw = s.get("why", "")[:300]
        if SH104_SHEET_REDACT_CLAIM_TYPE and raw_claim in SENSITIVE_CLAIM_TYPES:
            sheet_claim = SHEET_REDACT_PLACEHOLDER
            notes_for_sheet = (f"[claim_type:{raw_claim}] {notes_raw}").strip()[:300]
        else:
            sheet_claim = raw_claim
            notes_for_sheet = notes_raw
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
            "CLAIM_TYPE": sheet_claim,
            "SAFE_TO_USE": s.get("safe_to_use", False),
            "MANIFEST_URL": manifest_url,
            "STATUS": "verified",
            "NOTES": notes_for_sheet,
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
                         verified_count: int, target_count: int,
                         transcribed_count: int):
    """Append/update 📋 Content Queue with outcome-specific SH-104 status."""
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
    status = _outcome_status(transcribed_count, verified_count)
    row_dict = {
        "DATE": today,
        "NICHE": niche,
        "TITLE": title,
        "SOURCE": "person_evidence_mining",
        "STATUS": status,
        "MANIFEST_URL": manifest_url,
        "VERIFIED_COUNT": verified_count,
        "TARGET_COUNT": target_count,
        "NOTES": f"Transcribed={transcribed_count}. Phase 1 manifest only. Render gate: manual approval after review.",
    }
    row = [row_dict.get(h, "") for h in headers]

    # Idempotency guard — key = (SOURCE=person_evidence_mining, TITLE, NICHE).
    # On retry, UPDATE the existing row (refresh manifest_url + counts +
    # status), don't append a duplicate. Skip update if status is Rejected.
    try:
        res = svc.spreadsheets().values().get(
            spreadsheetId=IDEAS_INBOX_ID, range=_header_range(tab, headers)
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
                range=f"'{tab}'!A{target_row_idx}:{_a1_col(len(headers))}{target_row_idx}",
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
            _log(f"  Content Queue: row {target_row_idx} updated ({status}, retry)")
        else:
            svc.spreadsheets().values().append(
                spreadsheetId=IDEAS_INBOX_ID,
                range=_header_range(tab, headers),
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
            _log(f"  Content Queue: row appended ({status})")
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

def _send_email_via_workflow(subject: str, body: str) -> bool:
    """Route B — trigger send_email.yml. Works inside GHA when GITHUB_TOKEN
    env var is set (capture_pipeline.yml passes ${{ github.token }}).
    Returns True if dispatch accepted."""
    import shutil, subprocess
    gh = shutil.which("gh") or os.path.expanduser("~/bin/gh")
    if not os.path.exists(gh):
        return False
    try:
        r = subprocess.run(
            [gh, "workflow", "run", "send_email.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", "to=priscila@oakpark-construction.com",
             "-f", f"subject={subject}",
             "-f", f"body={body}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True
        _log(f"  send_email.yml dispatch rc={r.returncode}: {(r.stderr or r.stdout)[:200]}")
        return False
    except Exception as e:
        _log(f"  send_email.yml dispatch exception: {e}")
        return False


def _send_email_via_smtplib(subject: str, body: str) -> bool:
    """Route C — direct SMTP. Requires PRI_OP_GMAIL_APP_PASSWORD env var."""
    import smtplib
    from email.message import EmailMessage
    pwd = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")
    if not pwd:
        return False
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
        return True
    except Exception as e:
        _log(f"  smtplib send failed: {e}")
        return False


def _send_email_summary(person_name: str, niche: str, seed_url: str,
                        manifest_url: str, verified: list[dict], rejected: list[dict],
                        candidates_collected: int, candidates_transcribed: int):
    """Email summary — 2 send routes implemented in this CI runner.

    CLAUDE.md catalogues 3 routes overall:
      Route A — Gmail MCP DRAFT (IDE-only; NOT callable from a GitHub Actions
                runner, so deliberately not implemented here).
      Route B — send_email.yml workflow_dispatch via gh CLI (preferred from CI).
      Route C — smtplib SMTP_SSL direct (always-available fallback).

    Implemented here: B → C cascade. If both fail, _fail() records to the
    🚨 Pipeline Failures tab. Route A is documented but absent because there
    is no MCP host inside the runner.
    """
    state_snap = get_state().snapshot()
    status = _outcome_status(candidates_transcribed, len(verified))
    needs_research = status.startswith("Needs Research")
    subject = f"[SH-104] {status} — {person_name} — {niche}"
    top3_lines = []
    for v in verified[:3]:
        s = v.get("score", {})
        top3_lines.append(
            f"  • [{s.get('claim_type','?')}] \"{s.get('best_quote','')[:140]}…\""
            f" ({s.get('timestamp_start','?')}-{s.get('timestamp_end','?')})"
            f" safe_to_use={s.get('safe_to_use', False)}"
        )

    # Compact one-line route summary for the email body.
    rs = state_snap.get("route_status", {})
    routes_line = (
        f"fallback_mode={state_snap.get('fallback_mode','?')} | "
        f"apify={rs.get('apify','?')} anthropic={rs.get('anthropic','?')} "
        f"openai={rs.get('openai','?')} serpapi={rs.get('serpapi','?')} "
        f"ddg={rs.get('duckduckgo','?')} youtube={rs.get('youtube','?')} "
        f"manual={rs.get('manual_candidates',0)}"
    )
    needs_research_block = (
        f"\n⚠️  {status.upper()}.\n"
        "    Manifest is preserved. Render gate stays manual.\n"
        "    Re-run with broader requirement or paste manual candidate URLs.\n"
        if needs_research else ""
    )
    body = f"""Phase 1 evidence manifest ready for review.
{needs_research_block}
Person: {person_name}
Niche: {niche}
Seed: {seed_url}

Candidates collected: {candidates_collected}
Candidates transcribed: {candidates_transcribed}
Verified: {len(verified)}
Rejected: {len(rejected)}

Routes: {routes_line}

Top verified quotes:
""" + ("\n".join(top3_lines) if top3_lines else "  (none)") + f"""

Manifest: {manifest_url}

────────────────────────────────────────────
REPLY COMMANDS (just reply to this email with one line):

  APPROVE MANIFEST          — mark manifest approved in Content Queue
                              (no render auto-triggered)
  RENDER CAROUSEL           — dispatch clipmine_render.yml mode=carousel
  RENDER REMOTION           — dispatch clipmine_render.yml mode=remotion
  NEEDS MORE EVIDENCE       — flag in Content Queue for MANUAL re-dispatch
                              (does NOT auto-retrigger video-research.yml —
                              re-run with broader queries / higher
                              target_clip_count yourself)
  REJECT MANIFEST           — mark Rejected in Content Queue

Phase 3 render gate: APPROVE MANIFEST first, then RENDER CAROUSEL or
RENDER REMOTION. Final preview approval still required before posting.
────────────────────────────────────────────

NO render triggered. NO Buffer scheduling.

— SH-104 / FLOW_person_evidence_mining
"""
    # 2-route cascade in CI (Route A / Gmail MCP is IDE-only, not implementable
    # in a GitHub Actions runner). Route B preferred → Route C fallback.
    if _send_email_via_workflow(subject, body):
        _log("  Email summary dispatched via send_email.yml")
        return
    if _send_email_via_smtplib(subject, body):
        _log("  Email summary sent via smtplib")
        return
    _fail("email_summary", "all_email_routes_failed")


# ── main entry ───────────────────────────────────────────────────────────────

def run_person_evidence_mining(seed_url: str, person_name: str,
                               evidence_requirement: str,
                               target_clip_count: int = 6,
                               niche: str = "brazil") -> int:
    """Returns process exit code (0 ok, 1 if any failures recorded).

    Reads optional discovery hints from env (set by video-research.yml):
      DISCOVERY_NOTES        — free-text user context (Haiku extracts topics)
      DISCOVERY_KEYWORD_HINTS — comma-separated explicit topic keywords
      DISCOVERY_LANGUAGE      — "pt" (default) or "en"
    """
    _log(f"=== person_evidence_mining ===")
    _log(f"  seed_url: {seed_url}")
    _log(f"  person_name: {person_name}")
    _log(f"  niche: {niche}")
    _log(f"  target_clip_count: {target_clip_count}")
    _log(f"  evidence_requirement: {evidence_requirement[:200]}")

    notes = (os.environ.get("DISCOVERY_NOTES") or "").strip()
    hints_raw = (os.environ.get("DISCOVERY_KEYWORD_HINTS") or "").strip()
    keyword_hints = [h.strip() for h in re.split(r"[,;\n]+", hints_raw) if h.strip()] or None
    language = (os.environ.get("DISCOVERY_LANGUAGE") or "pt").lower()
    if language not in ("pt", "en"):
        language = "pt"
    if notes:
        _log(f"  notes: {notes[:160]}")
    if keyword_hints:
        _log(f"  keyword_hints: {keyword_hints}")
    _log(f"  language: {language}")

    # 1) Transcribe seed — soft failure: if the seed itself can't be transcribed,
    # the run still proceeds with empty seed_excerpt. The manifest will reflect
    # candidates_collected but call out the seed transcription gap in route_failures.
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
            _log_route_failure(stage, detail)
    except Exception as e:
        _log_route_failure("seed_transcribe", e)
    seed_excerpt = seed_transcript[:1500]

    # 2+3+4) Generate queries + collect candidates
    candidates, queries = collect_candidates(
        person_name=person_name,
        requirement=evidence_requirement,
        seed_excerpt=seed_excerpt,
        seed_url=seed_url,
        target_count=target_clip_count,
        notes=notes,
        keyword_hints=keyword_hints,
        language=language,
        on_failure=_log_route_failure,
    )
    _log(f"  Total candidates from search: {len(candidates)}")

    # Manual candidate URLs (from CANDIDATE_URLS env, newline-separated).
    # Inserted at the FRONT so they're scored first (likely user-curated).
    manual_raw = os.environ.get("CANDIDATE_URLS", "") or ""
    manual_urls = [u.strip() for u in manual_raw.replace("\r", "").split("\n") if u.strip()]
    if manual_urls:
        manual_cands = []
        for u in manual_urls:
            platform = (
                "youtube" if ("youtube.com" in u or "youtu.be" in u)
                else "instagram" if "instagram.com" in u
                else "tiktok" if "tiktok.com" in u
                else "other"
            )
            manual_cands.append({
                "platform": platform,
                "id": u.rsplit("/", 1)[-1].split("?")[0],
                "url": u,
                "title": "",
                "uploader": "",
                "duration": None,
                "upload_date": "",
                "query": "manual_candidate",
            })
        # Dedupe against existing search candidates by normalized URL.
        existing = { (c.get("url") or "").split("?")[0].rstrip("/").lower()
                     for c in candidates }
        new_manual = [c for c in manual_cands
                      if c["url"].split("?")[0].rstrip("/").lower() not in existing]
        get_state().increment_manual(len(new_manual))
        candidates = new_manual + candidates
        _log(f"  Manual candidates added: {len(new_manual)} (total: {len(candidates)})")

    # Cap how many we actually transcribe to keep costs bounded
    cap = max(target_clip_count * 4, 20)
    candidates = candidates[:cap]

    # Working dir for transcripts/manifests
    work_root = Path(f"/tmp/clipmine_{slugify(person_name)}_{slugify_bounded(evidence_requirement, 30)}")
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
                # 🚨 Pipeline Failures tab so they're not silent. These are
                # non-fatal at the candidate level: the cascade tried every
                # tier and returned empty for THIS one URL — the run still
                # produces a manifest from the candidates that did transcribe.
                if stage_detail in ("whisper", "apify_yt", "apify_ig", "ytdlp_audio"):
                    _log_route_failure(f"transcribe_candidate:{stage_detail}",
                                       err_detail or reason)
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
                on_failure=_log_route_failure,
            )
        except Exception as e:
            _fail(f"score_candidate:{cand.get('id','')}", e)
            rejected.append({"candidate": cand, "reason": f"score_exception: {e}"})
            continue

        ok, reasons = validate_score(score)
        # Honor safe_to_use too — even if validation passes, unsafe goes to rejected
        if ok and score.get("safe_to_use"):
            dup_reason = _duplicate_verified_quote_reason(score, verified)
            if dup_reason:
                rejected.append({
                    "candidate": cand,
                    "reason": dup_reason,
                    "score": score,
                })
                _log(f"  ⊘ rejected: {dup_reason}")
                continue
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
    # Inject route-state snapshot — record which paid/free routes were used,
    # which fell through, and why. Lets reviewers see at a glance whether
    # weaker results came from an Apify outage vs no candidates existing.
    manifest.update(get_state().snapshot())
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
    folder_name = f"clipmine_{slugify(person_name)}_{slugify_bounded(evidence_requirement, 30)}"
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
                         len(verified), target_clip_count, transcribed_count)
    _update_inspiration_library(seed_url, manifest_link or "")

    # 9) Email summary
    _send_email_summary(person_name, niche, seed_url, manifest_link or "(no link)",
                        verified, rejected, len(candidates), transcribed_count)

    _log(f"\n=== DONE ===")
    _log(f"  verified={len(verified)} rejected={len(rejected)} "
         f"candidates={len(candidates)} transcribed={transcribed_count}")
    snap = get_state().snapshot()
    _log(f"  fallback_mode={snap['fallback_mode']} routes={snap['route_status']}")
    if snap["route_failures"]:
        _log(f"  route_fallbacks={len(snap['route_failures'])} (non-fatal — see manifest)")
    status = _outcome_status(transcribed_count, len(verified))
    if status.startswith("Needs Research"):
        _log(f"  ⚠️  STATUS={status} (workflow exit still 0).")
    else:
        _log(f"  STATUS={status}")
    _log(f"  Phase 1 manifest only. Manual review required before render.")

    # Exit code policy: only FATAL failures (drive/sheet code errors etc.)
    # flip the workflow. Route fallbacks (Apify/Anthropic quota) do not.
    return 1 if PIPELINE_FAILURES else 0
