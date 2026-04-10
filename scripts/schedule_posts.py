#!/usr/bin/env python3
"""
schedule_posts.py — Oak Park Construction Post Scheduler
Runs daily at 9PM ET via GitHub Actions.

Reads Content Queue tab — columns matched by header name:
  L = ok to schedule      → "Yes" to process this row
  M = Suggested Post Date → YYYY-MM-DD
  N = suggested time      → "7:00 PM" or "19:00"
  O = Platform            → Instagram / TikTok / Instagram, TikTok
  G = Caption Body  H = CTA  I = Hashtags
  AB = Drive Folder Link  → Google Drive folder with slide JPGs

Flow:
  1. Read rows where "ok to schedule" = "Yes"
  2. Resolve post datetime (skip if > 60 min away)
  3. Make Drive slide images public, collect URLs
  4. Schedule via Buffer API
  5. Update sheet: J=Scheduled, L=Scheduled

Env vars:
  SHEETS_TOKEN       — Google OAuth token JSON
  CONTENT_SHEET_ID   — Google Sheet ID
  BUFFER_API_KEY     — Buffer access token (expires 2027-04-09, secret: BUFFER_API_KEY_EXP04092027)
"""

import os, json, re, urllib.request, urllib.parse, sys, time
from datetime import date, datetime
import pytz

ET        = pytz.timezone("America/New_York")
SHEET_ID  = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB = "📋 Content Queue"
BUFFER_KEY = os.environ.get("BUFFER_API_KEY", "")
BUFFER_API = "https://api.bufferapp.com/1"

# ── Auth ───────────────────────────────────────────────────────────────────────
_token_cache = {}

def get_token():
    if _token_cache.get("token") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["token"]
    raw = os.environ.get("SHEETS_TOKEN", "")
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
    enc = urllib.parse.quote(range_str, safe="!:'")
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

def get_rows_to_schedule(token) -> list:
    rows = sheet_get(token, f"'{QUEUE_TAB}'").get("values", [])
    if len(rows) < 2:
        return []
    header = [h.strip() for h in rows[0]]
    def ci(name):
        return next((i for i, h in enumerate(header) if name.lower() in h.lower()), None)

    result = []
    for idx, row in enumerate(rows[1:], start=2):
        def v(col):
            i = ci(col)
            return row[i].strip() if i is not None and len(row) > i else ""
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
            "drive_link": v("ab"),
            "status_col": col_letter((ci("status") or 9) + 1),
            "l_col":      col_letter((ci("ok to schedule") or 11) + 1),
        })
    return result

# ── Drive helpers ──────────────────────────────────────────────────────────────
def drive_folder_id_from_url(url: str) -> str:
    m = re.search(r'/folders/([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else ""

def get_public_slide_urls(folder_id: str, creds) -> list:
    from googleapiclient.discovery import build
    try:
        svc = build("drive", "v3", credentials=creds)
        results = svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false and mimeType='image/jpeg'",
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            orderBy="name"
        ).execute()
        urls = []
        for f in results.get("files", []):
            try:
                svc.permissions().create(
                    fileId=f["id"],
                    body={"type": "anyone", "role": "reader"},
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass
            urls.append(f"https://drive.google.com/uc?export=download&id={f['id']}")
        return urls
    except Exception as e:
        print(f"  ⚠️  Could not get slide URLs: {e}")
        return []

# ── Buffer helpers ─────────────────────────────────────────────────────────────
_buffer_profiles = None

def buffer_get_profiles() -> list:
    global _buffer_profiles
    if _buffer_profiles is not None:
        return _buffer_profiles
    try:
        url = f"{BUFFER_API}/profiles.json?access_token={BUFFER_KEY}"
        resp = json.loads(urllib.request.urlopen(url, timeout=15).read())
        _buffer_profiles = resp if isinstance(resp, list) else []
        names = [p.get("formatted_service") or p.get("service", "?") for p in _buffer_profiles]
        print(f"  📱 Buffer profiles found: {names}")
        return _buffer_profiles
    except Exception as e:
        print(f"  ❌ Could not fetch Buffer profiles: {e}")
        _buffer_profiles = []
        return []

def buffer_profile_ids_for(platform: str) -> list:
    service_map = {"instagram": ["instagram"], "tiktok": ["tiktok"]}
    targets = service_map.get(platform.lower(), [platform.lower()])
    return [p["id"] for p in buffer_get_profiles()
            if p.get("service", "").lower() in targets]

def buffer_schedule_post(profile_ids: list, caption: str,
                         image_urls: list, scheduled_at: datetime) -> bool:
    if not profile_ids:
        print(f"  ❌ No Buffer profile matched for this platform")
        return False

    scheduled_utc = scheduled_at.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = [
        ("access_token", BUFFER_KEY),
        ("text", caption),
        ("scheduled_at", scheduled_utc),
        ("now", "false"),
    ]
    for pid in profile_ids:
        params.append(("profile_ids[]", pid))

    if len(image_urls) == 1:
        params.append(("media[picture]", image_urls[0]))
    elif len(image_urls) > 1:
        for url in image_urls[:10]:
            params.append(("media[photos][]", url))

    payload = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{BUFFER_API}/updates/create.json",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        if resp.get("success") or resp.get("id") or "updates" in resp:
            print(f"  ✅ Buffer scheduled → {scheduled_at.strftime('%Y-%m-%d %I:%M %p ET')}")
            return True
        print(f"  ❌ Buffer rejected: {resp}")
        return False
    except Exception as e:
        print(f"  ❌ Buffer error: {e}")
        return False

# ── Helpers ────────────────────────────────────────────────────────────────────
def build_caption(post: dict) -> str:
    parts = [post["caption"]]
    if post["cta"]:
        parts.append(f"\n{post['cta']}")
    if post["hashtags"]:
        parts.append(f"\n\n{post['hashtags']}")
    return "\n".join(filter(None, parts))

def resolve_post_datetime(post_date_str: str, post_time_str: str) -> datetime:
    now_et = datetime.now(ET)
    today  = now_et.date()
    try:
        post_date = datetime.strptime(post_date_str, "%Y-%m-%d").date()
    except Exception:
        post_date = today
    if post_date < today:
        print(f"  ⚠️  Date {post_date} is past — using today {today}")
        post_date = today
    t = None
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            t = datetime.strptime(post_time_str.strip(), fmt)
            break
        except Exception:
            pass
    if t is None:
        print(f"  ⚠️  Could not parse time '{post_time_str}' — defaulting to 7:00 PM")
        t = datetime.strptime("7:00 PM", "%I:%M %p")
    return ET.localize(datetime(post_date.year, post_date.month, post_date.day, t.hour, t.minute))

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"\n📅 Post Scheduler (Buffer) — {date.today()}")
    print("=" * 50)

    if not BUFFER_KEY:
        print("❌ BUFFER_API_KEY not set — cannot schedule posts")
        sys.exit(1)

    token = get_token()
    creds = get_creds()
    rows  = get_rows_to_schedule(token)

    if not rows:
        print("✅ No posts with 'ok to schedule' = Yes — nothing to do.")
        return

    print(f"   Found {len(rows)} post(s) to process\n")

    for post in rows:
        print(f"\n{'='*50}")
        print(f"📌 {post['project']} — {post['platform']}")

        folder_id = drive_folder_id_from_url(post["drive_link"])
        if not folder_id:
            print(f"  ❌ No Drive folder link in row {post['row']}")
            continue

        image_urls = get_public_slide_urls(folder_id, creds)
        if not image_urls:
            print(f"  ❌ No JPG slides found in Drive folder")
            continue
        print(f"  📂 {len(image_urls)} slides found")

        dt_et    = resolve_post_datetime(post["post_date"], post["post_time"])
        now_et   = datetime.now(ET)
        diff_min = (dt_et - now_et).total_seconds() / 60
        print(f"  🕐 Target: {dt_et.strftime('%Y-%m-%d %I:%M %p ET')} ({diff_min:.0f} min away)")

        if diff_min > 60:
            print(f"  ⏳ Too early — will pick up at next 9PM run")
            continue

        caption   = build_caption(post)
        platforms = [p.strip().lower() for p in post["platform"].split(",")]
        any_ok    = False

        for platform in platforms:
            profile_ids = buffer_profile_ids_for(platform)
            ok = buffer_schedule_post(profile_ids, caption, image_urls, dt_et)
            if ok:
                any_ok = True

        if any_ok:
            sheet_update_cells(token, QUEUE_TAB, [
                (f"{post['status_col']}{post['row']}", "Scheduled"),
                (f"{post['l_col']}{post['row']}",      "Scheduled"),
            ])
            print(f"  📊 Sheet updated → Scheduled")
        else:
            print(f"  ⚠️  All platforms failed — sheet not updated")

    print(f"\n✅ Scheduler run complete.")

if __name__ == "__main__":
    main()
