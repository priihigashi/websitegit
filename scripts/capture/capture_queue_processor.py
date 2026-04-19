"""
capture_queue_processor.py
==========================
Daily processor for the '📲 Capture Queue' tab.

Reads unprocessed rows, runs Capture Pipeline v2 inline (subprocess),
then writes PROCESSED / SCORE / MOVED TO / HUB DOC PATH back to the sheet.

Tab columns (A–H, 1-indexed):
  A = DATE
  B = LINK (URL to capture)
  C = COMMENT / NOTES (passed as --notes to pipeline)
  D = PROCESSED  (checkbox — TRUE when done, empty when pending)
  E = SCORE      (1-5 derived from pipeline classification)
  F = MOVED TO   (destination name or "⚠️ Pipeline failed" on error)
  G = HUB DOC PATH (folder / doc URL from pipeline)
  H = PROJECT    (brazil | usa | book — default: content)

Skip rules:
  - Skip if D == "TRUE" (already processed)
  - Skip if F starts with "⚠️"  (previously failed — clear F to retry)

Env vars (all already in oak-park-ai-hub GitHub Secrets):
  SHEETS_TOKEN            — OAuth refresh token JSON
  OPENAI_API_KEY          — forwarded to capture_pipeline.py
  ANTHROPIC_API_KEY       — forwarded to capture_pipeline.py
  APIFY_API_KEY           — forwarded to capture_pipeline.py (optional)
  PRI_OP_GMAIL_APP_PASSWORD — forwarded to capture_pipeline.py (optional)
"""

import os
import sys
import json
import re
import subprocess
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SHEET_ID  = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
QUEUE_TAB = "📲 Capture Queue"
MAX_PER_RUN    = int(os.getenv("QUEUE_MAX_PER_RUN", "5"))
RETRY_FAILED   = os.getenv("QUEUE_RETRY_FAILED", "false").lower() in ("1", "true", "yes")
BULK_URLS_RAW  = os.getenv("QUEUE_BULK_URLS", "").strip()
CAPTURE_SCRIPT = "scripts/capture/capture_pipeline.py"

STATUS_TO_SCORE = {"READY": 5, "NEEDS_REVIEW": 3, "NOT_RELEVANT": 1}

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).parent.parent))
from routing import pipeline_project as _pipeline_project, queue_dest as _queue_dest


# ─── AUTH ─────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        sys.exit("ERROR: SHEETS_TOKEN not set")
    td = json.loads(raw)
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


# ─── SHEETS ───────────────────────────────────────────────────────────────────

def _read_queue(token: str) -> list:
    enc = urllib.parse.quote(f"'{QUEUE_TAB}'!A2:H", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    ).read())
    return resp.get("values", [])


def _write_success(token: str, row_num: int, score: int, moved_to: str, hub_path: str):
    """Mark row as processed — writes TRUE to D (checkbox) + fills E/F/G."""
    _batch_update(token, [
        (f"'{QUEUE_TAB}'!D{row_num}", True),   # boolean TRUE → checks the checkbox
        (f"'{QUEUE_TAB}'!E{row_num}", score),
        (f"'{QUEUE_TAB}'!F{row_num}", moved_to),
        (f"'{QUEUE_TAB}'!G{row_num}", hub_path),
    ])


def _write_failure(token: str, row_num: int, reason: str):
    """On failure: leave D empty (checkbox intact, will not retry next run due to ⚠️ in F)."""
    _batch_update(token, [
        (f"'{QUEUE_TAB}'!F{row_num}", f"⚠️ Pipeline failed"),
        (f"'{QUEUE_TAB}'!G{row_num}", reason[:200]),
    ])


def _batch_update(token: str, updates: list):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
    body = {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": rng, "values": [[val]]}
            for rng, val in updates
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization":  f"Bearer {token}",
            "Content-Type":   "application/json",
        },
    )
    urllib.request.urlopen(req).read()


def _clear_failure_flags(token: str, rows: list):
    """Clear ⚠️ from column F for all previously-failed rows so they get retried.
    Called when QUEUE_RETRY_FAILED=true before the main processing loop.
    """
    updates = []
    for i, row in enumerate(rows):
        row_padded = row + [""] * (8 - len(row))
        moved_to = row_padded[5].strip()
        if moved_to.startswith("⚠️"):
            sheet_row = i + 2
            updates.append((f"'{QUEUE_TAB}'!F{sheet_row}", ""))
            updates.append((f"'{QUEUE_TAB}'!G{sheet_row}", ""))
    if updates:
        _batch_update(token, updates)
        print(f"[capture_queue] retry_failed=true: cleared ⚠️ from {len(updates)//2} row(s)")
    else:
        print("[capture_queue] retry_failed=true: no failed rows to clear")


def _append_bulk_urls(token: str, urls: list):
    """Append each URL as a new row to the queue tab (DATE + LINK columns only).
    Duplicate detection: skip URLs already present in column B.
    Returns the refreshed rows list after appending.
    """
    if not urls:
        return

    # Read existing URLs to avoid duplicates
    existing_rows = _read_queue(token)
    existing_urls = set()
    for r in existing_rows:
        if len(r) > 1 and r[1].strip():
            existing_urls.add(r[1].strip().split("?")[0])  # normalise: strip query params

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    appended = 0
    for raw_url in urls:
        u = raw_url.strip()
        if not u:
            continue
        norm = u.split("?")[0]
        if norm in existing_urls:
            print(f"[capture_queue] bulk_urls: skipping duplicate {u[:60]}")
            continue
        # Append row: DATE | URL (leave C-H empty — will be filled by processor)
        enc = urllib.parse.quote(f"'{QUEUE_TAB}'!A:H", safe="!:'")
        append_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
            f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        )
        body = {"values": [[today, u]]}
        req = urllib.request.Request(
            append_url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
        )
        urllib.request.urlopen(req).read()
        existing_urls.add(norm)
        appended += 1
        print(f"[capture_queue] bulk_urls: added row → {u[:70]}")

    if appended:
        print(f"[capture_queue] bulk_urls: {appended} new URL(s) added to queue")


# ─── RESULT PARSING ───────────────────────────────────────────────────────────

def _parse_result(stdout: str, project: str) -> tuple[int, str, str]:
    """
    Extract score, moved_to, hub_path from capture_pipeline.py stdout.

    Content output block:
      CONTENT CAPTURE DONE
      Niche: ...
      Status: READY | NEEDS_REVIEW | NOT_RELEVANT
      Folder: https://...
      Brief: https://...

    Sovereign/Book output block:
      SOVEREIGN CAPTURE DONE  /  BOOK CAPTURE DONE
      Story ID: ...
      Doc: https://...

    Also checks the email notification lines:
      Content Hub: https://...
    """
    score    = 3  # safe default
    hub_path = ""

    # Score from classification status (opc project only).
    # Anchor to the capture DONE block — handles both current label (OPC) and legacy (CONTENT).
    m = re.search(r'(?:OPC|CONTENT) CAPTURE DONE.*?Status:\s*(\w+)', stdout, re.DOTALL)
    if m:
        score = STATUS_TO_SCORE.get(m.group(1).strip().upper(), 3)

    # Hub path — try Content Hub line first (most authoritative)
    m = re.search(r'Content Hub:\s*(https?://\S+)', stdout)
    if m:
        hub_path = m.group(1).strip()

    # Fallback: Folder line
    if not hub_path:
        m = re.search(r'Folder:\s*(https?://\S+)', stdout)
        if m:
            hub_path = m.group(1).strip()

    # Fallback: Doc line (sovereign/book)
    if not hub_path:
        m = re.search(r'Doc:\s*(https?://\S+)', stdout)
        if m:
            hub_path = m.group(1).strip()

    # Fallback: Brief line (content)
    if not hub_path:
        m = re.search(r'Brief:\s*(https?://\S+)', stdout)
        if m:
            hub_path = m.group(1).strip()

    moved_to = _queue_dest(project)
    return score, moved_to, hub_path


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    token = _get_token()

    # ── Bulk URL ingestion ─────────────────────────────────────────────────────
    # If QUEUE_BULK_URLS is set, parse the newline-separated list and append any
    # new URLs as rows before running the normal processing loop.
    if BULK_URLS_RAW:
        bulk_list = [u.strip() for u in BULK_URLS_RAW.splitlines() if u.strip()]
        print(f"[capture_queue] bulk_urls: {len(bulk_list)} URL(s) received")
        _append_bulk_urls(token, bulk_list)

    rows  = _read_queue(token)
    print(f"[capture_queue] {len(rows)} rows found in '{QUEUE_TAB}'")

    # ── Retry mode ─────────────────────────────────────────────────────────────
    # If QUEUE_RETRY_FAILED=true, clear the ⚠️ flag from all failed rows so the
    # loop below will pick them up and try again with the updated fallback chain.
    if RETRY_FAILED:
        _clear_failure_flags(token, rows)
        rows = _read_queue(token)  # re-read after clearing flags

    processed_count = 0
    for i, row in enumerate(rows):
        if processed_count >= MAX_PER_RUN:
            print(f"[capture_queue] Cap reached ({MAX_PER_RUN}), stopping.")
            break

        # Pad to 8 columns
        row = row + [""] * (8 - len(row))
        url       = row[1].strip()           # B — LINK
        comment   = row[2].strip()           # C — COMMENT
        processed = row[3].strip().upper()   # D — PROCESSED (checkbox)
        moved_to  = row[5].strip()           # F — MOVED TO  (used to detect prior failure)
        project   = row[7].strip() or "content"  # H — PROJECT

        sheet_row = i + 2  # 1-indexed, skip header

        if not url:
            continue

        # Skip already-processed rows
        if processed == "TRUE":
            continue

        # Skip rows that previously failed (⚠️ in F) — clear F cell to retry
        if moved_to.startswith("⚠️"):
            print(f"[capture_queue] Row {sheet_row}: skipping previously-failed row ({url[:50]})")
            continue

        print(f"\n[capture_queue] Row {sheet_row}: {url[:70]}")
        print(f"  project={project}  notes={'yes' if comment else 'none'}")

        pipeline_project = _pipeline_project(project)  # routing.py is the source of truth

        # Dispatch each URL as a separate capture_pipeline.yml workflow run.
        # Each run gets a fresh GitHub Actions runner IP, avoiding Instagram rate limits.
        try:
            subprocess.run([
                "gh", "workflow", "run", "capture_pipeline.yml",
                "--repo", "priihigashi/oak-park-ai-hub",
                "--field", f"url={url}",
                "--field", f"project={pipeline_project}",
            ], check=True, capture_output=True, text=True, timeout=30)
            moved_to = _queue_dest(project)
            _write_success(token, sheet_row, 3, moved_to, "")
            print(f"  ✓ DISPATCHED — project={pipeline_project}")

        except subprocess.TimeoutExpired:
            _write_failure(token, sheet_row, "Timeout dispatching workflow")
            print(f"  ✗ TIMEOUT dispatching")

        except subprocess.CalledProcessError as exc:
            err_msg = (exc.stderr or exc.stdout or "unknown error")[-200:]
            _write_failure(token, sheet_row, f"dispatch failed: {err_msg}")
            print(f"  ✗ DISPATCH FAILED — rc={exc.returncode}")

        except Exception as exc:
            _write_failure(token, sheet_row, str(exc)[:200])
            print(f"  ✗ EXCEPTION: {exc}")

        processed_count += 1
        time.sleep(2)  # brief pause between captures to avoid rate limits

    print(f"\n[capture_queue] Done. {processed_count} URLs processed.")


if __name__ == "__main__":
    main()
