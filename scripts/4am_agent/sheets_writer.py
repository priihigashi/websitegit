"""
sheets_writer.py — All Google Sheets read/write operations for the 4AM agent.
Tabs: Scraping Targets (read), Content Queue (append), Clip Collections (update), Runs Log (append).
Uses SHEETS_TOKEN OAuth refresh token (same pattern as all other scripts).
"""
import os, json, sys
from pathlib import Path
import urllib.request, urllib.parse
import pytz
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
from fake_news_classifier import normalize_url

SPREADSHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
et             = pytz.timezone("America/New_York")


def _service():
    raw = os.environ["SHEETS_TOKEN"]
    td  = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        "https://oauth2.googleapis.com/token", data=data
    ).read())
    creds = Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )
    return build("sheets", "v4", credentials=creds)


# ─── Scraping Targets ────────────────────────────────────────────────────────

def read_scraping_targets():
    """
    Returns: {target_type: {niche: [values...]}}
    Example: {"ACCOUNT": {"OAK PARK": ["@oakparkconstruction"], "BRAZIL": []}, ...}
    """
    result = _service().spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="'🎯 Scraping Targets'!A1:F50",
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


BLOG_SHEET_ID = "1CrVHlIe8u1bo_1W0iU0O3WKv2JUrm0-UO76y4p5NC_c"


def append_blog_ideas_to_content_sheet(articles):
    """
    Writes website-scraped article titles to Content Ideas tab in the blog spreadsheet.
    Each article becomes a row with status '🆕 Idea' so blog-generator.js picks it up.
    Deduplicates by Raw Idea (col E) to avoid resubmitting the same title.
    Returns count of rows appended.
    """
    svc = _service()
    today = datetime.now(et).strftime("%-m/%-d/%Y")

    # Read existing Raw Ideas to deduplicate
    try:
        existing = svc.spreadsheets().values().get(
            spreadsheetId=BLOG_SHEET_ID,
            range="'Content Ideas'!E:E"
        ).execute()
        existing_titles = {r[0].strip().lower() for r in existing.get("values", [])[1:] if r}
    except Exception:
        existing_titles = set()

    rows_to_add = []
    for article in articles:
        title = article.get("caption", "").strip()
        if not title or title.lower() in existing_titles:
            continue
        source_url = article.get("url", "")
        source_domain = article.get("target_value", source_url)
        row = [""] * 23
        row[0]  = today
        row[1]  = "4AM Agent"
        row[2]  = source_domain
        row[3]  = source_url
        row[4]  = title
        row[19] = "🆕 Idea"
        rows_to_add.append(row)
        existing_titles.add(title.lower())

    if not rows_to_add:
        return 0

    svc.spreadsheets().values().append(
        spreadsheetId=BLOG_SHEET_ID,
        range="'Content Ideas'!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_add},
    ).execute()
    return len(rows_to_add)


def read_scraping_destinations():
    """Returns {target_type: destination} from column G (DESTINATION).
    Values: 'instagram' | 'blog' | 'both' | 'reference'
    Defaults to 'instagram' if column is missing or empty.
    """
    result = _service().spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="'🎯 Scraping Targets'!A1:G50",
    ).execute()
    rows = result.get("values", [])
    destinations = {}
    for row in rows[1:]:
        if not row:
            continue
        target_type = row[0].strip()
        if target_type:
            destinations[target_type] = row[6].strip().lower() if len(row) > 6 else "instagram"
    return destinations


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
        pexels_urls = [c["pexels_url"] for c in clips if c.get("source") == "pexels" and c.get("pexels_url")]
        yt_urls    = [c["youtube_url"] for c in clips if c.get("source") == "youtube" and c.get("youtube_url")]
        broll  = " | ".join(pexels_urls + yt_urls)

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
    For each script generated:
    - If a matching Collecting row exists → append new clip URLs and update count
    - If no matching row exists → add a new row automatically
    Sources tracked: Pexels (free stock) and YouTube (real-world clips).
    """
    svc    = _service()
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="'📋 Clip Collections'!A:G",
    ).execute()
    rows = result.get("values", [])
    existing_topics = {(row[0].lower() if row else ""): idx+2 for idx, row in enumerate(rows[1:])}

    updated = 0
    for item in scripts_with_broll:
        topic = item["script_data"].get("topic", "")
        clips = item.get("broll_clips", [])
        if not clips:
            continue

        pexels_links  = [c["pexels_url"]  for c in clips if c.get("source") == "pexels"  and c.get("pexels_url")]
        youtube_links = [c["youtube_url"] for c in clips if c.get("source") == "youtube" and c.get("youtube_url")]
        all_links = pexels_links + youtube_links

        # Check for matching existing row
        matched_row = None
        for ex_topic, ex_row_idx in existing_topics.items():
            if (topic.lower() in ex_topic or ex_topic in topic.lower() or
                    any(w in ex_topic for w in topic.lower().split() if len(w) > 4)):
                matched_row = ex_row_idx
                break

        if matched_row:
            row_data = rows[matched_row - 2] if matched_row - 2 < len(rows) else []
            existing_links = row_data[4] if len(row_data) > 4 else ""
            current_count  = int(row_data[3]) if len(row_data) > 3 and str(row_data[3]).isdigit() else 0
            combined = [l for l in existing_links.split(" | ") if l] + all_links
            svc.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'📋 Clip Collections'!D{matched_row}:E{matched_row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[str(current_count + len(all_links)), " | ".join(combined)]]},
            ).execute()
        else:
            # Auto-add new row for this topic
            link_str = " | ".join(all_links)
            svc.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="'📋 Clip Collections'!A:G",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [[
                    topic,                  # A Topic
                    "OAK PARK",             # B Niche
                    "10",                   # C Target Clips
                    str(len(all_links)),    # D Clips Collected
                    link_str,               # E Links
                    "Collecting",           # F Status
                    f"Auto-added by 4AM agent. Pexels: {len(pexels_links)} | YouTube: {len(youtube_links)}",  # G Notes
                ]]},
            ).execute()
        updated += 1

    return updated

# ─── Inspiration Library — news/verification scrape results ──────────────────

def save_scraped_to_inspiration_library(items):
    """
    Writes scraped items that have series_override set (e.g. Verificamos, Fact-Checked)
    to the Inspiration Library tab for human review + topic_picker routing.
    Deduplicates by URL — skips if URL already in column C.
    Returns count of rows appended.
    """
    svc = _service()
    today = datetime.now(et).strftime("%Y-%m-%d")

    # Read existing URLs to deduplicate — normalize before comparing to catch
    # duplicates where only the query string differs (e.g. ?igsh= tracking params)
    existing_urls = set()
    try:
        existing = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="'📥 Inspiration Library'!C:C"
        ).execute()
        for row in existing.get("values", [])[1:]:
            if row:
                existing_urls.add(normalize_url(row[0].strip()))
    except Exception:
        pass

    # Resolve headers to find series_override / fake_news_route / fake_news_confidence columns
    try:
        hdr_resp = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="'📥 Inspiration Library'!1:1"
        ).execute()
        headers = hdr_resp.get("values", [[]])[0]
        hmap = {h.strip().lower(): i for i, h in enumerate(headers)}
    except Exception:
        hmap = {}

    width = max(hmap.values()) + 1 if hmap else 29

    rows_to_add = []
    for item in items:
        url = item.get("url", "").strip()
        norm = normalize_url(url)
        if not url or norm in existing_urls:
            continue
        series_override = item.get("series_override", "")

        row = [""] * width
        def put(col_name, val):
            idx = hmap.get(col_name.lower())
            if idx is not None and idx < width:
                row[idx] = val

        put("date added", today)
        put("platform", item.get("platform", "instagram").capitalize())
        put("url", url)
        put("content type", item.get("content_type", "Reel"))
        put("description", item.get("caption", "")[:200])
        put("views", str(item.get("views", "")) if item.get("views") else "")
        put("niche", item.get("niche", "").title())
        put("status", "captured")
        put("series_override", series_override)
        put("fake_news_confidence", "medium")  # scraped via verification hashtag = medium confidence

        rows_to_add.append(row)
        existing_urls.add(norm)

    if not rows_to_add:
        return 0

    svc.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="'📥 Inspiration Library'!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_add},
    ).execute()
    return len(rows_to_add)


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
