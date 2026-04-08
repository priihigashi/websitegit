#!/usr/bin/env python3
"""
inspiration_scraper.py — Oak Park Construction Inspiration Library
Daily scraper for trending construction content.

Sources:
  A) Instagram via Apify  — hashtags + South Florida locations + accounts list
  B) YouTube Data API v3  — shorts + regular videos, construction niche

Output: 📥 Inspiration Library tab in Google Sheet
Safe:   deduplicates by URL, never clears existing rows.

Config block below — update ACCOUNTS_TO_MONITOR when Priscila finds good accounts.
"""

import os, json, time, urllib.request, urllib.parse, sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
WORKSPACE       = Path("/Users/priscilahigashi/ClaudeWorkspace")
TOKEN_FILE      = Path(os.environ.get("SHEETS_TOKEN_PATH",
                        str(WORKSPACE / "Credentials" / "sheets_token.json")))
ENV_FILE        = WORKSPACE / ".env"
SHEET_ID        = os.environ.get("SHEET_ID",
                                  "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
INSPO_TAB       = "📥 Inspiration Library"

# ── Scraping limits ───────────────────────────────────────────────────────────
MAX_INSTAGRAM   = 25   # posts per run
MAX_YOUTUBE     = 15   # videos per run
DAYS_LOOKBACK   = 14   # only posts from last 14 days
MIN_IG_VIEWS    = 5000 # minimum views to consider
MIN_YT_VIEWS    = 25000

# ── Instagram — hashtags to scrape ───────────────────────────────────────────
# Mix: niche + service-specific + South Florida local
IG_HASHTAGS = [
    # Service types (what Oak Park does)
    "kitchenremodel", "bathroomremodel", "homeaddition", "shellconstruction",
    "cbsconstruction", "newconstruction", "pergola", "outdoorkitchen",
    "concretedesign", "concretedriveway", "roofing", "tileinstallation",
    # Broader renovation
    "homerenovation", "beforeandafter", "customhomebuilder", "contractorlife",
    "constructionlife", "homeimprovement", "remodeling",
    # South Florida local (catches posts WITHOUT national hashtags)
    "southfloridahomes", "southfloridaliving", "pompanobeach",
    "fortlauderdale", "browardcounty", "miamirealestate",
]

# ── Instagram — accounts to monitor (add when Priscila identifies them) ──────
# Leave empty for now — hashtags + locations cover the gap
# Format: ["username1", "username2"]
ACCOUNTS_TO_MONITOR = []

# ── YouTube — search queries ──────────────────────────────────────────────────
YT_QUERIES = [
    "home renovation before after 2026",
    "kitchen remodel Florida",
    "bathroom remodel ideas",
    "concrete driveway installation",
    "pergola build backyard",
    "home addition construction",
    "CBS construction Florida",
    "contractor tips homeowner",
    "roof replacement cost 2026",
    "shell construction new home",
    "before after home renovation",
    "south florida custom home build",
]

# ── Header (19 cols — added Comments) ────────────────────────────────────────
INSPO_HEADER = [
    "Date Added", "Platform", "URL / Link", "Creator / Account",
    "Content Type", "Description", "Transcription", "Original Caption",
    "Visual Hook", "Hook Type",
    "Views", "Likes", "Comments", "Saves / Shares",
    "What's Working", "A/B Test Notes", "Use As Inspo For",
    "Copyright Version Created?", "Status"
]

# ── Env ───────────────────────────────────────────────────────────────────────
def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # env vars override file
    for k in ["APIFY_API_KEY", "YOUTUBE_API_KEY", "ANTHROPIC_API_KEY"]:
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env

# ── Google Auth ───────────────────────────────────────────────────────────────
def get_gtoken():
    td = json.loads(TOKEN_FILE.read_text())
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token"
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    return resp["access_token"]

def sheet_get(token, path):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{urllib.parse.quote(path, safe='/:!?=&')}"
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read())

def sheet_post(token, path, body):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{urllib.parse.quote(path, safe='/:!?=&')}"
    data = json.dumps(body).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})).read())

def sheet_put(token, path, body):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{urllib.parse.quote(path, safe='/:!?=&')}"
    data = json.dumps(body).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})).read())

# ── Sheet helpers ─────────────────────────────────────────────────────────────
def fix_header_add_comments(token):
    """Add Comments column to header if missing (safe — no data rows yet)."""
    rows = sheet_get(token, f"/values/'{INSPO_TAB}'!1:1").get("values", [[]])
    current = rows[0] if rows else []
    if "Comments" not in current:
        sheet_put(token,
                  f"/values/'{INSPO_TAB}'!A1:S1?valueInputOption=USER_ENTERED",
                  {"values": [INSPO_HEADER]})
        print("✅ Header updated — Comments column added")
    else:
        print("✅ Header OK")

def get_existing_urls(token) -> set:
    """Return set of URLs already in Inspiration Library (column C)."""
    try:
        rows = sheet_get(token, f"/values/'{INSPO_TAB}'!C:C").get("values", [])
        return {r[0].strip() for r in rows[1:] if r}
    except Exception:
        return set()

def append_rows(token, rows: list):
    sheet_post(token,
               f"/values/'{INSPO_TAB}'!A1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
               {"values": rows})

# ── Apify helper ───────────────────────────────────────────────────────────────
def apify_run(api_key, actor_id, input_data, timeout=300):
    """Start Apify actor, wait for completion, return items list."""
    # Start run
    url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={api_key}"
    data = json.dumps(input_data).encode()
    try:
        resp = json.loads(urllib.request.urlopen(urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"}), timeout=30).read())
    except Exception as e:
        print(f"  ⚠️  Apify start failed: {e}")
        return []

    run_id = resp.get("data", {}).get("id")
    dataset_id = resp.get("data", {}).get("defaultDatasetId")
    if not run_id:
        print(f"  ⚠️  No run ID returned")
        return []

    print(f"  ⏳ Apify run started: {run_id}")

    # Poll until done
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(15)
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={api_key}"
        try:
            status = json.loads(urllib.request.urlopen(status_url, timeout=15).read())
            state = status.get("data", {}).get("status", "")
            print(f"     Status: {state}")
            if state in ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"):
                break
        except Exception:
            pass

    if not dataset_id:
        return []

    # Fetch results
    items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={api_key}&format=json&limit=100"
    try:
        items = json.loads(urllib.request.urlopen(items_url, timeout=30).read())
        return items if isinstance(items, list) else []
    except Exception as e:
        print(f"  ⚠️  Dataset fetch failed: {e}")
        return []

# ── Source A — Instagram ───────────────────────────────────────────────────────
def scrape_instagram(api_key: str, existing_urls: set) -> list:
    print("\n📸 Source A — Instagram (Apify)...")

    # Build list of hashtag URLs + account URLs
    targets = []
    for tag in IG_HASHTAGS:
        targets.append(f"https://www.instagram.com/explore/tags/{tag}/")
    for account in ACCOUNTS_TO_MONITOR:
        targets.append(f"https://www.instagram.com/{account}/")

    # Use apify/instagram-scraper
    items = apify_run(api_key, "apify~instagram-scraper", {
        "directUrls": targets,
        "resultsType": "posts",
        "resultsLimit": 50,    # we'll filter down
        "addParentData": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}
    })

    print(f"  Raw results: {len(items)}")
    cutoff = (datetime.now() - timedelta(days=DAYS_LOOKBACK)).isoformat()
    new_rows = []
    today = date.today().isoformat()

    for item in items:
        url = item.get("url") or item.get("shortCode") and f"https://www.instagram.com/p/{item['shortCode']}/"
        if not url or url in existing_urls:
            continue

        # Only Reels/Videos
        item_type = item.get("type", "").lower()
        if item_type not in ("video", "reel") and not item.get("isVideo"):
            continue

        views = int(item.get("videoViewCount") or item.get("videoPlayCount") or 0)
        if views < MIN_IG_VIEWS:
            continue

        # Date filter
        timestamp = item.get("timestamp", "")
        if timestamp and timestamp < cutoff:
            continue

        likes    = int(item.get("likesCount") or 0)
        comments = int(item.get("commentsCount") or 0)
        caption  = (item.get("caption") or "")[:200]
        hook     = caption[:100] if caption else ""
        username = item.get("ownerUsername") or item.get("username") or ""

        new_rows.append([
            today,                          # Date Added
            "Instagram",                    # Platform
            url,                            # URL
            f"@{username}",                 # Creator
            "Reel",                         # Content Type
            "",                             # Description (blank — fill manually or AI later)
            "",                             # Transcription
            caption,                        # Original Caption
            "",                             # Visual Hook
            "Visual",                       # Hook Type
            str(views),                     # Views
            str(likes),                     # Likes
            str(comments),                  # Comments
            "",                             # Saves (Instagram API doesn't expose this)
            "",                             # What's Working
            "",                             # A/B Test Notes
            "",                             # Use As Inspo For
            "No",                           # Copyright Version Created
            "New"                           # Status
        ])
        existing_urls.add(url)

        if len(new_rows) >= MAX_INSTAGRAM:
            break

    print(f"  ✅ {len(new_rows)} new Instagram posts")
    return new_rows

# ── Source B — YouTube ────────────────────────────────────────────────────────
def scrape_youtube(api_key: str, existing_urls: set) -> list:
    print("\n▶️  Source B — YouTube (Data API v3)...")

    cutoff = (datetime.now() - timedelta(days=DAYS_LOOKBACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found_ids = {}   # video_id -> search snippet
    today = date.today().isoformat()

    for query in YT_QUERIES:
        if len(found_ids) >= MAX_YOUTUBE * 3:
            break
        # Search both regular videos AND shorts
        for video_type in ["video"]:   # shorts are just short videos — filter by duration below
            params = urllib.parse.urlencode({
                "part": "snippet",
                "q": query,
                "type": "video",
                "order": "viewCount",
                "publishedAfter": cutoff,
                "maxResults": 10,
                "key": api_key
            })
            try:
                resp = json.loads(urllib.request.urlopen(
                    f"https://www.googleapis.com/youtube/v3/search?{params}", timeout=15).read())
                for item in resp.get("items", []):
                    vid_id = item["id"].get("videoId")
                    if vid_id and vid_id not in found_ids:
                        found_ids[vid_id] = item["snippet"]
            except Exception as e:
                print(f"  ⚠️  YouTube search error ({query}): {e}")
            time.sleep(0.3)

    if not found_ids:
        print("  ⚠️  No YouTube results found")
        return []

    # Batch get stats + duration
    vid_ids_list = list(found_ids.keys())
    new_rows = []

    for i in range(0, len(vid_ids_list), 50):
        batch = vid_ids_list[i:i+50]
        params = urllib.parse.urlencode({
            "part": "statistics,contentDetails",
            "id": ",".join(batch),
            "key": api_key
        })
        try:
            stats_resp = json.loads(urllib.request.urlopen(
                f"https://www.googleapis.com/youtube/v3/videos?{params}", timeout=15).read())
        except Exception as e:
            print(f"  ⚠️  Stats fetch error: {e}")
            continue

        for item in stats_resp.get("items", []):
            vid_id = item["id"]
            url = f"https://www.youtube.com/watch?v={vid_id}"
            if url in existing_urls:
                continue

            stats   = item.get("statistics", {})
            details = item.get("contentDetails", {})
            views   = int(stats.get("viewCount") or 0)
            likes   = int(stats.get("likeCount") or 0)
            comments = int(stats.get("commentCount") or 0)
            duration = details.get("duration", "")  # ISO 8601 e.g. PT4M13S

            if views < MIN_YT_VIEWS:
                continue

            # Detect short vs regular
            is_short = False
            if "PT" in duration:
                import re
                mins = int(re.search(r'(\d+)M', duration).group(1)) if 'M' in duration else 0
                secs = int(re.search(r'(\d+)S', duration).group(1)) if 'S' in duration else 0
                total_secs = mins * 60 + secs
                is_short = total_secs <= 60

            snippet = found_ids.get(vid_id, {})
            title = snippet.get("title", "")
            channel = snippet.get("channelTitle", "")
            desc = (snippet.get("description") or "")[:150]
            content_type = "YouTube Short" if is_short else "YouTube Video"

            new_rows.append([
                today,                          # Date Added
                "YouTube",                      # Platform
                url,                            # URL
                channel,                        # Creator
                content_type,                   # Content Type (Short vs Video)
                desc,                           # Description
                "",                             # Transcription
                title,                          # Original Caption (title = hook idea)
                "",                             # Visual Hook
                "Text/Title",                   # Hook Type
                str(views),                     # Views
                str(likes),                     # Likes
                str(comments),                  # Comments
                "",                             # Saves
                "",                             # What's Working
                "",                             # A/B Test Notes
                "",                             # Use As Inspo For
                "No",                           # Copyright Version Created
                "New"                           # Status
            ])
            existing_urls.add(url)

            if len(new_rows) >= MAX_YOUTUBE:
                break

        if len(new_rows) >= MAX_YOUTUBE:
            break

    print(f"  ✅ {len(new_rows)} new YouTube videos")
    return new_rows

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"\n🔍 Inspiration Scraper — {date.today()}")
    print("=" * 45)

    env = load_env()
    apify_key   = env.get("APIFY_API_KEY", "")
    youtube_key = env.get("YOUTUBE_API_KEY", "")

    if not apify_key:
        print("❌ APIFY_API_KEY missing"); sys.exit(1)
    if not youtube_key:
        print("❌ YOUTUBE_API_KEY missing"); sys.exit(1)

    print("🔐 Authenticating with Google...")
    gtoken = get_gtoken()

    fix_header_add_comments(gtoken)

    print("🔗 Loading existing URLs (dedup check)...")
    existing_urls = get_existing_urls(gtoken)
    print(f"   {len(existing_urls)} URLs already in library")

    all_rows = []

    # Source A — Instagram
    if apify_key:
        ig_rows = scrape_instagram(apify_key, existing_urls)
        all_rows.extend(ig_rows)

    # Source B — YouTube
    if youtube_key:
        yt_rows = scrape_youtube(youtube_key, existing_urls)
        all_rows.extend(yt_rows)

    if all_rows:
        print(f"\n📝 Writing {len(all_rows)} rows to '{INSPO_TAB}'...")
        append_rows(gtoken, all_rows)
        ig_count = sum(1 for r in all_rows if r[1] == "Instagram")
        yt_count = sum(1 for r in all_rows if r[1] == "YouTube")
        print(f"✅ Done — {ig_count} Instagram + {yt_count} YouTube added")
    else:
        print("\n✅ Nothing new to add today")

    print(f"   Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")

if __name__ == "__main__":
    main()
