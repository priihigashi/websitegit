#!/usr/bin/env python3
"""
schedule_posts.py — Oak Park Construction Post Scheduler
Runs daily at 9PM ET via GitHub Actions.

Reads 📋 Content Queue tab:
  - L (ok to schedule) = "Yes" → schedule the post
  - M (Suggested Post Date) = date
  - N (suggested time) = time
  - O (Platform) = Instagram / TikTok / Instagram, TikTok
  - G (Caption Body) + H (CTA) + I (Hashtags) = full caption
  - AB (Drive Folder Link) = slides folder

Flow:
  1. Read all rows where L = "Yes"
  2. Check date/time — if past, use next available slot today or tomorrow
  3. Make Drive files publicly accessible (temporary)
  4. Post to Instagram and/or TikTok via Composio
  5. Set J (Status) = "Scheduled", L = "Scheduled"

Env vars required:
  SHEETS_TOKEN        — Google OAuth token JSON
  CONTENT_SHEET_ID    — Google Sheet ID
  COMPOSIO_API_KEY    — Composio API key
"""

import os, io, json, re, urllib.request, urllib.parse, sys, time, tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
import pytz

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID   = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB  = "📋 Content Queue"
ET         = pytz.timezone("America/New_York")

# ── Auth ──────────────────────────────────────────────────────────────────────
_token_cache = {}

def get_token():
    if _token_cache.get("token") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["token"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        path = os.environ.get("SHEETS_TOKEN_PATH", "")
        if path and Path(path).exists():
            raw = Path(path).read_text()
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN set")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["token"] = resp["access_token"]
    _token_cache["exp"]   = time.time() + resp.get("expires_in", 3500) - 60
    _token_cache["td"]    = td
    return resp["access_token"]

def get_creds():
    from google.oauth2.credentials import Credentials
    get_token()
    td = _token_cache["td"]
    return Credentials(
        token=_token_cache["token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=["https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets"],
    )

# ── Sheets helpers ─────────────────────────────────────────────────────────────
def col_letter(n):
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result

def sheet_get(token, range_str):
    enc = urllib.parse.quote(range_str, safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read())

def sheet_update_cells(token, tab_name, updates: list):
    data = [{"range": f"'{tab_name}'!{cell}", "values": [[val]]} for cell, val in updates]
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": data}).encode()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req).read()
    except Exception as e:
        print(f"  ⚠️  Sheet update error: {e}")

def get_rows_to_schedule(token) -> list[dict]:
    rows = sheet_get(token, f"'{QUEUE_TAB}'").get("values", [])
    if len(rows) < 2:
        return []
    header = [h.strip() for h in rows[0]]
    def ci(name): return next((i for i,h in enumerate(header) if name.lower() in h.lower()), None)

    result = []
    for idx, row in enumerate(rows[1:], start=2):
        def v(col): i=ci(col); return row[i].strip() if i is not None and len(row)>i else ""
        if v("ok to schedule").lower() != "yes":
            continue
        result.append({
            "row":        idx,
            "project":    v("project name"),
            "caption":    v("caption body"),
            "cta":        v("cta"),
            "hashtags":   v("hashtags"),
            "platform":   v("platform"),
            "post_date":  v("suggested post date"),
            "post_time":  v("suggested time"),
            "drive_link": v("ab"),  # col AB = Drive folder link
            "status_col": col_letter(ci("status") + 1) if ci("status") is not None else "J",
            "l_col":      col_letter(ci("ok to schedule") + 1) if ci("ok to schedule") is not None else "L",
        })
    return result

# ── Drive helpers ──────────────────────────────────────────────────────────────
def drive_folder_id_from_url(url: str) -> str:
    m = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else ""

def get_slide_urls_from_folder(folder_id: str, creds) -> list[str]:
    """Get public download URLs for all JPG slides in a Drive folder."""
    from googleapiclient.discovery import build
    try:
        svc = build("drive", "v3", credentials=creds)
        # List files in folder
        results = svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false and mimeType='image/jpeg'",
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            orderBy="name"
        ).execute()
        files = results.get("files", [])

        urls = []
        for f in files:
            # Make publicly accessible temporarily
            try:
                svc.permissions().create(
                    fileId=f["id"],
                    body={"type": "anyone", "role": "reader"},
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass  # May already be public
            # Direct download URL (works with Instagram/TikTok)
            urls.append(f"https://drive.google.com/uc?export=download&id={f['id']}")

        return sorted(urls)  # Sort by name order = slide order
    except Exception as e:
        print(f"  ⚠️  Could not get slide URLs: {e}")
        return []

# ── Composio helpers ───────────────────────────────────────────────────────────
COMPOSIO_KEY = os.environ.get("COMPOSIO_API_KEY", "")

def composio_execute(tool_slug: str, params: dict) -> dict:
    payload = json.dumps({
        "tool": tool_slug,
        "input": params
    }).encode()
    req = urllib.request.Request(
        "https://backend.composio.dev/api/v2/actions/execute",
        data=payload,
        headers={
            "Authorization": f"Bearer {COMPOSIO_KEY}",
            "Content-Type": "application/json",
            "x-api-key": COMPOSIO_KEY,
        }
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return resp
    except Exception as e:
        print(f"  ⚠️  Composio error ({tool_slug}): {e}")
        return {}

# ── Build caption ──────────────────────────────────────────────────────────────
def build_caption(post: dict) -> str:
    parts = []
    if post["caption"]:
        parts.append(post["caption"])
    if post["cta"]:
        parts.append(f"\n{post['cta']}")
    if post["hashtags"]:
        parts.append(f"\n\n{post['hashtags']}")
    return "\n".join(parts)

# ── Resolve post datetime ──────────────────────────────────────────────────────
def resolve_post_datetime(post_date_str: str, post_time_str: str) -> datetime:
    """If date is past → use today. Parse time string → return ET datetime."""
    now_et = datetime.now(ET)
    today = now_et.date()

    try:
        post_date = datetime.strptime(post_date_str, "%Y-%m-%d").date()
    except Exception:
        post_date = today

    if post_date < today:
        print(f"  ⚠️  Date {post_date} is past — using today {today}")
        post_date = today

    # Parse time
    try:
        t = datetime.strptime(post_time_str.strip(), "%I:%M %p")
    except Exception:
        try:
            t = datetime.strptime(post_time_str.strip(), "%H:%M")
        except Exception:
            print(f"  ⚠️  Could not parse time '{post_time_str}' — defaulting to 7:00 PM")
            t = datetime.strptime("7:00 PM", "%I:%M %p")

    dt_et = ET.localize(datetime(post_date.year, post_date.month, post_date.day,
                                  t.hour, t.minute))
    return dt_et

# ── Post to Instagram ──────────────────────────────────────────────────────────
def post_to_instagram(slide_urls: list[str], caption: str) -> bool:
    if not slide_urls:
        print("  ❌ No slide URLs for Instagram")
        return False
    print(f"  📸 Posting carousel to Instagram ({len(slide_urls)} slides)...")

    if len(slide_urls) == 1:
        # Single image
        resp = composio_execute("INSTAGRAM_POST_IG_USER_MEDIA", {
            "ig_user_id": "me",
            "image_url": slide_urls[0],
            "caption": caption,
        })
    else:
        # Carousel — create child containers first
        child_ids = []
        for url in slide_urls[:10]:  # Instagram max 10
            child_resp = composio_execute("INSTAGRAM_POST_IG_USER_MEDIA", {
                "ig_user_id": "me",
                "image_url": url,
                "is_carousel_item": True,
            })
            cid = (child_resp.get("data", {}) or {}).get("id")
            if cid:
                child_ids.append(cid)
                time.sleep(1)

        if not child_ids:
            print("  ❌ Failed to create Instagram carousel children")
            return False

        # Create carousel container
        resp = composio_execute("INSTAGRAM_POST_IG_USER_MEDIA", {
            "ig_user_id": "me",
            "media_type": "CAROUSEL",
            "children": child_ids,
            "caption": caption,
        })

    container_id = (resp.get("data", {}) or {}).get("id")
    if not container_id:
        print(f"  ❌ Instagram container creation failed: {resp}")
        return False

    # Publish
    time.sleep(3)
    pub_resp = composio_execute("INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH", {
        "ig_user_id": "me",
        "creation_id": container_id,
    })
    published_id = (pub_resp.get("data", {}) or {}).get("id")
    if published_id:
        print(f"  ✅ Instagram posted (ID: {published_id})")
        return True
    else:
        print(f"  ❌ Instagram publish failed: {pub_resp}")
        return False

# ── Post to TikTok ─────────────────────────────────────────────────────────────
def post_to_tiktok(slide_urls: list[str], caption: str) -> bool:
    if not slide_urls:
        print("  ❌ No slide URLs for TikTok")
        return False
    print(f"  🎵 Posting photo slideshow to TikTok ({len(slide_urls)} slides)...")

    resp = composio_execute("TIKTOK_POST_PHOTO", {
        "photo_images": slide_urls[:35],  # TikTok max 35
        "description": caption[:2200],
        "privacy_level": "PUBLIC_TO_EVERYONE",
    })
    publish_id = (resp.get("data", {}) or {}).get("publish_id")
    if publish_id:
        print(f"  ✅ TikTok posted (publish_id: {publish_id})")
        return True
    else:
        print(f"  ❌ TikTok post failed: {resp}")
        return False

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n📅 Post Scheduler — {date.today()}")
    print("=" * 50)

    if not COMPOSIO_KEY:
        print("❌ COMPOSIO_API_KEY not set — cannot schedule posts")
        sys.exit(1)

    token = get_token()
    creds = get_creds()

    rows = get_rows_to_schedule(token)
    if not rows:
        print("✅ No posts with L=Yes found — nothing to schedule.")
        return

    print(f"   Found {len(rows)} post(s) to schedule\n")

    for post in rows:
        print(f"\n{'='*50}")
        print(f"📌 {post['project']} — {post['platform']}")

        # Get slides from Drive
        folder_id = drive_folder_id_from_url(post["drive_link"])
        if not folder_id:
            print(f"  ❌ Could not parse Drive folder from: {post['drive_link']}")
            continue

        slide_urls = get_slide_urls_from_folder(folder_id, creds)
        if not slide_urls:
            print(f"  ❌ No slides found in Drive folder")
            continue
        print(f"  📂 Found {len(slide_urls)} slides")

        # Resolve date/time
        dt_et = resolve_post_datetime(post["post_date"], post["post_time"])
        now_et = datetime.now(ET)
        print(f"  🕐 Scheduled for: {dt_et.strftime('%Y-%m-%d %I:%M %p ET')}")

        # Only post if the scheduled time is within the next 60 minutes or past
        diff_minutes = (dt_et - now_et).total_seconds() / 60
        if diff_minutes > 60:
            print(f"  ⏳ Too early — {diff_minutes:.0f} min until post time, skipping")
            continue

        caption = build_caption(post)
        platforms = [p.strip().lower() for p in post["platform"].split(",")]

        ig_ok = False
        tt_ok = False

        if "instagram" in platforms:
            ig_ok = post_to_instagram(slide_urls, caption)

        if "tiktok" in platforms:
            tt_ok = post_to_tiktok(slide_urls, caption)

        # Update sheet
        if ig_ok or tt_ok:
            sheet_update_cells(token, QUEUE_TAB, [
                (f"{post['status_col']}{post['row']}", "Scheduled"),
                (f"{post['l_col']}{post['row']}", "Scheduled"),
            ])
            print(f"  📊 Sheet updated: J=Scheduled, L=Scheduled")
        else:
            print(f"  ⚠️  All platforms failed — sheet not updated")

    print(f"\n✅ Scheduler run complete.")

if __name__ == "__main__":
    main()
