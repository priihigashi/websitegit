"""
pipeline_tracker_writer.py — sync Self-Heal Queue → Pipeline Fix Master Checklist

Modes:
  sync     : read all SH queue rows, update Master Checklist status for each SH-ID found
  credit   : append one row to Credit Blocks tab (for API credit failures)
  done     : mark a specific SH-ID as Done with evidence (direct update)

Usage:
  python scripts/pipeline_tracker_writer.py sync
  python scripts/pipeline_tracker_writer.py credit --workflow "capture_pipeline.yml" \
      --run-id "12345" --api "Anthropic" --error "credit balance too low" \
      --task "describe_with_claude_vision" --step "Capture / Intake"
  python scripts/pipeline_tracker_writer.py done --sh-id SH-007 \
      --done "Manual fix: replaced _get_token() with OAuth refresh" \
      --evidence "commit 2193698"
"""

import argparse
import json
import os
import sys
import datetime
from typing import Optional

# ── Auth ──────────────────────────────────────────────────────────────────────
def _get_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        raise SystemExit("SHEETS_TOKEN env var not set")

    data = json.loads(raw)
    creds = Credentials(
        token=data.get("token") or data.get("access_token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes") or ["https://www.googleapis.com/auth/spreadsheets"],
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

# ── Constants ─────────────────────────────────────────────────────────────────
QUEUE_SS   = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
QUEUE_TAB  = "🔧 Self-Heal Queue"

TRACKER_SS = "1yh9C7KU9OlqCdHNDI9mbZ6ldqLA3bAR3uENXUh37bkQ"
MC_TAB     = "Master Checklist"
CB_TAB     = "Credit Blocks"

TODAY = datetime.date.today().isoformat()

# Map SH queue status → tracker status
STATUS_MAP = {
    "DONE":        "Done",
    "BLOCKED":     "Blocked",
    "NEEDS-REVIEW":"Needs Verification",
    "PENDING":     "Not Started",
    "IN-PROGRESS": "Next",
    "USER-ONLY":   "Blocked",
}

# Master Checklist column indices (0-based)
MC_COL_SH_ID   = 5   # F — Queue ID / SH-ID
MC_COL_STATUS  = 7   # H — Status
MC_COL_DONE    = 9   # J — What Was Done
MC_COL_EVIDENCE= 10  # K — Evidence / Commit / Doc
MC_COL_UPDATED = 15  # P — Last Updated


def _sheets(creds):
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds)


# ── SYNC mode ─────────────────────────────────────────────────────────────────
def cmd_sync(svc):
    print("sync: reading Self-Heal Queue …")
    q = svc.spreadsheets().values().get(
        spreadsheetId=QUEUE_SS,
        range=f"'{QUEUE_TAB}'!A2:M200",
    ).execute().get("values", [])

    # Build a lookup: SH-ID → {status, last_result, fix_log}
    queue_map = {}
    for row in q:
        sh_id = (row[0] if row else "").strip()
        if not sh_id:
            continue
        queue_map[sh_id] = {
            "status":     (row[8]  if len(row) > 8  else "").strip(),
            "last_result":(row[11] if len(row) > 11 else "").strip(),
            "fix_log":    (row[12] if len(row) > 12 else "").strip(),
        }

    print(f"sync: {len(queue_map)} queue rows loaded")

    # Read Master Checklist
    mc = svc.spreadsheets().values().get(
        spreadsheetId=TRACKER_SS,
        range=f"'{MC_TAB}'!A2:Q200",
    ).execute().get("values", [])

    updates = []
    for row_idx, row in enumerate(mc, start=2):  # row 2 = first data row
        sh_id_cell = row[MC_COL_SH_ID].strip() if len(row) > MC_COL_SH_ID else ""
        if not sh_id_cell or sh_id_cell not in queue_map:
            continue

        q_data   = queue_map[sh_id_cell]
        new_status = STATUS_MAP.get(q_data["status"], "")
        if not new_status:
            continue

        current_status = row[MC_COL_STATUS].strip() if len(row) > MC_COL_STATUS else ""
        if current_status == new_status:
            continue  # already in sync

        print(f"  {sh_id_cell}: {current_status!r} → {new_status!r}")
        # Update Status, What Was Done, Evidence, Last Updated
        updates.append({
            "range": f"'{MC_TAB}'!H{row_idx}",
            "values": [[new_status]],
        })
        if q_data["last_result"]:
            updates.append({
                "range": f"'{MC_TAB}'!J{row_idx}",
                "values": [[q_data["last_result"][:500]]],
            })
        if q_data["fix_log"]:
            updates.append({
                "range": f"'{MC_TAB}'!K{row_idx}",
                "values": [[q_data["fix_log"]]],
            })
        updates.append({
            "range": f"'{MC_TAB}'!P{row_idx}",
            "values": [[TODAY]],
        })

    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=TRACKER_SS,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        print(f"sync: {len(updates)} cell updates written to Master Checklist")
    else:
        print("sync: everything already in sync — no updates needed")


# ── CREDIT mode ───────────────────────────────────────────────────────────────
def cmd_credit(svc, args):
    row = [
        TODAY,
        args.workflow or "—",
        args.run_id   or "—",
        args.api      or "—",
        args.error    or "—",
        args.task     or "—",
        args.step     or "—",
        args.credits  or "—",
        args.resolved or "No",
        args.resolution or "—",
    ]
    svc.spreadsheets().values().append(
        spreadsheetId=TRACKER_SS,
        range=f"'{CB_TAB}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    print(f"credit: appended row to Credit Blocks — {args.api} / {args.error[:60]}")


# ── DONE mode ──────────────────────────────────────────────────────────────────
def cmd_done(svc, args):
    if not args.sh_id:
        raise SystemExit("--sh-id required for done mode")

    mc = svc.spreadsheets().values().get(
        spreadsheetId=TRACKER_SS,
        range=f"'{MC_TAB}'!A2:Q200",
    ).execute().get("values", [])

    updates = []
    found = False
    for row_idx, row in enumerate(mc, start=2):
        sh_id_cell = row[MC_COL_SH_ID].strip() if len(row) > MC_COL_SH_ID else ""
        if sh_id_cell != args.sh_id:
            continue
        found = True
        print(f"done: found {args.sh_id} at row {row_idx}")
        updates.append({"range": f"'{MC_TAB}'!H{row_idx}", "values": [["Done"]]})
        if args.done:
            updates.append({"range": f"'{MC_TAB}'!J{row_idx}", "values": [[args.done[:500]]]})
        if args.evidence:
            updates.append({"range": f"'{MC_TAB}'!K{row_idx}", "values": [[args.evidence]]})
        updates.append({"range": f"'{MC_TAB}'!P{row_idx}", "values": [[TODAY]]})

    if not found:
        print(f"done: SH-ID {args.sh_id!r} not found in Master Checklist — no update")
        return
    if updates:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=TRACKER_SS,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
        print(f"done: {args.sh_id} marked Done in Master Checklist")


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Pipeline Fix Tracker writer")
    sub = p.add_subparsers(dest="mode")

    sub.add_parser("sync", help="Sync Self-Heal Queue statuses → Master Checklist")

    cr = sub.add_parser("credit", help="Log an API credit failure to Credit Blocks tab")
    cr.add_argument("--workflow");  cr.add_argument("--run-id")
    cr.add_argument("--api");       cr.add_argument("--error")
    cr.add_argument("--task");      cr.add_argument("--step")
    cr.add_argument("--credits");   cr.add_argument("--resolved")
    cr.add_argument("--resolution")

    dn = sub.add_parser("done", help="Mark a specific SH-ID as Done")
    dn.add_argument("--sh-id", required=True)
    dn.add_argument("--done",     default="")
    dn.add_argument("--evidence", default="")

    args = p.parse_args()
    if not args.mode:
        p.print_help(); sys.exit(1)

    creds = _get_creds()
    svc   = _sheets(creds)

    if args.mode == "sync":
        cmd_sync(svc)
    elif args.mode == "credit":
        cmd_credit(svc, args)
    elif args.mode == "done":
        cmd_done(svc, args)

if __name__ == "__main__":
    main()
