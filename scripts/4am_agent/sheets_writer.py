"""
sheets_writer.py — All Google Sheets read/write operations for the 4AM agent.
Tabs: Scraping Targets (read), Content Queue (append), Clip Collections (update), Runs Log (append).
Uses service account from GOOGLE_SA_KEY env var.
"""
import os, json
import pytz
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
SCOPES         = ["https://www.googleapis.com/auth/spreadsheets"]
et             = pytz.timezone("America/New_York")


def _service():
    sa_info = json.loads(os.environ["GOOGLE_SA_KEY"])
    creds   = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


# ─── Scraping Targets ────────────────────────────────────────────────────────

def read_scraping_targets():
    """
    Returns: {target_type: {niche: [values...]}}
    Example: {"ACCOUNT": {"OAK PARK": ["@oakparkconstruction"], "BRAZIL": []}, ...}
    """
    result = _service().spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Scraping Targets!A1:F10",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        return {}

    headers    = rows[0]          # TYPE/TARGET, OAK PARK, BRAZIL, UGC, NEWS/WORLD, NOTES
    niche_cols = headers[1:-1]    # drop TYPE/TARGET and NOTES

    targets = {}
    for row in rows[1:]:
        if not row:
            continue
        target_type = row[0].strip()
        if not target_type:
            continue
        targets[target_type] = {}
        for i, niche in enumerate(niche_cols):
            col_idx = i + 1
            cell    = row[col_idx].strip() if col_idx < len(row) else ""
            targets[target_type][niche] = [v.strip() for v in cell.split(",") if v.strip()]

    return targets


# ─── Content Queue ────────────────────────────────────────────────────────────
# Columns: Date Created | Project Name | Service Type | Photo(s) Used |
#          Content Type | Hook | Caption Body | CTA | Hashtags | Status |
#          after processed | ok to schedule | Suggested Post Date |
#          suggested time | Platform | Content Source | A/B Test Group | Inspo Source URL

def append_to_content_queue(scripts_with_broll):
    """Append 2 new Talking Head rows to Content Queue."""
    date_str = datetime.now(et).strftime("%Y-%m-%d")
    rows = []

    for item in scripts_with_broll:
        s      = item["script_data"]
        clips  = item.get("broll_clips", [])
        broll  = " | ".join(c["pexels_url"] for c in clips)

        rows.append([
            date_str,                   # A Date Created
            "Oak Park Construction",    # B Project Name
            "Talking Head",             # C Service Type
            broll,                      # D Photo(s) Used — B-roll links
            "Talking Head",             # E Content Type
            s.get("topic", ""),         # F Hook
            s.get("script", ""),        # G Caption Body
            "Link in bio",              # H CTA
            s.get("hashtags", ""),      # I Hashtags
            "Pending",                  # J Status
            "", "", "",                 # K–M (after processed, ok to schedule, date)
            "",                         # N suggested time
            "Instagram,TikTok",         # O Platform
            "4AM Agent",                # P Content Source
            "",                         # Q A/B Test Group
            s.get("inspo_url", ""),     # R Inspo Source URL
        ])

    _service().spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="📋 Content Queue!A:R",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

    return len(rows)


# ─── Clip Collections ─────────────────────────────────────────────────────────
# Columns: Topic | Niche | Target Clips | Clips Collected | Links | Status | Notes

def update_clip_collections(scripts_with_broll):
    """
    For any Clip Collections row with Status='Collecting' that matches a script topic,
    append new Pexels clip URLs and update the count.
    """
    svc    = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Clip Collections!A:G",
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return 0

    updated = 0
    for item in scripts_with_broll:
        topic = item["script_data"].get("topic", "").lower()
        clips = item.get("broll_clips", [])

        for row_idx, row in enumerate(rows[1:], start=2):   # sheet row = row_idx+1
            row_topic  = (row[0] if row else "").lower()
            row_status = (row[5] if len(row) > 5 else "").lower()

            topic_match = (
                topic in row_topic
                or row_topic in topic
                or any(w in row_topic for w in topic.split() if len(w) > 4)
            )

            if row_status == "collecting" and topic_match and clips:
                existing_links = row[4] if len(row) > 4 else ""
                new_links      = [c["pexels_url"] for c in clips]
                all_links      = [l for l in existing_links.split(" | ") if l] + new_links
                current_count  = int(row[3]) if len(row) > 3 and str(row[3]).isdigit() else 0

                svc.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"Clip Collections!D{row_idx}:E{row_idx}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[str(current_count + len(new_links)), " | ".join(all_links)]]},
                ).execute()
                updated += 1

    return updated


# ─── Runs Log ─────────────────────────────────────────────────────────────────
# Columns: Date | Time | Status | Topics Found | Scripts Generated | Clips Found |
#          Rows Added to Queue | Apify Results Count | Filter Rejected Count |
#          Error Message | Duration Seconds | Notification Sent | Lessons Learned

def append_run_log(log_data):
    """Append one row to the Runs Log tab."""
    now_et = datetime.now(et)

    row = [[
        now_et.strftime("%Y-%m-%d"),
        now_et.strftime("%H:%M ET"),
        log_data.get("status", "success"),
        log_data.get("topics_found", 0),
        log_data.get("scripts_generated", 0),
        log_data.get("clips_found", 0),
        log_data.get("rows_added", 0),
        log_data.get("apify_count", 0),
        log_data.get("rejected_count", 0),
        log_data.get("error", ""),
        log_data.get("duration_seconds", 0),
        log_data.get("notification_sent", False),
        log_data.get("lessons_learned", ""),
    ]]

    _service().spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="📊 Runs Log!A:M",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": row},
    ).execute()
