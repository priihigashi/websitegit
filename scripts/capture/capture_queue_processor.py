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
MAX_PER_RUN   = int(os.getenv("QUEUE_MAX_PER_RUN", "5"))
CAPTURE_SCRIPT = "scripts/capture/capture_pipeline.py"

STATUS_TO_SCORE = {"READY": 5, "NEEDS_REVIEW": 3, "NOT_RELEVANT": 1}
PROJECT_TO_DEST = {
    "brazil":    "Brazil News Drive",
    "usa":       "Inspiration Library",
    "book":      "Book Tracker",
    "content":   "Inspiration Library",   # legacy alias
    "sovereign": "Brazil News Drive",     # legacy alias → use brazil
}


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

    # Score from classification status (content project only)
    m = re.search(r'Status:\s*(\w+)', stdout)
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

    moved_to = PROJECT_TO_DEST.get(project, "Inspiration Library")
    return score, moved_to, hub_path


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    token = _get_token()
    rows  = _read_queue(token)
    print(f"[capture_queue] {len(rows)} rows found in '{QUEUE_TAB}'")

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

        # Build subprocess command — same args as capture_pipeline.yml run step
        cmd = [sys.executable, CAPTURE_SCRIPT, url, "--project", project]
        if comment:
            cmd += ["--notes", comment]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1500,   # 25 min per capture (matches pipeline expectations)
                env={**os.environ},
            )
            combined = result.stdout + "\n" + result.stderr
            # Log tail so GitHub Actions shows progress
            print(combined[-3000:] if len(combined) > 3000 else combined)

            if result.returncode == 0:
                score, dest, hub = _parse_result(combined, project)
                _write_success(token, sheet_row, score, dest, hub)
                print(f"  ✓ DONE — score={score}, dest={dest}, hub={hub[:70] if hub else '(none)'}")
                try:
                    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
                    from content_tracker import log_run
                    log_run(pipeline="capture_queue", trigger="queue", url=url,
                            project=project, status="success", score=score,
                            drive_path=hub or "", notes=f"queue row {sheet_row}")
                except Exception: pass
            else:
                err_msg = (result.stderr or result.stdout or "unknown error")[-200:]
                _write_failure(token, sheet_row, f"rc={result.returncode}: {err_msg}")
                print(f"  ✗ FAILED — rc={result.returncode}")

        except subprocess.TimeoutExpired:
            _write_failure(token, sheet_row, "Timeout after 25 minutes")
            print(f"  ✗ TIMEOUT after 25 min")

        except Exception as exc:
            _write_failure(token, sheet_row, str(exc)[:200])
            print(f"  ✗ EXCEPTION: {exc}")

        processed_count += 1
        time.sleep(2)  # brief pause between captures to avoid rate limits

    print(f"\n[capture_queue] Done. {processed_count} URLs processed.")


if __name__ == "__main__":
    main()
