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
SHEET_ID        = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"  # Ideas & Inbox — always
INSPO_TAB       = "📥 Inspiration Library"

# ── Scraping limits ───────────────────────────────────────────────────────────
MAX_INSTAGRAM   = 25   # posts per run
MAX_YOUTUBE     = 15   # videos per run
DAYS_LOOKBACK   = 14   # only posts from last 14 days
MIN_IG_VIEWS    = 5000 # minimum views to consider
MIN_YT_VIEWS    = 25000

# ── Instagram targets — loaded from 🎯 Scraping Targets tab at runtime ───────
# Fallback values used only if the sheet is empty or unreachable.
_IG_HASHTAGS_FALLBACK = [
    "kitchenremodel", "bathroomremodel", "homeaddition", "shellconstruction",
    "cbsconstruction", "newconstruction", "pergola", "outdoorkitchen",
    "concretedesign", "concretedriveway", "roofing", "tileinstallation",
    "homerenovation", "beforeandafter", "customhomebuilder", "contractorlife",
    "constructionlife", "homeimprovement", "remodeling",
    "southfloridahomes", "southfloridaliving", "pompanobeach",
    "fortlauderdale", "browardcounty", "miamirealestate",
]
_ACCOUNTS_FALLBACK = []

# Populated at runtime by _load_opc_targets_from_sheet()
IG_HASHTAGS: list = []
ACCOUNTS_TO_MONITOR: list = []


def _load_opc_targets_from_sheet(token: str) -> None:
    """Read 🎯 Scraping Targets tab and populate IG_HASHTAGS / ACCOUNTS_TO_MONITOR.
    Falls back to hardcoded lists if the sheet is empty or the call fails."""
    global IG_HASHTAGS, ACCOUNTS_TO_MONITOR
    try:
        data = sheet_get(token, "/values/'%F0%9F%8E%AF%20Scraping%20Targets'!A1:F50")
        rows = data.get("values", [])
        if not rows or len(rows) < 2:
            raise ValueError("empty")

        # Row 0: TYPE/TARGET | OAK PARK | BRAZIL | UGC | NEWS/WORLD | NOTES
        # OAK PARK is always column index 1 (verified against live sheet 2026-04-20)
        hashtags, accounts = [], []
        for row in rows[1:]:
            if not row:
                continue
            target_type = row[0].strip().upper()
            cell = row[1].strip() if len(row) > 1 else ""
            values = [v.strip() for v in cell.split(",") if v.strip()]
            if target_type == "HASHTAG":
                hashtags.extend(values)
            elif target_type == "ACCOUNT":
                accounts.extend(values)

        IG_HASHTAGS = hashtags or _IG_HASHTAGS_FALLBACK
        ACCOUNTS_TO_MONITOR = accounts or _ACCOUNTS_FALLBACK
        print(f"✅ Scraping Targets loaded — {len(IG_HASHTAGS)} hashtags, "
              f"{len(ACCOUNTS_TO_MONITOR)} accounts")
    except Exception as e:
        IG_HASHTAGS = _IG_HASHTAGS_FALLBACK
        ACCOUNTS_TO_MONITOR = _ACCOUNTS_FALLBACK
        print(f"⚠️  Could not load Scraping Targets ({e}) — using fallback lists")

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

# ── Header (27 cols — matches col remap 2026-04-17: ContentHubLink moved to B) ─
INSPO_HEADER = [
    "Date Added", "Content Hub Link", "Platform", "URL", "Creator / Account",
    "Content Type", "Description", "Transcription", "Original Caption",
    "Visual Hook", "Hook Type", "Views",
    "Engagement Comments", "Saves / Shares",
    "What's Working", "A/B Test", "Brief / Angle", "Format", "Status",
    "Topic / Title", "Niche", "Comments",
    "AI Score (1-5)", "Date Status Changed", "Drive Folder Path", "My Raw Notes",
    "series_override"
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
    for k in ["APIFY_API_KEY", "YOUTUBE_API_KEY", "CLAUDE_KEY_4_CONTENT"]:
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
    """Rewrite full header if it doesn't match current schema (safe — never clears data rows)."""
    rows = sheet_get(token, f"/values/'{INSPO_TAB}'!1:1").get("values", [[]])
    current = rows[0] if rows else []
    # Use last column letter dynamically: len(INSPO_HEADER) cols, A=1 → letter
    last_col_idx = len(INSPO_HEADER) - 1  # 0-based
    last_col_letter = chr(ord('A') + last_col_idx) if last_col_idx < 26 else "Z"
    end_range = f"A1:{last_col_letter}1"
    if current != INSPO_HEADER:
        sheet_put(token,
                  f"/values/'{INSPO_TAB}'!{end_range}?valueInputOption=USER_ENTERED",
                  {"values": [INSPO_HEADER]})
        print(f"✅ Header updated to {len(INSPO_HEADER)}-col schema")
    else:
        print("✅ Header OK")

def get_existing_urls(token) -> set:
    """Return set of URLs already in Inspiration Library (column D after 2026-04-17 remap)."""
    try:
        rows = sheet_get(token, f"/values/'{INSPO_TAB}'!D:D").get("values", [])
        return {r[0].strip() for r in rows[1:] if r}
    except Exception:
        return set()

def get_existing_titles(token) -> set:
    """Return normalized set of Original Captions / titles (column I after 2026-04-17 remap)."""
    try:
        rows = sheet_get(token, f"/values/'{INSPO_TAB}'!I:I").get("values", [])
        return {r[0].strip().lower() for r in rows[1:] if r and r[0].strip()}
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

        # Accept images, carousels, and videos/reels
        item_type = item.get("type", "").lower()
        is_video  = item_type in ("video", "reel") or bool(item.get("isVideo"))
        is_sidecar = item_type in ("sidecar", "carousel") or bool(item.get("childPosts"))
        if not is_video and not is_sidecar and item_type not in ("image", "graphimage", "graphsidecar"):
            if not item.get("displayUrl"):
                continue

        views = int(item.get("videoViewCount") or item.get("videoPlayCount") or 0)
        if is_video and views < MIN_IG_VIEWS:
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
            today,          # A  Date Added
            "",             # B  Content Hub Link (scraper has no hub path)
            "Instagram",    # C  Platform
            url,            # D  URL
            f"@{username}", # E  Creator / Account
            "Reel",         # F  Content Type
            "",             # G  Description (empty — AI fills later)
            "",             # H  Transcription
            caption,        # I  Original Caption
            hook,           # J  Visual Hook
            "Visual",       # K  Hook Type
            str(views),     # L  Views
            str(comments),  # M  Engagement Comments
            "",             # N  Saves / Shares
            "",             # O  What's Working
            "",             # P  A/B Test
            "",             # Q  Brief / Angle
            "",             # R  Format
            "New",          # S  Status
            caption[:80],   # T  Topic / Title
            "OPC",          # U  Niche
            "",             # V  Comments (internal note)
            "",             # W  AI Score (1-5) — blank until processed
            "",             # X  Date Status Changed
            "",             # Y  Drive Folder Path
            "",             # Z  My Raw Notes
        ])
        existing_urls.add(url)

        if len(new_rows) >= MAX_INSTAGRAM:
            break

    print(f"  ✅ {len(new_rows)} new Instagram posts")
    return new_rows

# ── Source B — YouTube ────────────────────────────────────────────────────────
def scrape_youtube(api_key: str, existing_urls: set, existing_titles: set = None) -> list:
    print("\n▶️  Source B — YouTube (Data API v3)...")
    if existing_titles is None:
        existing_titles = set()

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
            if title.lower() in existing_titles:
                continue
            channel = snippet.get("channelTitle", "")
            desc = (snippet.get("description") or "")[:150]
            content_type = "YouTube Short" if is_short else "YouTube Video"

            existing_urls.add(url)
            existing_titles.add(title.lower())
            new_rows.append([
                today,          # A  Date Added
                "",             # B  Content Hub Link (scraper has no hub path)
                "YouTube",      # C  Platform
                url,            # D  URL
                channel,        # E  Creator / Account
                content_type,   # F  Content Type (Short vs Video)
                desc,           # G  Description
                "",             # H  Transcription
                title,          # I  Original Caption (title = hook idea)
                "",             # J  Visual Hook
                "Text/Title",   # K  Hook Type
                str(views),     # L  Views
                str(comments),  # M  Engagement Comments
                "",             # N  Saves / Shares
                "",             # O  What's Working
                "",             # P  A/B Test
                "",             # Q  Brief / Angle
                "",             # R  Format
                "New",          # S  Status
                title[:80],     # T  Topic / Title
                "OPC",          # U  Niche
                "",             # V  Comments (internal)
                "",             # W  AI Score (1-5)
                "",             # X  Date Status Changed
                "",             # Y  Drive Folder Path
                "",             # Z  My Raw Notes
            ])

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

    print("📋 Loading OPC targets from 🎯 Scraping Targets tab...")
    _load_opc_targets_from_sheet(gtoken)

    print("🔗 Loading existing URLs + titles (dedup check)...")
    existing_urls = get_existing_urls(gtoken)
    existing_titles = get_existing_titles(gtoken)
    print(f"   {len(existing_urls)} URLs, {len(existing_titles)} titles already in library")

    all_rows = []

    # Source A — Instagram
    if apify_key:
        ig_rows = scrape_instagram(apify_key, existing_urls)
        all_rows.extend(ig_rows)

    # Source B — YouTube
    if youtube_key:
        yt_rows = scrape_youtube(youtube_key, existing_urls, existing_titles)
        all_rows.extend(yt_rows)

    if all_rows:
        print(f"\n📝 Writing {len(all_rows)} rows to '{INSPO_TAB}'...")
        append_rows(gtoken, all_rows)
        ig_count = sum(1 for r in all_rows if r[2] == "Instagram")
        yt_count = sum(1 for r in all_rows if r[2] == "YouTube")
        print(f"✅ Done — {ig_count} Instagram + {yt_count} YouTube added")
    else:
        print("\n✅ Nothing new to add today")

    print(f"   Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")

    # Log to Content Creation Log
    try:
        import sys
        sys.path.insert(0, str(WORKSPACE / "oak-park-ai-hub" / "scripts"))
        os.environ.setdefault("SHEETS_TOKEN", TOKEN_FILE.read_text())
        from content_tracker import log_run
        log_run(pipeline="inspiration_scraper", trigger="scheduled",
                niche="OPC", status="success" if all_rows else "skipped",
                notes=f"{len(all_rows)} rows added (IG+YT)")
    except Exception: pass

if __name__ == "__main__":
    main()
