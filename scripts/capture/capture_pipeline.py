#!/usr/bin/env python3
"""
capture_pipeline.py
===================
Capture Pipeline v2 — runs via GitHub Actions, triggered from phone.

WHAT IT DOES:
  1. Fetches reel metadata via APIFY API (creator name, caption, likes, etc.)
     FYI: We use Apify (apify/instagram-scraper with directUrls) for IG metadata.
     yt-dlp handles audio download, but Apify gets us the caption, creator
     handle, view count, and other metadata yt-dlp doesn't return.
     API key: APIFY_API_KEY in GitHub Secrets.
     Console: https://console.apify.com/account/integrations
  2. Downloads audio from Instagram/TikTok/YouTube using yt-dlp
  3. Transcribes with OpenAI Whisper API (whisper-1)
  4. Saves transcript locally (uploaded as artifact)
  5. Routes based on --project (routing.py is source of truth):
     book          → Claude fact-checks → story doc in The Book Drive folder
                    → Book Tracker Stories tab → Calendar task
     brazil | usa  → Claude analyses → study doc in News/{Brazil,USA}/Captures
                    → Calendar task, Inspiration Library row, content brief (EN+PT)
     opc           → Claude classifies niche → Content Hub → Inspiration Library
                    → Calendar task

CREDITS / ATTRIBUTION:
  When --credits flag is set, the pipeline fetches the original creator's info
  via Apify and includes it in the output so captions can give proper credit.
  Fields saved: creator handle, creator name, original caption, source URL.

REQUIRED ENV VARS (all stored as GitHub Secrets in oak-park-ai-hub):
  OPENAI_API_KEY
  CLAUDE_KEY_4_CONTENT
  SHEETS_TOKEN    (OAuth refresh token JSON — same secret used by all other workflows)
  APIFY_API_KEY   — Used for fetching reel metadata (creator, caption, stats)
"""

import os
import sys
import json
import re
import argparse
import tempfile
import base64
import subprocess
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Routing — single source of truth for per-niche Drive destinations
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).parent.parent))
try:
    from routing import capture_folder as _capture_folder_fn
    def get_capture_folder(project: str) -> str:
        return _capture_folder_fn(project)
except Exception as _routing_err:
    def get_capture_folder(project: str) -> str:
        raise RuntimeError(
            f"routing.py failed to load — cannot determine capture folder for '{project}'. "
            f"Error: {_routing_err}"
        )

# ─── CONFIG ───────────────────────────────────────────────────────────────────

OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
CLAUDE_KEY_4_CONTENT  = os.getenv("CLAUDE_KEY_4_CONTENT", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")  # fallback transcription tier
# FYI: Apify API is used to fetch reel metadata (creator, caption, stats).
# Key stored in GitHub Secrets as APIFY_API_KEY.
# Get yours at: https://console.apify.com/account/integrations
APIFY_API_KEY      = os.getenv("APIFY_API_KEY", "")

# Shared quota / billing error helpers — see _quota_errors.py for patterns.
try:
    from _quota_errors import classify_error, short_sheet_message, send_quota_alert_email
except ImportError:
    # Safety net: if the module is missing, define no-ops so the pipeline never crashes
    # on an import error. The fallback behavior is still exercised on exception text.
    def classify_error(_): return None
    def short_sheet_message(_c, url=""): return ""
    def send_quota_alert_email(_c, context="", url=""): pass
# Run-level flag: set True when Apify returns "Monthly usage hard limit exceeded"
# so we skip all further Apify calls in the same run instead of hammering the endpoint.
_apify_limit_hit   = False
YOUTUBE_API_KEY    = os.getenv("YOUTUBE_API_KEY", "")
YT_COOKIES_RAW     = os.getenv("PRI_OP_YT_COOKIES", "")
IG_COOKIES_RAW     = os.getenv("PRI_OP_IG_COOKIES", "")

def _write_cookies_file() -> str:
    """Write PRI_OP_YT_COOKIES secret to a temp Netscape cookies.txt. Returns path or ''."""
    if not YT_COOKIES_RAW.strip():
        return ""
    path = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
    with open(path, "w") as f:
        f.write(YT_COOKIES_RAW)
    return path

def _write_ig_cookies_file() -> str:
    """Write PRI_OP_IG_COOKIES secret to a temp Netscape cookies.txt. Returns path or ''."""
    if not IG_COOKIES_RAW.strip():
        return ""
    path = os.path.join(tempfile.gettempdir(), "ig_cookies.txt")
    with open(path, "w") as f:
        f.write(IG_COOKIES_RAW)
    return path

_YT_COOKIES_PATH = ""   # lazily populated
_IG_COOKIES_PATH = ""   # lazily populated
_YT_COOKIE_FAILURE = False  # set True when yt-dlp hits bot-detection

# Spreadsheet IDs — hardcoded as defaults, can override via env
BOOK_TRACKER_ID    = os.getenv("BOOK_TRACKER_ID",    "1SeDFDisb0uNeyfyv5fCS_0x5EbkJRcFeS6CGuUmlH7c")
IDEAS_INBOX_ID     = os.getenv("IDEAS_INBOX_ID",     "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")

# Drive folder IDs — hardcoded as defaults
BOOK_FOLDER_ID              = "1HlY1tmUHmRZ_ZfPUzGpY_j7sHbe_OCz1"
CONTENT_CREATION_FOLDER_ID = "1um7y2Yt8zi9KGxev6kfFJYgrkMYwrCNh"  # Drive > Marketing > Claude Code Workspace > Content Creation

# Capture destinations — driven by routing.py (capture_folder_id per niche).
# Call get_capture_folder(project) at runtime — do NOT hardcode folder IDs here.
CONTENT_HUB_FOLDER_ID = "1p7s2Q7kCxzKdvaVRFxSoYAQ-IG_NhTqq" # OPC Content Hub — kept for reference

# Spreadsheet IDs for content pipeline
CONTENT_QUEUE_ID = "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg"  # Ideas Queue tab

GMAIL_FROM     = "priscila@oakpark-construction.com"
GMAIL_PASSWORD = os.getenv("PRI_OP_GMAIL_APP_PASSWORD", "")

TRANSCRIPTS_DIR = Path("transcripts")
TRANSCRIPTS_DIR.mkdir(exist_ok=True)


# ─── GOOGLE AUTH ──────────────────────────────────────────────────────────────

def _get_creds(scopes: list):
    """Return Google credentials via SHEETS_TOKEN OAuth refresh token."""
    from google.oauth2.credentials import Credentials
    import urllib.request, urllib.parse

    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        raise RuntimeError("No Google credentials. Set SHEETS_TOKEN secret.")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    return Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )


def _yt_cookie_alert(resolved=False):
    """Write or resolve a YT_COOKIE_ALERT row in the 📥 Inbox tab, and email if flagging."""
    import smtplib
    from email.mime.text import MIMEText
    from datetime import datetime, timezone

    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        return
    try:
        td = json.loads(raw)
        data = urllib.parse.urlencode({
            "client_id": td["client_id"], "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
        token = resp["access_token"]
    except Exception as e:
        print(f"  _yt_cookie_alert auth failed: {e}")
        return

    sheet_id = IDEAS_INBOX_ID
    tab = "📥 Inbox"
    status = "resolved" if resolved else "action_needed"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check if an unresolved alert row already exists
    enc = urllib.parse.quote(f"'{tab}'!A:C", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read()).get("values", [])
    except Exception:
        rows = []

    existing_row = None
    for i, row in enumerate(rows):
        if row and row[0] == "SYSTEM:YT_COOKIE_ALERT":
            existing_row = i + 1  # 1-indexed
            break

    if existing_row:
        enc2 = urllib.parse.quote(f"'{tab}'!C{existing_row}", safe="!:'")
        url2 = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc2}?valueInputOption=USER_ENTERED"
        urllib.request.urlopen(urllib.request.Request(url2,
            data=json.dumps({"values": [[status]]}).encode(),
            method="PUT", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}))
    elif not resolved:
        enc3 = urllib.parse.quote(f"'{tab}'!A:C", safe="!:'")
        url3 = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc3}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        row_data = [["SYSTEM:YT_COOKIE_ALERT",
                     f"YouTube cookies expired ({today}) — export from Chrome → update PRI_OP_YT_COOKIES secret in GitHub → Settings → Secrets → Actions",
                     "action_needed"]]
        urllib.request.urlopen(urllib.request.Request(url3,
            data=json.dumps({"values": row_data}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}))

    if not resolved and GMAIL_PASSWORD:
        try:
            msg = MIMEText(
                "⚠️ YouTube cookies have expired.\n\n"
                "yt-dlp is being blocked by YouTube bot-detection. Video downloads will fall back to transcript-only until fixed.\n\n"
                "To fix:\n"
                "1. Open Chrome and go to youtube.com (make sure you're logged in)\n"
                "2. Use the 'Get cookies.txt LOCALLY' extension to export cookies\n"
                "3. Go to github.com/priihigashi/oak-park-ai-hub → Settings → Secrets → Actions\n"
                "4. Update PRI_OP_YT_COOKIES with the new cookies file content\n\n"
                "The 4AM agent will remind you daily until this is fixed."
            )
            msg["Subject"] = "⚠️ Action needed: YouTube cookies expired"
            msg["From"] = GMAIL_FROM
            msg["To"] = GMAIL_FROM
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
                s.login(GMAIL_FROM, GMAIL_PASSWORD)
                s.sendmail(GMAIL_FROM, GMAIL_FROM, msg.as_string())
            print("  Cookie expiry alert email sent")
        except Exception as e:
            print(f"  Could not send cookie alert email: {e}")


def get_sheets_client():
    try:
        import gspread
        creds = _get_creds(["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)
    except Exception as e:
        print(f"  SKIP Sheets: {e}")
        return None


def get_drive_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds([
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
        ])
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"  SKIP Drive: {e}")
        return None


def get_docs_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds(["https://www.googleapis.com/auth/documents"])
        return build("docs", "v1", credentials=creds)
    except Exception as e:
        print(f"  SKIP Docs: {e}")
        return None


def get_calendar_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds(["https://www.googleapis.com/auth/calendar"])
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"  SKIP Calendar: {e}")
        return None


# ─── EMAIL NOTIFICATIONS ─────────────────────────────────────────────────────

def send_notification_email(subject: str, body: str):
    """Send email notification via Gmail SMTP. Non-fatal if unavailable."""
    if not GMAIL_PASSWORD:
        print("  SKIP email: PRI_OP_GMAIL_APP_PASSWORD not set")
        return
    import smtplib
    from email.mime.text import MIMEText
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = GMAIL_FROM
        msg["To"] = GMAIL_FROM
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_FROM, GMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"  Email sent: {subject}")
    except Exception as e:
        print(f"  WARNING email (non-fatal): {e}")


# ─── STEP 0: APIFY METADATA ──────────────────────────────────────────────────
# FYI: This step uses the Apify API to fetch reel metadata BEFORE downloading.
# It grabs: creator handle, creator name, caption, likes, views, comments count.
# This is how we get credits info for attribution in captions.
# Actor: apify/instagram-scraper with directUrls input.
# If APIFY_API_KEY is not set, this step is skipped (non-fatal).

APIFY_BASE = "https://api.apify.com/v2"


def _metadata_via_yt_dlp(url: str) -> dict:
    """Fallback IG metadata extraction via yt-dlp's --print-json. Free, no quota.
    yt-dlp already downloads IG audio successfully in this pipeline, so its metadata
    path is a reliable fallback when Apify is out. Returns {} on any failure.
    """
    try:
        proc = subprocess.run(
            ["yt-dlp", "--skip-download", "--dump-single-json", "--no-warnings", url],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print(f"  yt-dlp metadata fallback returned rc={proc.returncode}, stderr[:200]={proc.stderr[:200]}")
            return {}
        info = json.loads(proc.stdout.strip().splitlines()[-1])
        handle = info.get("uploader_id") or info.get("channel_id") or info.get("uploader") or ""
        name   = info.get("uploader") or info.get("channel") or handle
        caption = (info.get("description") or info.get("title") or "")[:500]
        return {
            "creator_handle": handle,
            "creator_name":   name,
            "caption":        caption,
            "likes":          info.get("like_count", 0) or 0,
            "comments":       info.get("comment_count", 0) or 0,
            "views":          info.get("view_count", 0) or 0,
            "timestamp":      info.get("timestamp", "") or "",
            "source_url":     url,
            "video_url":      info.get("url") or "",
        }
    except Exception as e:
        print(f"  yt-dlp metadata fallback failed: {type(e).__name__}: {e}")
        return {}


def _ig_metadata_fallback(url: str, reason: str) -> dict:
    """Fallback cascade when Apify fails: yt-dlp → (future: instaloader with IG_COOKIES) → {}."""
    md = _metadata_via_yt_dlp(url)
    if md.get("creator_handle"):
        print(f"  Metadata via yt-dlp fallback — @{md['creator_handle']} (reason: {reason})")
        return md
    # Future: instaloader with PRI_OP_IG_COOKIES could slot here as tier-2 fallback.
    print(f"  Metadata fallback returned nothing (reason: {reason})")
    return {}


def fetch_reel_metadata(url: str) -> dict:
    """Fetch reel metadata via Apify. Returns dict with creator info + stats.

    FYI: Uses apify/instagram-scraper actor with directUrls.
    Non-fatal — returns empty dict if Apify unavailable or fails.
    """
    global _apify_limit_hit
    if _apify_limit_hit:
        print("  SKIP Apify (limit already hit this run) — using yt-dlp metadata fallback")
        return _ig_metadata_fallback(url, reason="Apify limit (cached)")

    if not APIFY_API_KEY:
        print("  SKIP Apify metadata: APIFY_API_KEY not set — using yt-dlp fallback")
        return _ig_metadata_fallback(url, reason="APIFY_API_KEY missing")

    if "instagram.com" not in url:
        print("  SKIP Apify metadata: not an Instagram URL")
        return {}

    print(f"\n[0/3] Fetching reel metadata via Apify...")
    actor_id = "apify~instagram-scraper"
    input_data = {
        "directUrls": [url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        # DATACENTER proxy — much cheaper compute units than RESIDENTIAL.
        # RESIDENTIAL was burning the STARTER monthly limit rapidly.
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["DATACENTER"]},
    }

    try:
        run_resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            params={"token": APIFY_API_KEY},
            json=input_data,
            timeout=30,
        )
        # Surface the real Apify error before raising — the generic 403 HTTP message
        # hides useful info like "Monthly usage hard limit exceeded".
        if run_resp.status_code == 403:
            err = run_resp.json().get("error", {})
            err_type = err.get("type", "unknown")
            err_msg  = err.get("message", run_resp.text)
            if err_type == "platform-feature-disabled" and "limit" in err_msg.lower():
                _apify_limit_hit = True
                print(f"  WARNING Apify: monthly usage hard limit exceeded — switching to yt-dlp fallback for the rest of this run.")
                classified = classify_error(f"{err_type}: {err_msg}")
                if classified:
                    send_quota_alert_email(classified, context="Apify IG metadata", url=url)
                return _ig_metadata_fallback(url, reason="Apify monthly limit")
            print(f"  WARNING Apify 403: {err_type} — {err_msg}")
            return _ig_metadata_fallback(url, reason=f"Apify 403 {err_type}")
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        print(f"  Apify run started: {run_id}")

        # Poll until finished (max ~2 minutes)
        for attempt in range(12):
            time.sleep(10)
            status_resp = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": APIFY_API_KEY},
                timeout=15,
            )
            status = status_resp.json()["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if status != "SUCCEEDED":
            print(f"  WARNING: Apify run ended with status: {status} — falling back to yt-dlp")
            return _ig_metadata_fallback(url, reason=f"Apify run {status}")

        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
            timeout=30,
        )
        items = items_resp.json()
        if not items:
            print("  WARNING: Apify returned no results — falling back to yt-dlp")
            return _ig_metadata_fallback(url, reason="Apify empty result")

        item = items[0]
        metadata = {
            "creator_handle": item.get("ownerUsername", ""),
            "creator_name": item.get("ownerFullName", ""),
            "caption": item.get("caption", ""),
            "likes": item.get("likesCount", 0),
            "comments": item.get("commentsCount", 0),
            "views": item.get("videoViewCount", 0),
            "timestamp": item.get("timestamp", ""),
            "source_url": url,
            "video_url": item.get("videoUrl", ""),
        }
        print(f"  Creator: @{metadata['creator_handle']} ({metadata['creator_name']})")
        print(f"  Stats: {metadata['likes']} likes, {metadata['views']} views")
        print(f"  Caption: {metadata['caption'][:100]}...")
        return metadata

    except Exception as e:
        print(f"  WARNING Apify metadata (non-fatal): {e}")
        return {}


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _extract_youtube_id(url: str) -> str:
    """Extract video ID from YouTube URL (watch, youtu.be, shorts)."""
    m = re.search(r'(?:watch\?v=|youtu\.be/|shorts/)([^&/?]+)', url)
    return m.group(1) if m else ""


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _fetch_youtube_metadata_via_api(url: str) -> dict:
    """Fetch YouTube video metadata via YouTube Data API v3.
    Returns a dict with the same shape as fetch_reel_metadata() so downstream code
    (update_inspiration_library, run_opc, etc.) works unchanged.
    Requires YOUTUBE_API_KEY env var (stored in GitHub Secrets as YOUTUBE_API_KEY).
    Non-fatal — returns empty dict on any failure.
    """
    if not YOUTUBE_API_KEY:
        print("  SKIP YouTube Data API: YOUTUBE_API_KEY not set")
        return {}

    vid_id = _extract_youtube_id(url)
    if not vid_id:
        print("  SKIP YouTube Data API: cannot extract video ID")
        return {}

    print(f"\n[0/3] Fetching YouTube metadata via Data API (video ID: {vid_id})...")
    try:
        api_url = (
            "https://www.googleapis.com/youtube/v3/videos"
            f"?part=snippet,contentDetails,statistics&id={vid_id}&key={YOUTUBE_API_KEY}"
        )
        resp = requests.get(api_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            print(f"  YouTube Data API: no results for video ID {vid_id}")
            return {}

        item = items[0]
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        content_details = item.get("contentDetails", {})

        channel = snippet.get("channelTitle", "")
        title = snippet.get("title", "")
        description = snippet.get("description", "")
        tags = snippet.get("tags", [])
        thumbnail = (
            snippet.get("thumbnails", {}).get("high", {}).get("url", "")
            or snippet.get("thumbnails", {}).get("default", {}).get("url", "")
        )
        views = statistics.get("viewCount", "")
        duration = content_details.get("duration", "")

        metadata = {
            "creator_handle": channel,
            "creator_name": channel,
            # Use description as "caption" (what downstream expects); fall back to title
            "caption": (description[:500] if description else title),
            "title": title,
            "tags": tags,
            "thumbnail_url": thumbnail,
            "duration": duration,
            "views": int(views) if views else 0,
            "source_url": url,
            "video_url": "",  # not used for YouTube (no direct download needed)
        }
        print(f"  Title: {title}")
        print(f"  Channel: {channel}  Views: {views}")
        return metadata

    except Exception as e:
        print(f"  WARNING YouTube Data API (non-fatal): {e}")
        return {}


# ─── STEP 1: DOWNLOAD ─────────────────────────────────────────────────────────

def _find_audio_file(tmp_dir: str) -> str:
    """Find the downloaded audio file in tmp_dir regardless of extension."""
    for ext in ["mp3", "m4a", "webm", "ogg", "wav", "opus"]:
        path = os.path.join(tmp_dir, f"audio.{ext}")
        if os.path.exists(path):
            return path
    return ""


def _try_ytdlp(url: str, tmp_dir: str, extra_args: list = None) -> str:
    """Try yt-dlp download with optional extra args. Returns audio path or empty string.
    For non-YouTube (IG/TikTok), adds --keep-video so the original video file is saved
    alongside the mp3 — this lets download_video() reuse it without a second request.
    """
    global _YT_COOKIES_PATH, _IG_COOKIES_PATH
    output = os.path.join(tmp_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "0", "--output", output,
        "--no-playlist", "--quiet",
    ]
    if _is_youtube(url):
        if not _YT_COOKIES_PATH:
            _YT_COOKIES_PATH = _write_cookies_file()
        if _YT_COOKIES_PATH:
            cmd.extend(["--cookies", _YT_COOKIES_PATH])
    else:
        # Keep original video file so download_video() can reuse it without a 2nd request
        cmd.append("--keep-video")
        if not _IG_COOKIES_PATH:
            _IG_COOKIES_PATH = _write_ig_cookies_file()
        if _IG_COOKIES_PATH:
            cmd.extend(["--cookies", _IG_COOKIES_PATH])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return _find_audio_file(tmp_dir)
    stderr = result.stderr[:400]
    print(f"  yt-dlp failed: {stderr[:200]}")
    if _is_youtube(url):
        bot_keywords = ("sign in to confirm", "bot", "http error 429", "please sign in", "cookies")
        if any(k in stderr.lower() for k in bot_keywords):
            global _YT_COOKIE_FAILURE
            _YT_COOKIE_FAILURE = True
            print("  ⚠️  YouTube bot-detection — cookies may have expired")
    return ""


def _try_apify_youtube_download(url: str, tmp_dir: str) -> str:
    """Download YouTube audio via Apify actor. Returns audio path or empty string.
    Uses bernardo/youtube-scraper actor which can extract audio URLs.
    Falls back to streamers/youtube-scraper for direct download link.
    """
    global _apify_limit_hit
    if _apify_limit_hit:
        print("  SKIP Apify download: monthly usage limit already hit this run")
        return ""

    if not APIFY_API_KEY:
        print("  SKIP Apify download: APIFY_API_KEY not set")
        return ""

    vid_id = _extract_youtube_id(url)
    if not vid_id:
        print("  SKIP Apify download: cannot extract video ID")
        return ""

    print("  Trying Apify YouTube download...")
    actor_id = "bernardo~youtube-scraper"
    input_data = {
        "startUrls": [{"url": f"https://www.youtube.com/watch?v={vid_id}"}],
        "maxResults": 1,
        "proxy": {"useApifyProxy": True},
    }

    try:
        # Start the actor run
        run_resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            params={"token": APIFY_API_KEY},
            json=input_data,
            timeout=30,
        )
        if run_resp.status_code == 403:
            err = run_resp.json().get("error", {})
            err_type = err.get("type", "unknown")
            err_msg  = err.get("message", run_resp.text)
            if err_type == "platform-feature-disabled" and "limit" in err_msg.lower():
                _apify_limit_hit = True
                print(f"  WARNING Apify: monthly usage hard limit exceeded — skipping Apify for all remaining URLs this run.")
            else:
                print(f"  WARNING Apify 403: {err_type} — {err_msg}")
            return ""
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        print(f"  Apify run: {run_id}")

        # Poll (max ~3 min for video)
        for _ in range(18):
            time.sleep(10)
            status_resp = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": APIFY_API_KEY},
                timeout=15,
            )
            status = status_resp.json()["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if status != "SUCCEEDED":
            print(f"  Apify run ended: {status}")
            return ""

        # Get results — look for audio/video URL in output
        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
            timeout=30,
        )
        items = items_resp.json()
        if not items:
            print("  Apify: no results")
            return ""

        item = items[0]
        # Try to find a direct media URL in the result
        media_url = (
            item.get("mediaUrl")
            or item.get("videoUrl")
            or item.get("audioUrl")
            or item.get("url")
        )

        if not media_url or "youtube.com" in str(media_url):
            print("  Apify: no direct media URL in result")
            return ""

        # Download the media file
        print(f"  Downloading from Apify result...")
        audio_path = os.path.join(tmp_dir, "audio.mp3")
        dl = requests.get(media_url, timeout=120, stream=True)
        dl.raise_for_status()
        with open(audio_path, "wb") as f:
            for chunk in dl.iter_content(8192):
                f.write(chunk)

        size = os.path.getsize(audio_path) / 1024
        if size < 5:
            print(f"  Apify: downloaded file too small ({size:.0f} KB)")
            os.remove(audio_path)
            return ""

        print(f"  Apify download OK ({size:.0f} KB)")
        return audio_path

    except Exception as e:
        print(f"  Apify download failed (non-fatal): {e}")
        return ""


def _try_instaloader(url: str, tmp_dir: str) -> str:
    """Download Instagram reel via instaloader (GraphQL API, not shared_data scraping).
    Works on public reels with no credentials. Returns audio path or empty string.
    """
    try:
        import instaloader
    except ImportError:
        print("  instaloader not installed, skipping")
        return ""

    m = re.search(r'/(?:reel|p)/([A-Za-z0-9_-]+)', url)
    if not m:
        print("  instaloader: cannot extract shortcode from URL")
        return ""

    shortcode = m.group(1)
    print(f"  Trying instaloader for shortcode {shortcode}...")
    try:
        L = instaloader.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True,
        )
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=Path(tmp_dir))

        mp4_files = list(Path(tmp_dir).rglob("*.mp4"))
        if not mp4_files:
            print("  instaloader: no .mp4 found after download")
            return ""

        video_path = str(mp4_files[0])
        audio_path = os.path.join(tmp_dir, "audio.mp3")
        result = subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-acodec", "mp3", "-y", "-loglevel", "error", audio_path],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and os.path.exists(audio_path):
            size = os.path.getsize(audio_path) / 1024
            print(f"  Downloaded via instaloader ({size:.0f} KB audio)")
            return audio_path
        print(f"  instaloader: ffmpeg extraction failed: {result.stderr[:200]}")
        return ""
    except Exception as e:
        print(f"  instaloader failed: {e}")
        return ""


def download_audio(url: str, tmp_dir: str, metadata: dict = None) -> str:
    """Download audio: official APIs first for YouTube, yt-dlp for Instagram/TikTok.

    YouTube path (when YOUTUBE_API_KEY is set):
      Skip yt-dlp entirely — transcript comes from youtube-transcript-api, which
      uses official captions and is never blocked by GitHub runner IPs.

    Instagram/TikTok path (unchanged):
      yt-dlp → mobile UA → instaloader → Apify videoUrl fallback.
    """
    print(f"\n[1/3] Downloading audio: {url}")
    is_yt = _is_youtube(url)

    # YouTube fast path: skip yt-dlp entirely.
    # youtube-transcript-api uses the official captions API — no IP blocking possible.
    # Metadata (title, channel, views) comes from _fetch_youtube_metadata_via_api() in main().
    if is_yt and YOUTUBE_API_KEY:
        print("  YouTube URL — official API path (youtube-transcript-api, skipping yt-dlp)")
        return "__youtube_transcript_fallback__"

    # Tier 1: yt-dlp standard
    audio = _try_ytdlp(url, tmp_dir)
    if audio:
        size = os.path.getsize(audio) / 1024
        print(f"  Downloaded via yt-dlp ({size:.0f} KB)")
        return audio

    # Tier 1b: yt-dlp with iOS client trick (YouTube only)
    if is_yt:
        print("  Retrying yt-dlp with iOS client workaround...")
        audio = _try_ytdlp(url, tmp_dir, [
            "--extractor-args", "youtube:player_client=ios,web_creator",
        ])
        if audio:
            size = os.path.getsize(audio) / 1024
            print(f"  Downloaded via yt-dlp iOS trick ({size:.0f} KB)")
            return audio

    # Tier 2: Apify YouTube download
    if is_yt:
        audio = _try_apify_youtube_download(url, tmp_dir)
        if audio:
            return audio

    # Tier 3: transcript-api fallback (YouTube text only)
    if is_yt:
        print("  All download methods failed — falling back to transcript API (text only)")
        return "__youtube_transcript_fallback__"

    # Instagram/TikTok Tier 1b: Apify videoUrl — cookie-free, always fresh (fetched seconds ago)
    # Promoted to tier 1b so we never depend on session cookies staying valid.
    # yt-dlp + instaloader remain as fallbacks in case Apify CDN URL is missing or expires.
    if not is_yt:
        video_url = (metadata or {}).get("video_url", "")
        if video_url:
            print("  Trying Apify videoUrl (cookie-free CDN)...")
            try:
                audio_path = os.path.join(tmp_dir, "audio.mp4")
                resp = requests.get(video_url, timeout=120, stream=True,
                                    headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                with open(audio_path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                size = os.path.getsize(audio_path) / 1024
                if size > 100:
                    print(f"  Downloaded via Apify videoUrl ({size:.0f} KB)")
                    return audio_path
                print(f"  Apify videoUrl: file too small ({size:.0f} KB) — falling back")
            except Exception as e:
                print(f"  Apify videoUrl failed: {e} — falling back to yt-dlp")

    # Instagram/TikTok Tier 2: yt-dlp with mobile user-agent
    if not is_yt:
        print("  Retrying yt-dlp with mobile user-agent...")
        audio = _try_ytdlp(url, tmp_dir, [
            "--user-agent",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15",
        ])
        if audio:
            size = os.path.getsize(audio) / 1024
            print(f"  Downloaded via yt-dlp mobile UA ({size:.0f} KB)")
            return audio

    # Instagram/TikTok Tier 3: instaloader (uses GraphQL API — not shared_data scraping)
    if not is_yt:
        audio = _try_instaloader(url, tmp_dir)
        if audio:
            return audio

    print("  ERROR: all download methods failed for this URL")
    sys.exit(1)


# ─── STEP 1b: VIDEO DOWNLOAD ────────────────────────────────────────────────

def download_video(url: str, tmp_dir: str) -> str:
    """Download the actual video file for Content Hub storage + Remotion editing.
    YouTube capped at 720p to keep file sizes reasonable.
    Returns video file path or empty string (non-fatal — transcript still works).
    """
    print(f"\n[1b/3] Downloading video file...")
    is_yt = _is_youtube(url)

    output = os.path.join(tmp_dir, "video.%(ext)s")
    if is_yt:
        # YouTube: cap at 720p, merge to mp4
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "--merge-output-format", "mp4",
            "--output", output,
            "--no-playlist", "--quiet",
        ]
    else:
        # IG/TikTok: _try_ytdlp (audio step) already downloaded the reel with --keep-video,
        # so the original video file is already in tmp_dir as audio.<ext>.
        # Reuse it — no second request needed (Instagram rate-limits repeat requests).
        for ext in ["mp4", "mkv", "webm", "mov"]:
            src = os.path.join(tmp_dir, f"audio.{ext}")
            if os.path.exists(src) and os.path.getsize(src) > 100_000:
                dst = os.path.join(tmp_dir, f"video.{ext}")
                os.rename(src, dst)
                size_mb = os.path.getsize(dst) / (1024 * 1024)
                print(f"  Video reused from audio step ({size_mb:.1f} MB)")
                return dst
        # Audio step didn't keep video (older run or different URL) — fall through to download
        cmd = [
            "yt-dlp",
            "--output", output,
            "--no-playlist", "--quiet",
        ]

    global _YT_COOKIES_PATH
    if is_yt:
        if not _YT_COOKIES_PATH:
            _YT_COOKIES_PATH = _write_cookies_file()
        if _YT_COOKIES_PATH:
            cmd.extend(["--cookies", _YT_COOKIES_PATH])

    # Try standard yt-dlp first
    result = subprocess.run(cmd + [url], capture_output=True, text=True)
    video_path = os.path.join(tmp_dir, "video.mp4")

    if result.returncode != 0 and is_yt:
        # Try iOS client trick for YouTube
        print("  Video download retry with iOS client...")
        result = subprocess.run(
            cmd + ["--extractor-args", "youtube:player_client=ios,web_creator", url],
            capture_output=True, text=True,
        )

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "")[:300]
        print(f"  Video download failed (non-fatal): {err}")
        print(f"  VIDEO_DOWNLOAD_FAILED: {url}")
        return ""

    # Find the output file (extension might vary)
    if not os.path.exists(video_path):
        for ext in ["mp4", "mkv", "webm", "mov"]:
            alt = os.path.join(tmp_dir, f"video.{ext}")
            if os.path.exists(alt):
                video_path = alt
                break

    if not os.path.exists(video_path):
        print("  Video file not found after download")
        return ""

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"  Video downloaded ({size_mb:.1f} MB)")
    if size_mb > 200:
        print(f"  WARNING: Large video ({size_mb:.0f} MB) — upload may be slow")
    return video_path


# ─── STEP 2: TRANSCRIBE ───────────────────────────────────────────────────────
#
# Transcription cascade — never relies on a single provider:
#   Tier 1: OpenAI Whisper API            (fastest, costs credits)
#   Tier 2: faster-whisper (local CPU)    (free, runs in GH runner, no quota)
#   Tier 3: Gemini 1.5 Flash (audio in)   (free tier, text-only — no SRT)
# Every tier logs a line; _whisper_with_fallback emails a quota alert on tier-1
# billing failure so Priscila knows WHY we fell back.


def _try_openai_whisper(audio_path: str, fmt: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(audio_path, "rb") as f:
        return client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format=("srt" if fmt == "srt" else "text"),
        )


def _try_faster_whisper(audio_path: str, fmt: str) -> str:
    """Local CPU-based Whisper replacement. No API, no quota, no billing."""
    from faster_whisper import WhisperModel
    # "base" = 74MB model, good enough for <60s reels. Upgrade to "small" if accuracy lacks.
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_path, beam_size=5)
    segments = list(segments)
    if fmt == "srt":
        def _ts(t: float) -> str:
            h = int(t // 3600); m = int((t % 3600) // 60); s = t - h * 3600 - m * 60
            return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
        return "\n".join(
            f"{i}\n{_ts(seg.start)} --> {_ts(seg.end)}\n{seg.text.strip()}\n"
            for i, seg in enumerate(segments, 1)
        )
    return " ".join(seg.text.strip() for seg in segments)


def _try_gemini_transcribe(audio_path: str, fmt: str) -> str:
    """Gemini 1.5 Flash accepts audio input — free tier 15 req/min. Text-only; no SRT."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    if fmt == "srt":
        raise RuntimeError("Gemini does not support SRT output")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    audio_file = genai.upload_file(audio_path)
    model = genai.GenerativeModel("gemini-1.5-flash")
    resp  = model.generate_content([
        "Transcribe this audio exactly as spoken. Output ONLY the transcript text — no commentary, no timestamps, no speaker labels.",
        audio_file,
    ])
    return resp.text


def _whisper_with_fallback(audio_path: str, *, fmt: str = "text", url: str = "") -> str:
    """Cascade: OpenAI → faster-whisper → Gemini. fmt='text'|'srt'. Emails quota alerts."""
    last_err = None

    # Tier 1 — OpenAI Whisper API
    try:
        result = _try_openai_whisper(audio_path, fmt)
        print(f"  Transcribed via OpenAI Whisper ({len(result)} chars, fmt={fmt})")
        return result
    except Exception as e:
        last_err = e
        err_text   = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            print(f"  OpenAI Whisper → {classified['service']}:{classified['type']} — falling back")
            send_quota_alert_email(classified, context=f"Whisper transcription (fmt={fmt})", url=url)
        else:
            print(f"  OpenAI Whisper failed ({err_text}) — falling back")

    # Tier 2 — faster-whisper (local, free)
    try:
        result = _try_faster_whisper(audio_path, fmt)
        print(f"  Transcribed via faster-whisper local ({len(result)} chars, fmt={fmt})")
        return result
    except Exception as e:
        last_err = e
        print(f"  faster-whisper fallback failed: {type(e).__name__}: {e}")

    # Tier 3 — Gemini (text only)
    if fmt == "text":
        try:
            result = _try_gemini_transcribe(audio_path, fmt)
            print(f"  Transcribed via Gemini 1.5 Flash ({len(result)} chars)")
            return result
        except Exception as e:
            last_err = e
            print(f"  Gemini fallback failed: {type(e).__name__}: {e}")

    if fmt == "srt":
        return ""  # non-fatal — SRT is optional
    raise RuntimeError(f"All transcription providers failed. Last error: {last_err}") from last_err


def transcribe_audio(audio_path: str, url: str = "") -> str:
    print("\n[2/3] Transcribing...")
    # YouTube fallback: use youtube-transcript-api (no download, no cookies needed)
    if audio_path == "__youtube_transcript_fallback__":
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            vid_id = _extract_youtube_id(url)
            api = YouTubeTranscriptApi()
            fetched = api.fetch(vid_id)
            result = " ".join([t.text for t in fetched])
            print(f"  Transcribed via youtube-transcript-api ({len(result)} chars)")
            return result
        except Exception as e:
            # youtube-transcript-api hits InnerTube directly and is blocked at the network
            # layer when GitHub runners use Azure IPs. Cookies don't apply to this library.
            # Fall through to yt-dlp + Whisper using PRI_OP_YT_COOKIES (already set as secret).
            print(f"  youtube-transcript-api failed ({e}) — trying yt-dlp + Whisper fallback...")
            import tempfile as _tmp
            with _tmp.TemporaryDirectory() as _td:
                fallback_audio = _try_ytdlp(url, _td)
                if not fallback_audio and _is_youtube(url):
                    print("  Retrying yt-dlp with iOS client trick...")
                    fallback_audio = _try_ytdlp(url, _td, [
                        "--extractor-args", "youtube:player_client=ios,web_creator",
                    ])
                if not fallback_audio and _is_youtube(url):
                    print("  Trying Apify YouTube download...")
                    fallback_audio = _try_apify_youtube_download(url, _td)
                if not fallback_audio:
                    raise RuntimeError(
                        f"All YouTube paths failed for {url}: transcript-api blocked + "
                        f"yt-dlp + Apify all returned no audio"
                    ) from e
                return _whisper_with_fallback(fallback_audio, fmt="text", url=url)

    return _whisper_with_fallback(audio_path, fmt="text", url=url)


def get_caption_srt(audio_path: str) -> str:
    """Get timestamped SRT captions via the Whisper cascade. Returns '' on total failure
    (non-fatal — SRT captions are only used for Remotion news renders).
    Note: Gemini tier does not support SRT, so this effectively cascades OpenAI → faster-whisper.
    """
    try:
        return _whisper_with_fallback(audio_path, fmt="srt", url="")
    except Exception as e:
        print(f"  WARNING: SRT generation failed across all providers (non-fatal): {e}")
        return ""


# ─── STEP 3: SAVE TRANSCRIPT ──────────────────────────────────────────────────

def save_transcript(transcript: str, url: str, story_id: str, project: str) -> str:
    print("\n[3/3] Saving transcript...")
    slug = url.split("/reel/")[-1].split("/")[0].split("?")[0] if "/reel/" in url else "capture"
    filename = f"{story_id}_{slug}_transcript.txt"
    filepath = TRANSCRIPTS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"STORY ID: {story_id}\nPROJECT: {project}\nURL: {url}\nDATE: {datetime.now()}\n\n{transcript}")
    print(f"  Saved: {filepath}")
    return str(filepath)


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def analyze_book(transcript: str, url: str, story_id: str, notes: str) -> str:  # noqa: keep name for backward compat
    if not CLAUDE_KEY_4_CONTENT:
        return f"[PENDING — CLAUDE_KEY_4_CONTENT required]\n\n{transcript}"
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    print("  Claude (claude-opus-4-6) fact-checking...")
    prompt = f"""Run capture_crazy_ideas skill for RECEIPTS book.

Story ID: {story_id}
Source URL: {url}
Notes: {notes or "None"}
Date: {datetime.now().strftime("%Y-%m-%d")}

TRANSCRIPT:
{transcript}

Produce STORY DOCUMENT (no markdown tables — plain text only):

STORY ID: {story_id}
BOOK SECTION: [Trump Pardons | Political Deals | Historical Context | Other]
DATE CAPTURED: {datetime.now().strftime("%Y-%m-%d")}
SOURCE URL: {url}
TRANSCRIPT: [paste above]

SPEAKER: [full name, title, affiliation]
CREDENTIALS: [what makes them credible or not]
CREDIBILITY: HIGH / MEDIUM / LOW / UNVERIFIED

BACKGROUND (8th grade level, 2-3 paragraphs):

CLAIMS MADE:
  Claim 1: [quote or paraphrase]
  Fact Check: TRUE / FALSE / PARTIALLY TRUE / UNVERIFIED
  Evidence: [what we found]
  Official Sources: [URL1] | [URL2] | [URL3]

SPEAKER VERIFICATION:
  Red flags: [vague? no sources?]
  Credibility: HIGH / MEDIUM / LOW / UNVERIFIED

MEETING VERIFICATION:
  Meeting claimed: YES [describe] / NO
  Evidence: [URL or "No corroborating evidence found"]
  Official confirmation: [yes/no/silent]

PRESIDENTIAL / OFFICIAL STATEMENTS:
  [Quote with source URL. If none: "No official statement found."]

PATTERN / CONNECTION:
  [Donations? Deals? Timing? Visits?]
  [PATTERN - investigate further] or [No pattern found yet]

VISUAL SUGGESTIONS:
  - [Image/screenshot idea 1]
  - [Image/screenshot idea 2]

NEWS POST ANGLE:
  Hook: [scroll-stopping opening line]
  Core message: [concrete examples, not just negatives]
  Format: [talking head / carousel / before-after]

PORTUGUESE ANGLE:
  Relevant to Brazilian audience: YES / NO
  PT-BR hook: [if YES]

QR CODE SOURCES:
  1. [Source name] - [URL]
  2. [Source name] - [URL]
  3. [Source name] - [URL]

BOOK READY: YES / NO / NEEDS MORE RESEARCH"""
    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def analyze_news(transcript: str, url: str, story_id: str, notes: str, creator_name: str = "") -> str:
    if not CLAUDE_KEY_4_CONTENT:
        return f"[PENDING — CLAUDE_KEY_4_CONTENT required]\n\n{transcript}"
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    print("  Claude (claude-opus-4-6) News analysis...")
    prompt = f"""Analyze this content for the News political/civic page.
Study the format and identify how to do it better — more examples, more teaching, not just negatives.

Story ID: {story_id}
Source URL: {url}
Notes: {notes or "None"}

TRANSCRIPT:
{transcript}

Produce NEWS CAPTURE DOCUMENT (no markdown tables):

STORY ID: {story_id}
PROJECT: NEWS
DATE: {datetime.now().strftime("%Y-%m-%d")}
SOURCE URL: {url}

SPEAKER ANALYSIS:
  Known speaker (from Apify metadata): {creator_name or "UNKNOWN — identify from transcript context"}
  Who: [name, title, platform/following — confirm or correct the above]
  Credibility: HIGH / MEDIUM / LOW / UNVERIFIED
  Red flags: [vague? no sources? only negatives?]

CONTENT ANALYSIS:
  Main message: [one sentence]
  Emotional tone: [anger / fear / inspiration / outrage]
  What works: [specific format strengths]
  What's missing: [e.g. no examples, only complaints, no solutions]

NEWS POST ANGLE:
  Core message: [what this post says — with concrete examples]
  Teaching moment: [what audience learns and can apply]
  Format: [talking head / carousel / before-after / text overlay]
  CTA: [what action we want]

HOOK OPTIONS — write 3, each a different Hormozi category. For each: EN line + PT-BR line + why it works (1 sentence).

  HOOK A — Contrarian (challenge a belief the audience holds):
    EN: [hook]
    PT-BR: [hook]
    Why: [1 sentence]

  HOOK B — Curiosity Gap (open an information gap they must close):
    EN: [hook]
    PT-BR: [hook]
    Why: [1 sentence]

  HOOK C — Pain Agitate or Pattern Interrupt:
    EN: [hook]
    PT-BR: [hook]
    Why: [1 sentence]

  RECOMMENDED FOR REEL COVER: [A / B / C] — reason
  RECOMMENDED FOR CAROUSEL SLIDE 1: [A / B / C] — reason

STUDY NOTES (3 specific ways to do it better):
  1. [Improvement]
  2. [Improvement]
  3. [Improvement]

CONTENT READY: YES / NO / NEEDS REFINEMENT"""
    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def analyze_opc(transcript: str, url: str, notes: str) -> dict:
    if not CLAUDE_KEY_4_CONTENT:
        return {"niche": "Oak Park", "classification": "NEEDS_REVIEW", "summary": transcript[:150]}
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    print("  Claude (claude-sonnet-4-6) classifying...")
    prompt = f"""Classify this video transcript for Oak Park Construction content pipeline.
URL: {url}
Notes: {notes or "None"}
TRANSCRIPT: {transcript}

Fake news / misinformation detection: Does this content contain or spread a specific false or misleading claim (viral myth, fabricated statistic, doctored quote, out-of-context clip)? If yes, set fake_news_route to "A" if the source clip of the spreader is available, or "B" if an expert/outlet has already debunked it. If the niche is Brazil or bilingual, use series_override "Verificamos". If the niche is USA, use series_override "Fact-Checked".

Respond with JSON only:
{{"niche": "Oak Park" or "Brazil" or "UGC" or "News", "content_type": "Talking Head/Expert" or "Project Progress/Before-After" or "Product Tips" or "Other", "classification": "READY" or "NEEDS_REVIEW" or "NOT_RELEVANT", "summary": "one sentence", "hook": "suggested hook for repost or inspiration", "notes": "why classified this way", "series_override": "Verificamos" or "Fact-Checked" or "", "fake_news_route": "A" or "B" or "", "fake_news_confidence": "high" or "medium" or "low" or "", "additional_niches": [] or ["Brazil"] or ["News"] or ["Brazil", "News"] — list of OTHER niches this content should ALSO be captured for. Rules: (1) if user notes say "both", "bilingual", "brazil and usa", "for both" → include the other niche; (2) if topic is international (foreign elections, global leaders, geopolitics affecting multiple language audiences) → add both "Brazil" and "News"; (3) Brazil-only domestic politics → empty list; (4) USA-only domestic → empty list; (5) construction/OPC → empty list}}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {"niche": "Oak Park", "classification": "NEEDS_REVIEW", "summary": text[:150]}


# ─── DRIVE DOC ────────────────────────────────────────────────────────────────

def create_drive_doc(title: str, content: str, folder_id: str) -> str:
    drive = get_drive_service()
    if not drive:
        return ""
    try:
        file = drive.files().create(
            body={"name": title, "mimeType": "application/vnd.google-apps.document", "parents": [folder_id]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        file_id = file.get("id")
        doc_url = file.get("webViewLink", f"https://docs.google.com/document/d/{file_id}/edit")
        print(f"  Drive doc: {doc_url}")
        docs = get_docs_service()
        if docs and content:
            try:
                docs.documents().batchUpdate(
                    documentId=file_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]}
                ).execute()
            except Exception as e:
                print(f"  WARNING doc write: {e}")
        return doc_url
    except Exception as e:
        print(f"  WARNING Drive: {e}")
        return ""


# ─── SHEETS ───────────────────────────────────────────────────────────────────

def update_book_tracker(story_id, url, doc_url, analysis, notes):
    gc = get_sheets_client()
    if not gc:
        return
    try:
        sh = gc.open_by_key(BOOK_TRACKER_ID)
        stories = sh.worksheet("Stories")
        summary = analysis[:150].replace("\n", " ")
        section = "Other"
        for s in ["Trump Pardons", "Political Deals", "Historical Context"]:
            if s in analysis:
                section = s
                break
        stories.append_row([
            story_id, section, "", summary, url,
            "NEEDS REVIEW", notes or "", "", "", "", "",
            datetime.now().strftime("%Y-%m-%d"), url, doc_url, "NO"
        ])
        print(f"  Book Tracker Stories: {story_id} added")
        try:
            inbox = sh.worksheet("Inbox")
            for i, row in enumerate(inbox.get_all_values()):
                if url.split("?")[0] in str(row):
                    inbox.update_cell(i + 1, 4, story_id)
                    inbox.update_cell(i + 1, 5, f"CAPTURED {datetime.now().strftime('%Y-%m-%d')}")
                    break
        except Exception:
            pass
    except Exception as e:
        print(f"  WARNING Sheets: {e}")


def _detect_platform(url: str) -> str:
    """Detect platform name from URL for Inspiration Library Platform column."""
    u = url.lower()
    if "instagram.com" in u:
        return "Instagram"
    elif "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    elif "tiktok.com" in u:
        return "TikTok"
    elif "twitter.com" in u or "x.com" in u:
        return "Twitter/X"
    return "Web"


def update_inspiration_library(url, transcript, classification, hub_url="", doc_url="", metadata=None, user_notes=""):
    """
    Additive-only. Writes a NEW row. Never updates/overwrites existing rows.
    All columns resolved by header-name lookup — resilient to any future reorder.
    Schema as of 2026-04-17: A=Date Added, B=Content Hub Link, C=Platform,
    D=URL, E=Creator/Account, F=Content Type, G=Description, H=Transcription,
    I=Original Caption, J=Visual Hook, K=Hook Type, L=Views, M+= unchanged.
    """
    gc = get_sheets_client()
    if not gc:
        return
    metadata = metadata or {}
    try:
        sh = gc.open_by_key(IDEAS_INBOX_ID)
        lib = sh.worksheet("📥 Inspiration Library")

        # Resolve ALL columns by header name — never use positional index
        headers = lib.row_values(1)
        col_pos = {h.strip().lower(): i for i, h in enumerate(headers)}

        def _set_col(row, col_name, value):
            idx = col_pos.get(col_name.lower())
            if idx is not None:
                while len(row) <= idx:
                    row.append("")
                row[idx] = str(value) if value is not None else ""

        creator = metadata.get("creator_handle", "")
        if creator and not creator.startswith("@"):
            creator = f"@{creator}"

        base_row = []
        _set_col(base_row, "date added",        datetime.now().strftime("%Y-%m-%d"))
        _set_col(base_row, "content hub link",  hub_url or doc_url)
        _set_col(base_row, "platform",          _detect_platform(url))
        _set_col(base_row, "url",               url)
        _set_col(base_row, "creator / account", creator)
        _set_col(base_row, "content type",      classification.get("content_type", ""))
        _set_col(base_row, "description",       classification.get("summary", ""))
        _set_col(base_row, "transcription",     transcript[:300])
        _set_col(base_row, "original caption",  metadata.get("caption", "")[:300])
        _set_col(base_row, "visual hook",       classification.get("hook", ""))
        _set_col(base_row, "hook type",         "")
        _set_col(base_row, "views",             str(metadata.get("views", "")) if metadata.get("views") else "")
        _set_col(base_row, "series_override",   classification.get("series_override", ""))
        _set_col(base_row, "fake_news_route",   classification.get("fake_news_route", ""))
        _set_col(base_row, "fake_news_confidence", classification.get("fake_news_confidence", ""))
        if user_notes:
            _set_col(base_row, "my raw notes",  user_notes)

        lib.append_row(base_row, value_input_option="USER_ENTERED")
        print(f"  Inspiration Library updated (user_notes={'yes' if user_notes else 'no'})")
    except Exception as e:
        print(f"  WARNING Sheets: {e}")


# ─── CALENDAR ─────────────────────────────────────────────────────────────────

def create_calendar_task(story_id, project, url, doc_url, preview, notes, hub_url=""):
    cal = get_calendar_service()
    if not cal:
        return
    labels = {
        "book": "BOOK CAPTURE",
        "brazil": "BRAZIL NEWS CAPTURE",
        "usa": "USA NEWS CAPTURE",
        "opc": "OPC CAPTURE",
        "ugc": "UGC CAPTURE",
        "stocks": "STOCKS CAPTURE",
        "higashi": "HIGASHI CAPTURE",
    }
    label = labels.get(project, "CAPTURE")
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    try:
        cal.events().insert(calendarId="primary", body={
            "summary": f"{label} — {story_id} — Review Required",
            "description": (
                f"{label}: {story_id}\n\nSOURCE: {url}\n\n"
                f"CONTENT HUB: {hub_url or 'check Drive'}\n"
                f"CONTENT BRIEF: {doc_url or 'check artifacts'}\n\n"
                f"TRANSCRIPT PREVIEW:\n{preview[:400]}\n\n"
                f"NOTES: {notes or 'None'}\n\n"
                f"NEXT STEPS:\n1. Review content brief in Drive\n2. Pick carousel or reel idea\n"
                f"3. Move to production"
            ),
            "start": {"dateTime": tomorrow.isoformat(), "timeZone": "America/New_York"},
            "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat(), "timeZone": "America/New_York"},
        }).execute()
        print(f"  Calendar task: tomorrow 9am ET")
    except Exception as e:
        print(f"  WARNING Calendar: {e}")


# ─── QUEUE DEDUP ──────────────────────────────────────────────────────────────

def _mark_queue_processed(url: str):
    """If this URL exists in the '📲 Capture Queue' tab mark it as processed (D=TRUE).
    Called at the end of every run_* function so manual captures don't get re-run by
    the daily queue processor. Non-fatal — never blocks pipeline completion.
    """
    import urllib.request as _ur
    import urllib.parse as _up
    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        return
    try:
        td = json.loads(raw)
        data = _up.urlencode({
            "client_id": td["client_id"], "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(_ur.urlopen(
            _ur.Request("https://oauth2.googleapis.com/token", data=data)
        ).read())
        token = resp["access_token"]

        sheet_id = IDEAS_INBOX_ID
        tab = "📲 Capture Queue"
        enc = _up.quote(f"'{tab}'!A2:H", safe="!:'")
        rows_resp = json.loads(_ur.urlopen(
            _ur.Request(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}",
                headers={"Authorization": f"Bearer {token}"}
            )
        ).read())
        rows = rows_resp.get("values", [])

        # Normalize URL for comparison (strip query params that vary between triggers)
        norm = url.split("?")[0].rstrip("/")
        for i, row in enumerate(rows):
            row_url = (row[1].strip() if len(row) > 1 else "").split("?")[0].rstrip("/")
            if row_url == norm:
                sheet_row = i + 2  # 1-indexed, skip header
                processed = (row[3].strip().upper() if len(row) > 3 else "")
                if processed == "TRUE":
                    return  # already marked, skip
                update_url = (
                    f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values:batchUpdate"
                )
                body = json.dumps({
                    "valueInputOption": "USER_ENTERED",
                    "data": [
                        {"range": f"'{tab}'!D{sheet_row}", "values": [[True]]},
                        {"range": f"'{tab}'!F{sheet_row}", "values": [["Manual capture"]]},
                    ],
                }).encode()
                _ur.urlopen(_ur.Request(
                    update_url, data=body,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                )).read()
                print(f"  Queue row {sheet_row} marked processed (manual capture)")
                return
    except Exception as e:
        print(f"  WARNING _mark_queue_processed (non-fatal): {e}")


# ─── PIPELINES ────────────────────────────────────────────────────────────────

def run_book(args, transcript):
    print("\n[BOOK] Running fact-check pipeline...")

    # Research user notes BEFORE fact-check so findings land in the story doc.
    # Manual tasks (ex: "find clip of XYZ") go to Inbox instead of being lost.
    print("  Researching user notes before fact-check...")
    book_research = research_from_notes(args.notes or "", transcript, "Book", args.story_id)
    if book_research.get("manual_tasks"):
        _write_manual_tasks_to_inbox(book_research["manual_tasks"], args.story_id, args.url)

    # Feed research findings into analysis context so the book fact-checker sees them.
    enriched_notes = args.notes or ""
    if book_research.get("research_tasks"):
        research_block = "\n\nPRE-RESEARCH FINDINGS:\n" + "\n".join(
            f"- Q: {t.get('question','')}\n  A: {t.get('answer','')}" for t in book_research["research_tasks"]
        )
        enriched_notes = (enriched_notes + research_block).strip()

    analysis = analyze_book(transcript, args.url, args.story_id, enriched_notes)
    path = TRANSCRIPTS_DIR / f"{args.story_id}_analysis.txt"
    path.write_text(analysis, encoding="utf-8")
    print(f"  Analysis saved: {path}")
    doc_title = f"{args.story_id} — {datetime.now().strftime('%Y-%m-%d')}"
    doc_url = create_drive_doc(doc_title, analysis, BOOK_FOLDER_ID)
    update_book_tracker(args.story_id, args.url, doc_url, analysis, args.notes or "")
    create_calendar_task(args.story_id, args.project, args.url, doc_url, transcript[:400], args.notes or "")
    print(f"\n{'='*50}\nBOOK CAPTURE DONE\nStory ID: {args.story_id}\nDoc: {doc_url or 'check artifacts'}\n{'='*50}")
    _mark_queue_processed(args.url)
    try:
        import sys; sys.path.insert(0, str(Path(__file__).parent.parent))
        from content_tracker import log_run
        log_run(pipeline="capture_pipeline", trigger="manual", url=args.url,
                niche="", project="book", status="success", drive_path=doc_url or "", notes=args.story_id)
    except Exception: pass


def run_news(args, transcript, video_path: str = "", srt_content: str = "", creator_name: str = ""):
    print("\n[NEWS] Running format analysis...")
    analysis = analyze_news(transcript, args.url, args.story_id, args.notes or "", creator_name=creator_name)
    path = TRANSCRIPTS_DIR / f"{args.story_id}_news.txt"
    path.write_text(analysis, encoding="utf-8")

    # Save SRT captions file alongside transcript (needed by Remotion for timed captions)
    if srt_content:
        srt_path = TRANSCRIPTS_DIR / f"{args.story_id}_captions.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        print(f"  SRT saved: {srt_path}")

    _news_capture_folder = get_capture_folder(args.project)

    # Create per-story subfolder inside niche Captures/ so files don't pile up flat.
    # Naming mirrors save_to_content_hub: YYYY-MM-DD_NICHE_PLATFORM-SOURCEID
    _date = datetime.now().strftime("%Y-%m-%d")
    _niche = {"brazil": "BRAZIL", "usa": "USA"}.get(args.project, args.project.upper())
    if "instagram.com" in args.url:
        _plat = "IG"; _m = re.search(r'/reel/([^/?]+)', args.url); _src = _m.group(1) if _m else args.story_id
    elif "youtu" in args.url:
        _plat = "YT"; _m = re.search(r'(?:watch\?v=|youtu\.be/|shorts/)([^&/?]+)', args.url); _src = _m.group(1) if _m else args.story_id
    elif "tiktok.com" in args.url:
        _plat = "TK"; _m = re.search(r'/video/(\d+)', args.url); _src = _m.group(1) if _m else args.story_id
    else:
        _plat = "WEB"; _src = args.story_id
    _story_folder_name = f"{_date}_{_niche}_{_plat}-{_src}"
    _story_folder_id = _news_capture_folder  # fallback: write flat if Drive unavailable
    _story_folder_url = ""
    _drive_svc = get_drive_service()
    if _drive_svc:
        try:
            _sf = _drive_svc.files().create(
                body={"name": _story_folder_name, "mimeType": "application/vnd.google-apps.folder",
                      "parents": [_news_capture_folder]},
                supportsAllDrives=True, fields="id,webViewLink"
            ).execute()
            _story_folder_id = _sf["id"]
            _story_folder_url = _sf.get("webViewLink", f"https://drive.google.com/drive/folders/{_story_folder_id}")
            print(f"  Story folder: {_story_folder_url}")
        except Exception as _e:
            print(f"  WARNING: story subfolder creation failed, writing flat: {_e}")

    doc_url = create_drive_doc(f"{args.story_id} — NEWS — {_date}", analysis, _story_folder_id)
    create_calendar_task(args.story_id, args.project, args.url, doc_url, transcript[:400], args.notes or "")

    # Upload video to niche Captures folder so Remotion can reference it (not lost in tmpdir)
    video_drive_url = ""
    if video_path and os.path.exists(video_path):
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            token_data = json.loads(os.getenv("SHEETS_TOKEN", "{}"))
            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
            )
            drive = build("drive", "v3", credentials=creds)
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"  Uploading video to Captures folder ({size_mb:.1f} MB)...")
            file_meta = {"name": f"{args.story_id}_original.mp4", "parents": [_story_folder_id]}
            media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
            result = drive.files().create(
                body=file_meta, media_body=media, supportsAllDrives=True, fields="id,webViewLink"
            ).execute()
            video_drive_url = result.get("webViewLink", "")
            print(f"  Video uploaded: {video_drive_url}")

            # Upload SRT to same folder
            if srt_content:
                srt_tmp = Path("/tmp") / f"{args.story_id}_captions.srt"
                srt_tmp.write_text(srt_content, encoding="utf-8")
                srt_meta = {"name": f"{args.story_id}_captions.srt", "parents": [_story_folder_id]}
                srt_media = MediaFileUpload(str(srt_tmp), mimetype="text/plain")
                drive.files().create(
                    body=srt_meta, media_body=srt_media, supportsAllDrives=True
                ).execute()
                print(f"  SRT uploaded to Captures folder")
        except Exception as e:
            print(f"  WARNING: video upload failed (non-fatal): {e}")

    # Research notes before writing the brief — facts land IN the doc, not lost
    print("  Researching user notes before brief generation...")
    news_research = research_from_notes(args.notes or "", transcript, "News", args.story_id)
    if news_research.get("manual_tasks"):
        _write_manual_tasks_to_inbox(news_research["manual_tasks"], args.story_id, args.url)

    # Generate bilingual content brief alongside the deep analysis (ported from run_opc)
    print("  Generating bilingual content brief for News capture...")
    news_classification = {"niche": "News", "summary": args.story_id, "content_type": "Carousel"}
    brief = generate_content_brief(transcript, args.url, news_classification, args.notes or "",
                                   research=news_research)
    brief_pt = translate_to_pt(brief)
    brief_doc_url = ""
    try:
        drive_svc = get_drive_service()
        docs_svc = get_docs_service()
        if drive_svc and docs_svc:
            brief_doc = drive_svc.files().create(
                body={"name": f"[CONTENT BRIEF] {args.story_id}",
                      "mimeType": "application/vnd.google-apps.document",
                      "parents": [_story_folder_id]},
                supportsAllDrives=True, fields="id,webViewLink"
            ).execute()
            brief_doc_id = brief_doc["id"]
            brief_doc_url = brief_doc.get("webViewLink", f"https://docs.google.com/document/d/{brief_doc_id}/edit")
            full_brief = f"{brief}\n\n{'='*60}\nPT-BR VERSION\n{'='*60}\n\n{brief_pt}"
            docs_svc.documents().batchUpdate(
                documentId=brief_doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": full_brief}}]}
            ).execute()
            print(f"  Bilingual content brief: {brief_doc_url}")
    except Exception as e:
        print(f"  WARNING: content brief doc failed (non-fatal): {e}")

    # Add to Inspiration Library so news captures are discoverable (ported from run_opc)
    news_cl = {"niche": "News", "summary": args.story_id, "content_type": "News Capture",
               "hook": "", "series_override": "", "fake_news_route": "", "fake_news_confidence": ""}
    update_inspiration_library(args.url, transcript, news_cl,
                               hub_url=doc_url or "", doc_url=brief_doc_url,
                               metadata={}, user_notes=args.notes or "")
    for _extra_niche in news_cl.get("additional_niches", []):
        if _extra_niche and _extra_niche.lower() != news_cl.get("niche", "").lower():
            _extra_cl = dict(news_cl); _extra_cl["niche"] = _extra_niche
            _extra_notes = f"[CROSS-NICHE — also for {_extra_niche}] " + (args.notes or "")
            update_inspiration_library(args.url, transcript, _extra_cl, hub_url=doc_url or "",
                                       doc_url=brief_doc_url, metadata={}, user_notes=_extra_notes.strip())

    # Trigger topic cluster scraper (ported from run_opc — applies to political/Brazil news)
    if os.getenv("APIFY_API_KEY"):
        _trigger_topic_scraper(news_cl)

    print(f"\n{'='*50}\nNEWS CAPTURE DONE\nStory ID: {args.story_id}\nDoc: {doc_url or 'check artifacts'}\nBrief: {brief_doc_url or 'check artifacts'}\nVideo: {video_drive_url or 'upload failed — check artifacts'}\n{'='*50}")

    # Send completion email so Priscila knows the capture worked
    send_notification_email(
        subject=f"News capture done — {args.story_id}",
        body=(
            f"Story ID: {args.story_id}\n"
            f"Source: {args.url}\n\n"
            f"Analysis doc: {doc_url or 'check News Drive folder'}\n"
            f"Content brief (EN+PT): {brief_doc_url or 'check Drive'}\n"
            f"Video in Drive: {video_drive_url or 'not uploaded — check GitHub artifact'}\n"
            f"SRT captions: {'generated and uploaded' if srt_content else 'not generated (audio issue)'}\n"
            f"Inspiration Library: row added\n\n"
            f"Next step: trigger render-video.yml with story_id={args.story_id} to build the FORMAT-001 reel.\n\n"
            f"Transcript preview:\n{transcript[:400]}"
        ),
    )

    _mark_queue_processed(args.url)
    try:
        import sys; sys.path.insert(0, str(Path(__file__).parent.parent))
        from content_tracker import log_run
        log_run(pipeline="capture_pipeline", trigger="manual", url=args.url,
                niche="news", project="news", status="success",
                drive_path=doc_url or "", notes=args.story_id)
    except Exception: pass


def _trigger_topic_scraper(classification):
    """Dispatch topic_scraper.yml after a Brazil capture. Non-fatal if it fails."""
    import urllib.request
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("  SKIP topic scraper dispatch: GITHUB_TOKEN not set")
        return
    keywords = (classification.get("hook", "") or classification.get("summary", ""))[:80].strip()
    if not keywords:
        print("  SKIP topic scraper dispatch: no keywords extracted")
        return
    payload = json.dumps({"ref": "main", "inputs": {"keywords": keywords}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/priihigashi/oak-park-ai-hub/actions/workflows/topic_scraper.yml/dispatches",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            print(f"  ✅ Topic Cluster Scraper triggered (keywords: {keywords!r})")
    except Exception as e:
        print(f"  ⚠️  Topic scraper dispatch failed (non-fatal): {e}")


# ─── CONTENT WORKSPACE ────────────────────────────────────────────────────────

def research_from_notes(notes: str, transcript: str, niche: str, story_id: str = "") -> dict:
    """Parse user notes and execute research BEFORE the brief is written.

    Step 1 (Haiku): classify notes → research questions / structure hints /
                    format flags / manual tasks Claude cannot do.
    Step 2 (Sonnet): run each research question and return findings.
    Manual tasks are returned separately so the caller can write them to Inbox.

    Returns dict with keys: research_tasks, structure_hints, format_flags, manual_tasks.
    Returns empty dict on no notes or no API key.
    """
    empty = {"research_tasks": [], "structure_hints": [], "format_flags": {}, "manual_tasks": []}
    if not notes or notes.strip().lower() in ("none", "n/a", ""):
        return empty
    if not CLAUDE_KEY_4_CONTENT:
        return empty

    import anthropic, re as _re
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)

    # ── Step 1: parse notes into categories (Haiku — fast + cheap) ──
    parse_prompt = f"""Analyze these user notes from a video capture and categorize every instruction.

Notes: {notes}
Niche: {niche}
Transcript excerpt: {transcript[:600]}

Output ONLY valid JSON — no explanation, no markdown fences:
{{
  "research": ["specific question requiring fact-finding", ...],
  "structure": ["guidance on content approach/layout", ...],
  "format": {{"bilingual": bool, "format_code": "FORMAT-001 or empty", "motion": bool, "series": "Verificamos or empty", "remotion": bool}},
  "manual": ["task Claude cannot automate, e.g. find specific clip/video", ...]
}}

Rules:
- research = politician actions, statistics, debunking claims, historical facts, network connections
- structure = "not a repost", "first slide video", "use facial data", approach hints
- format = FORMAT-001/002, bilingual, Remotion, motion, series name
- manual = finding a specific asset/clip/image that requires human search"""

    try:
        parse_resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=800,
            messages=[{"role": "user", "content": parse_prompt}]
        )
        raw = parse_resp.content[0].text.strip()
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not m:
            print(f"  research_from_notes: could not parse JSON — skipping research")
            return empty
        categories = json.loads(m.group())
    except Exception as e:
        print(f"  research_from_notes: parse step failed ({e}) — skipping research")
        return empty

    # ── Step 2: build niche-aware researcher persona, then run each question ──
    niche_lower = niche.lower()
    if any(x in niche_lower for x in ("news", "brazil", "usa", "book")):
        researcher_persona = (
            "You are a research assistant for a bilingual investigative content creator "
            "covering Brazil and USA political news.\n"
            "Include: specific dates, numbers, official figures, political party affiliations, "
            "what the official narrative says vs. documented evidence, credible sources "
            "(newspaper names, government reports, NGO names), hidden connections or "
            "underreported angles. Flag clearly if events occurred after August 2025."
        )
    elif any(x in niche_lower for x in ("opc", "oak park", "construction", "contractor")):
        researcher_persona = (
            "You are a research assistant for Oak Park Construction, a residential remodeling "
            "and construction company in Florida.\n"
            "Include: specific materials, techniques, building codes, product recommendations, "
            "common mistakes and how to avoid them, cost ranges, timelines, industry standards, "
            "safety considerations, permit requirements. Cite trade associations or manufacturer specs."
        )
    elif any(x in niche_lower for x in ("ugc", "amazon", "product")):
        researcher_persona = (
            "You are a research assistant for a UGC/product content creator.\n"
            "Include: product specs, alternatives, price ranges, user pain points, "
            "competing products, common complaints or red flags from reviews."
        )
    else:
        researcher_persona = (
            "You are a research assistant for a bilingual social media content creator.\n"
            "Provide specific facts, dates, numbers, and credible sources. "
            "Flag clearly if you are uncertain."
        )

    research_tasks = []
    for question in categories.get("research", []):
        if not question.strip():
            continue
        research_prompt = f"""{researcher_persona}

Research question: {question}

Video transcript context:
{transcript[:1200]}

This will be embedded directly into a content brief. Be direct and factual."""

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1500,
                messages=[{"role": "user", "content": research_prompt}]
            )
            result = resp.content[0].text.strip()
            research_tasks.append({"question": question, "result": result})
            print(f"  Researched: {question[:70]}...")
        except Exception as e:
            print(f"  research_from_notes: research failed for '{question[:50]}': {e}")
            research_tasks.append({"question": question, "result": f"[Research failed: {e}]"})

    return {
        "research_tasks": research_tasks,
        "structure_hints": categories.get("structure", []),
        "format_flags": categories.get("format", {}),
        "manual_tasks": categories.get("manual", []),
    }


def _write_manual_tasks_to_inbox(manual_tasks: list, story_id: str, url: str):
    """Write tasks Claude cannot automate to 📥 Inbox tab for human follow-up."""
    if not manual_tasks:
        return
    gc = get_sheets_client()
    if not gc:
        return
    try:
        sh = gc.open_by_key(IDEAS_INBOX_ID)
        inbox = sh.worksheet("📥 Inbox")
        for task in manual_tasks:
            row = [url, f"[MANUAL — {story_id}] {task}", "Pending capture task"]
            inbox.append_row(row, value_input_option="USER_ENTERED")
            print(f"  Inbox: manual task added → {task[:70]}")
    except Exception as e:
        print(f"  _write_manual_tasks_to_inbox failed (non-fatal): {e}")


def generate_content_brief(transcript: str, url: str, classification: dict, notes: str,
                           research: dict = None) -> str:
    """Ask Claude to generate carousel + reel + topic breakdowns from transcript.
    research: optional dict from research_from_notes() — embedded before slides.
    Returns plain text content brief (no markdown tables — avoids Docs API 400 errors).
    Falls back to transcript + classification JSON if CLAUDE_KEY_4_CONTENT not set.
    """
    if not CLAUDE_KEY_4_CONTENT:
        return f"SOURCE: {url}\nNOTES: {notes or 'None'}\n\nTRANSCRIPT:\n{transcript}\n\nClassification:\n{json.dumps(classification, indent=2)}"
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    niche = classification.get("niche", "General")

    # Build research block to inject into prompt
    research_block = ""
    if research and research.get("research_tasks"):
        lines = ["RESEARCH FINDINGS (fact-checked before this brief was written):"]
        for i, rt in enumerate(research["research_tasks"], 1):
            lines.append(f"\nQ{i}: {rt['question']}")
            lines.append(f"A{i}: {rt['result']}")
        research_block = "\n".join(lines)

    structure_block = ""
    if research and research.get("structure_hints"):
        structure_block = "CREATOR STRUCTURE NOTES:\n" + "\n".join(f"- {h}" for h in research["structure_hints"])

    format_block = ""
    if research and research.get("format_flags"):
        ff = research["format_flags"]
        flags = []
        if ff.get("format_code"): flags.append(f"FORMAT: {ff['format_code']}")
        if ff.get("bilingual"):   flags.append("BILINGUAL: PT + EN required")
        if ff.get("series"):      flags.append(f"SERIES: {ff['series']}")
        if ff.get("remotion"):    flags.append("REMOTION: motion version required")
        if flags:
            format_block = "FORMAT FLAGS:\n" + "\n".join(f"- {f}" for f in flags)

    prompt = f"""You are a bilingual content creator (EN + PT-BR). Analyze this transcript and produce a CONTENT BRIEF.

Source URL: {url}
Niche: {niche}
Creator Notes: {notes or 'None'}

{research_block}

{structure_block}

{format_block}

TRANSCRIPT:
{transcript}

IMPORTANT: Use the RESEARCH FINDINGS above as verified facts — embed them into the slides and key facts section. Follow the CREATOR STRUCTURE NOTES and FORMAT FLAGS exactly.

Output plain text only — NO markdown tables. Use this structure:

CONTENT BRIEF
Date: {datetime.now().strftime('%Y-%m-%d')}
Source: {url}
Niche: {niche}
Status: DRAFT

KEY FACTS (5-8 verifiable claims — pull from transcript AND research findings above):

HOOK EN: [scroll-stopping first line in English]
HOOK PT-BR: [same in Brazilian Portuguese — rewrite, do not translate literally]

SHORT CAROUSEL (6 slides):
SLIDE 1 HOOK — EN: / PT:
SLIDE 2 — EN: / PT:
SLIDE 3 — EN: / PT: / SOURCE ON SLIDE:
SLIDE 4 — EN: / PT:
SLIDE 5 — EN: / PT:
SLIDE 6 CTA — EN: / PT:

LONG CAROUSEL (only if content has 4+ strong distinct points):
SLIDE 1 HOOK — EN: / PT:
[continue for each slide]
SLIDE [N] CTA — EN: / PT:

REEL IDEA:
Hook EN: [first line]
Hook PT: [first line]
Format: [what goes on screen]

TOPIC 1: [title] — [angle — one concept, explained simply]
TOPIC 2: [title] — [angle — one concept, explained simply]
TOPIC 3: [title] — [angle — one concept, explained simply]

CAPTION EN:
[full caption text]

CAPTION PT-BR:
[full caption text]

SOURCES (list from transcript + research findings):
1.
2.
3.

STATUS: DRAFT — text ready, art needed"""

    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def translate_to_pt(text: str) -> str:
    """Translate content brief text to Brazilian Portuguese via Claude Haiku.
    Uses the same urllib pattern as build_render_props.py. Non-fatal — returns
    the original text unchanged if translation fails or key is missing.
    """
    if not CLAUDE_KEY_4_CONTENT or not text.strip():
        return text
    import urllib.request as _urllib_request
    prompt = (
        "Translate this English content brief to Brazilian Portuguese (PT-BR). "
        "Keep the same structure, section headers, and formatting. "
        "Rewrite idioms and hooks naturally for Brazilian audiences — do not translate literally. "
        "Output ONLY the translated text, no commentary.\n\n"
        + text
    )
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    try:
        req = _urllib_request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": CLAUDE_KEY_4_CONTENT,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp = json.loads(_urllib_request.urlopen(req, timeout=60).read())
        translated = resp["content"][0]["text"]
        print(f"  PT-BR translation: {len(translated)} chars")
        return translated
    except Exception as e:
        print(f"  WARNING: PT translation failed (non-fatal): {e}")
        return text


def save_to_content_hub(story_id: str, url: str, transcript: str, classification: dict, video_path: str = "", notes: str = "", project: str = "opc") -> str:
    """Save transcript + resources + video (+ optional user notes) to Content Hub story folder. Returns folder URL."""
    drive = get_drive_service()
    if not drive:
        print("  SKIP Content Hub: Drive unavailable")
        return ""
    try:
        from googleapiclient.http import MediaInMemoryUpload
        niche = classification.get("niche", "General").upper()
        date = datetime.now().strftime("%Y-%m-%d")
        # Build deterministic slug from URL: PLATFORM-SOURCEID (not Claude summary)
        if "instagram.com" in url:
            platform = "IG"
            m = re.search(r'/reel/([^/?]+)', url)
            src_id = m.group(1) if m else story_id
        elif "youtu" in url:
            platform = "YT"
            m = re.search(r'(?:watch\?v=|youtu\.be/|shorts/)([^&/?]+)', url)
            src_id = m.group(1) if m else story_id
        elif "tiktok.com" in url:
            platform = "TK"
            m = re.search(r'/video/(\d+)', url)
            src_id = m.group(1) if m else story_id
        else:
            platform = "WEB"
            src_id = story_id
        slug = f"{platform}-{src_id}"
        folder_name = f"{date}_{niche}_{slug}"
        folder = drive.files().create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder",
                  "parents": [get_capture_folder(project)]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        folder_id = folder["id"]
        # Save transcript
        transcript_content = f"STORY ID: {story_id}\nSOURCE: {url}\nDATE: {date}\n\n{transcript}"
        media = MediaInMemoryUpload(transcript_content.encode("utf-8"), mimetype="text/plain")
        drive.files().create(
            body={"name": "transcript.txt", "parents": [folder_id]},
            media_body=media, supportsAllDrives=True, fields="id"
        ).execute()
        # Save resources stub
        resources_content = f"RESOURCE LINKS — {folder_name}\n\nSOURCE: {url}\n\nADD MORE LINKS HERE AS RESEARCH PROGRESSES\n"
        media2 = MediaInMemoryUpload(resources_content.encode("utf-8"), mimetype="text/plain")
        drive.files().create(
            body={"name": "resources.txt", "parents": [folder_id]},
            media_body=media2, supportsAllDrives=True, fields="id"
        ).execute()
        # Save user notes if provided at capture time
        if notes:
            notes_content = f"USER NOTES — {folder_name}\nCaptured: {date}\n\n{notes}\n"
            media3 = MediaInMemoryUpload(notes_content.encode("utf-8"), mimetype="text/plain")
            drive.files().create(
                body={"name": "user_notes.txt", "parents": [folder_id]},
                media_body=media3, supportsAllDrives=True, fields="id"
            ).execute()
        # Upload video file if available
        if video_path and os.path.exists(video_path):
            from googleapiclient.http import MediaFileUpload
            ext = os.path.splitext(video_path)[1] or ".mp4"
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"  Uploading video ({size_mb:.1f} MB) to Content Hub...")
            video_media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
            request = drive.files().create(
                body={"name": f"video{ext}", "parents": [folder_id]},
                media_body=video_media, supportsAllDrives=True, fields="id"
            )
            # Resumable upload for large files
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    print(f"  Upload progress: {int(status.progress() * 100)}%")
            print(f"  Video uploaded to Content Hub")
        folder_url = folder.get("webViewLink", f"https://drive.google.com/drive/folders/{folder_id}")
        print(f"  Content Hub story folder: {folder_url}")
        return folder_url
    except Exception as e:
        print(f"  WARNING Content Hub: {e}")
        return ""


def save_to_news_folder(story_id: str, url: str, transcript: str, classification: dict,
                         video_path: str = "", notes: str = "", research: dict = None,
                         project: str = "brazil") -> tuple:
    """News niche routing — saves to Big Crazy Ideas > News with shared/english/portuguese structure.

    Structure created:
      News / YYYY-MM-DD_topic-slug /
        _shared/
          original_reel.mp4, transcript.txt, resources.txt, topic_brief.md
          jewish_voices_broll/   (empty, for manual/future B-roll collection)
        english/
          [CONTENT BRIEF].gdoc   (Claude-generated carousel + reel + topics)
        portuguese/
          PENDING_translation.md (placeholder until AI translation flow is built)

    Returns: (story_folder_url, brief_doc_url) — matches create_content_workspace signature
    so run_opc can use the same return contract.
    """
    drive = get_drive_service()
    if not drive:
        print("  SKIP News folder: Drive unavailable")
        return "", ""
    try:
        from googleapiclient.http import MediaInMemoryUpload, MediaFileUpload
        date = datetime.now().strftime("%Y-%m-%d")
        summary = classification.get("summary", story_id)[:50]
        slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")[:50] or story_id.lower()
        folder_name = f"{date}_{slug}"

        # 1. Create story folder under News (routing.py supplies the per-niche capture folder)
        story_folder = drive.files().create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder",
                  "parents": [get_capture_folder(project)]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        story_id_drive = story_folder["id"]
        story_url = story_folder.get("webViewLink", f"https://drive.google.com/drive/folders/{story_id_drive}")
        print(f"  News story folder: {story_url}")

        # 2. Create _shared, english, portuguese subfolders
        def _mkfolder(name, parent):
            return drive.files().create(
                body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]},
                supportsAllDrives=True, fields="id"
            ).execute()["id"]

        shared_id = _mkfolder("_shared", story_id_drive)
        english_id = _mkfolder("english", story_id_drive)
        portuguese_id = _mkfolder("portuguese", story_id_drive)
        _mkfolder("jewish_voices_broll", shared_id)

        # 3. Save transcript + resources + topic_brief placeholder to _shared
        transcript_content = f"STORY ID: {story_id}\nSOURCE: {url}\nDATE: {date}\n\n{transcript}"
        drive.files().create(
            body={"name": "transcript.txt", "parents": [shared_id]},
            media_body=MediaInMemoryUpload(transcript_content.encode("utf-8"), mimetype="text/plain"),
            supportsAllDrives=True, fields="id"
        ).execute()
        resources_content = (
            f"RESOURCE LINKS — {folder_name}\n\nSOURCE: {url}\n\n"
            f"NOTES: {notes or '(none)'}\n\n"
            f"ADD MORE LINKS HERE AS RESEARCH PROGRESSES\n"
        )
        drive.files().create(
            body={"name": "resources.txt", "parents": [shared_id]},
            media_body=MediaInMemoryUpload(resources_content.encode("utf-8"), mimetype="text/plain"),
            supportsAllDrives=True, fields="id"
        ).execute()
        brief_placeholder = (
            f"# Topic Brief — {summary}\n\n"
            f"Captured: {date}\nSource: {url}\nNiche: News (USA + Brazil pages)\n\n"
            f"## Notes\n{notes or '(add angle, proof points, b-roll targets, usage rules)'}\n"
        )
        drive.files().create(
            body={"name": "topic_brief.md", "parents": [shared_id]},
            media_body=MediaInMemoryUpload(brief_placeholder.encode("utf-8"), mimetype="text/markdown"),
            supportsAllDrives=True, fields="id"
        ).execute()

        # 4. Upload video to _shared
        if video_path and os.path.exists(video_path):
            ext = os.path.splitext(video_path)[1] or ".mp4"
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"  Uploading video ({size_mb:.1f} MB) to News _shared...")
            video_media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
            request = drive.files().create(
                body={"name": f"original_reel{ext}", "parents": [shared_id]},
                media_body=video_media, supportsAllDrives=True, fields="id"
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    print(f"  Upload progress: {int(status.progress() * 100)}%")
            print(f"  Video uploaded to News _shared")

        # 5. Create content brief doc in english subfolder
        brief = generate_content_brief(transcript, url, classification, notes, research=research)

        # 5b. Translate to PT-BR via Claude Haiku (same pattern as build_render_props.py)
        print("  Translating brief to PT-BR (Claude Haiku)...")
        brief_pt = translate_to_pt(brief)

        doc_url = ""
        try:
            doc = drive.files().create(
                body={"name": f"[CONTENT BRIEF] {summary}",
                      "mimeType": "application/vnd.google-apps.document",
                      "parents": [english_id]},
                supportsAllDrives=True, fields="id,webViewLink"
            ).execute()
            doc_id = doc["id"]
            doc_url = doc.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")
            docs = get_docs_service()
            if docs and brief:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": brief}}]}
                ).execute()
            print(f"  English content brief: {doc_url}")
        except Exception as e:
            print(f"  WARNING News brief doc: {e}")

        # 6. Create PT-BR content brief doc in portuguese subfolder (AI translated via Haiku)
        pt_doc_url = ""
        try:
            doc_pt = drive.files().create(
                body={"name": f"[CONTENT BRIEF PT] {summary}",
                      "mimeType": "application/vnd.google-apps.document",
                      "parents": [portuguese_id]},
                supportsAllDrives=True, fields="id,webViewLink"
            ).execute()
            doc_pt_id = doc_pt["id"]
            pt_doc_url = doc_pt.get("webViewLink", f"https://docs.google.com/document/d/{doc_pt_id}/edit")
            docs_pt = get_docs_service()
            if docs_pt and brief_pt:
                docs_pt.documents().batchUpdate(
                    documentId=doc_pt_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": brief_pt}}]}
                ).execute()
            print(f"  Portuguese content brief: {pt_doc_url}")
        except Exception as e:
            print(f"  WARNING PT doc: {e}")

        # 7. Log to Ideas Queue (same pattern as content_workspace)
        gc = get_sheets_client()
        if gc:
            try:
                sh = gc.open_by_key(CONTENT_QUEUE_ID)
                queue = sh.worksheet("\U0001f4a1 Ideas Queue")
                queue.append_row([
                    summary,
                    classification.get("content_type", "Carousel"),
                    "Instagram",
                    classification.get("hook", ""),
                    "DRAFT \u2014 text needed",
                    "HIGH",
                    f"News folder: {story_url} | Brief EN: {doc_url} | Brief PT: {pt_doc_url} | Captured: {date}",
                    url,
                ])
                print("  Ideas Queue: row added")
            except Exception as e:
                print(f"  WARNING Ideas Queue: {e}")

        return story_url, doc_url
    except Exception as e:
        print(f"  WARNING News folder: {e}")
        return "", ""


def create_content_workspace(story_id: str, title: str, transcript: str,
                              classification: dict, url: str, notes: str = "",
                              research: dict = None) -> tuple:
    """Creates Drive workspace for a content piece.

    Structure created:
      Content Creation / [title] /
        Art/
        Caption/
        Reel/
        [CONTENT BRIEF] [title].gdoc  ← Claude-generated carousel + reel + topics

    Also logs one row to the Ideas Queue tab in the Content Queue spreadsheet.
    Returns: (folder_url, doc_url) — empty strings on failure (non-fatal).
    """
    drive = get_drive_service()
    if not drive:
        print("  SKIP workspace: Drive unavailable")
        return "", ""

    # 1. Create parent folder
    try:
        folder = drive.files().create(
            body={"name": title, "mimeType": "application/vnd.google-apps.folder",
                  "parents": [CONTENT_CREATION_FOLDER_ID]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        folder_id = folder["id"]
        folder_url = folder.get("webViewLink", f"https://drive.google.com/drive/folders/{folder_id}")
        print(f"  Drive folder: {folder_url}")
    except Exception as e:
        print(f"  WARNING folder creation: {e}")
        return "", ""

    # 2. Create Art/, Caption/, Reel/ subfolders
    for sub in ["Art", "Caption", "Reel"]:
        try:
            drive.files().create(
                body={"name": sub, "mimeType": "application/vnd.google-apps.folder",
                      "parents": [folder_id]},
                supportsAllDrives=True, fields="id"
            ).execute()
        except Exception as e:
            print(f"  WARNING subfolder {sub}: {e}")

    # 3. Generate content brief via Claude
    print("  Generating content brief (Claude)...")
    brief = generate_content_brief(transcript, url, classification, notes, research=research)

    # 3b. Translate to PT-BR via Claude Haiku (same pattern as build_render_props.py)
    print("  Translating brief to PT-BR (Claude Haiku)...")
    brief_pt = translate_to_pt(brief)

    # 4. Create empty Google Doc then write content via Docs API batchUpdate
    doc_url = ""
    try:
        doc = drive.files().create(
            body={"name": f"[CONTENT BRIEF] {title}",
                  "mimeType": "application/vnd.google-apps.document",
                  "parents": [folder_id]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        doc_id = doc["id"]
        doc_url = doc.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")
        docs = get_docs_service()
        if docs and brief:
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": brief}}]}
            ).execute()
        print(f"  Content brief doc (EN): {doc_url}")
    except Exception as e:
        print(f"  WARNING doc creation: {e}")

    # 4b. Create PT-BR content brief doc in same folder
    doc_url_pt = ""
    try:
        doc_pt = drive.files().create(
            body={"name": f"[CONTENT BRIEF PT] {title}",
                  "mimeType": "application/vnd.google-apps.document",
                  "parents": [folder_id]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        doc_pt_id = doc_pt["id"]
        doc_url_pt = doc_pt.get("webViewLink", f"https://docs.google.com/document/d/{doc_pt_id}/edit")
        docs_pt = get_docs_service()
        if docs_pt and brief_pt:
            docs_pt.documents().batchUpdate(
                documentId=doc_pt_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": brief_pt}}]}
            ).execute()
        print(f"  Content brief doc (PT): {doc_url_pt}")
    except Exception as e:
        print(f"  WARNING PT doc creation: {e}")

    # 5. Log to Ideas Queue tab in Content Queue spreadsheet
    gc = get_sheets_client()
    if gc:
        try:
            sh = gc.open_by_key(CONTENT_QUEUE_ID)
            queue = sh.worksheet("\U0001f4a1 Ideas Queue")
            queue.append_row([
                title,
                classification.get("content_type", "Carousel"),
                "Instagram",
                classification.get("hook", ""),
                "DRAFT \u2014 text needed",
                "HIGH",
                f"Drive: {folder_url} | Brief EN: {doc_url} | Brief PT: {doc_url_pt} | Captured: {datetime.now().strftime('%Y-%m-%d')}",
                url,
            ])
            print("  Ideas Queue: row added")
        except Exception as e:
            print(f"  WARNING Ideas Queue: {e}")

    return folder_url, doc_url


def run_opc(args, transcript, video_path: str = "", metadata: dict = None, srt_content: str = ""):
    print("\n[OPC] Running classification...")
    cl = analyze_opc(transcript, args.url, args.notes or "")
    sid = args.story_id or f"CNT-{datetime.now().strftime('%Y%m%d%H%M')}"

    # Research notes before writing the brief — facts land IN the doc, not lost
    print("  Researching user notes before brief generation...")
    opc_research = research_from_notes(args.notes or "", transcript, cl.get("niche", "OPC"), sid)
    if opc_research.get("manual_tasks"):
        _write_manual_tasks_to_inbox(opc_research["manual_tasks"], sid, args.url)

    # News niche → route to Big Crazy Ideas > News with shared/english/portuguese structure
    # Other niches (Oak Park, Brazil content, UGC) → standard Content Hub + Content Creation flow
    if cl.get("niche", "").lower() == "news":
        hub_url, doc_url = save_to_news_folder(sid, args.url, transcript, cl,
                                                 video_path=video_path, notes=args.notes or "",
                                                 research=opc_research, project=args.project)
        folder_url = hub_url  # Same folder contains both archive (_shared) and production (english/portuguese)
    else:
        # Save raw transcript + resources + video to Content Hub (permanent home)
        hub_url = save_to_content_hub(sid, args.url, transcript, cl, video_path=video_path, notes=args.notes or "", project=args.project)
        # Create Drive workspace: folder + Art/Caption/Reel subfolders + content brief doc + Ideas Queue row
        title = (cl.get("summary") or sid)[:60].strip()
        folder_url, doc_url = create_content_workspace(sid, title, transcript, cl, args.url, args.notes or "",
                                                        research=opc_research)

    # Log to Inspiration Library WITH Drive links (must come after hub + workspace created)
    # Pass user_notes so Priscila's verbatim /capture ARGUMENTS text lands in the 'My Raw Notes' column
    # as a permanent safety net — survives even if I (Claude) forget to merge into the brief.
    update_inspiration_library(args.url, transcript, cl, hub_url=hub_url, doc_url=doc_url,
                                metadata=metadata, user_notes=args.notes or "")
    for _extra_niche in cl.get("additional_niches", []):
        if _extra_niche and _extra_niche.lower() != cl.get("niche", "").lower():
            _extra_cl = dict(cl); _extra_cl["niche"] = _extra_niche
            _extra_notes = f"[CROSS-NICHE — also for {_extra_niche}] " + (args.notes or "")
            update_inspiration_library(args.url, transcript, _extra_cl, hub_url=hub_url, doc_url=doc_url,
                                       metadata=metadata, user_notes=_extra_notes.strip())

    create_calendar_task(sid, args.project, args.url, doc_url or "", transcript[:400], args.notes or "", hub_url=hub_url)
    # Auto-trigger Topic Cluster Scraper for Brazil captures
    if cl.get("niche") == "Brazil" and os.getenv("APIFY_API_KEY"):
        _trigger_topic_scraper(cl)

    niche = cl.get("niche", "")
    summary = cl.get("summary") or sid

    # Save SRT captions alongside transcript (ported from run_news — useful for Reels editing)
    if srt_content:
        srt_path = TRANSCRIPTS_DIR / f"{sid}_captions.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        print(f"  SRT saved: {srt_path}")
        # Upload SRT to Content Hub folder if we have a hub_url and drive access
        try:
            drive_svc = get_drive_service()
            if drive_svc and hub_url:
                # Extract folder ID from hub_url to upload SRT there
                m = re.search(r'/folders/([a-zA-Z0-9_-]+)', hub_url)
                if m:
                    from googleapiclient.http import MediaInMemoryUpload
                    srt_media = MediaInMemoryUpload(srt_content.encode("utf-8"), mimetype="text/plain")
                    drive_svc.files().create(
                        body={"name": f"{sid}_captions.srt", "parents": [m.group(1)]},
                        media_body=srt_media, supportsAllDrives=True, fields="id"
                    ).execute()
                    print(f"  SRT uploaded to Content Hub")
        except Exception as e:
            print(f"  WARNING: SRT upload failed (non-fatal): {e}")

    print(f"\n{'='*50}\nOPC CAPTURE DONE\nNiche: {niche}\nType: {cl.get('content_type')}\nStatus: {cl.get('classification')}\nFolder: {folder_url or 'check artifacts'}\nBrief: {doc_url or 'check artifacts'}\n{'='*50}")
    score_map = {"READY": 5, "NEEDS_REVIEW": 3, "NOT_RELEVANT": 1}
    try:
        import sys; sys.path.insert(0, str(Path(__file__).parent.parent))
        from content_tracker import log_run
        log_run(pipeline="capture_pipeline", trigger="manual", url=args.url,
                niche=niche, project="opc", status="success",
                score=score_map.get(cl.get("classification", ""), 3),
                drive_path=hub_url or folder_url or "",
                brief_url=doc_url or "", notes=summary[:100])
    except Exception: pass

    # UX Fix: send completion email so Priscila knows it worked
    if video_path:
        video_note = "Video: uploaded to Content Hub"
        video_retry_note = ""
    else:
        video_note = "Video: download failed (transcript still captured)"
        video_retry_note = (
            f"\nTo retry video only: trigger capture_pipeline.yml with this URL:\n"
            f"  {args.url}\n"
            f"  https://github.com/priihigashi/oak-park-ai-hub/actions/workflows/capture_pipeline.yml\n"
        )
    send_notification_email(
        subject=f"OPC capture done — {niche} | {summary[:50]}",
        body=(
            f"Content Hub: {hub_url or 'check Drive'}\n"
            f"Content Brief: {doc_url or 'check artifacts'}\n"
            f"Production Folder: {folder_url or 'check Drive'}\n"
            f"{video_note}\n"
            f"{video_retry_note}"
            f"SRT captions: {'saved to Content Hub' if srt_content else 'not generated'}\n"
            f"Sheets: row added to Inspiration Library\n\n"
            f"Source: {args.url}\n"
            f"Niche: {niche}\n"
            f"Transcript preview:\n{transcript[:400]}"
        ),
    )
    _mark_queue_processed(args.url)


# ─── UGC / STOCKS / HIGASHI — thin wrappers around run_opc() ─────────────────
# Same pipeline, different niche label + story prefix + email subject.
# Folder routing uses env var from routing.py (UGC_FOLDER_ID / STOCKS_FOLDER_ID /
# HIGASHI_FOLDER_ID) — falls back to CONTENT_HUB_FOLDER_ID until those GitHub
# secrets are added.

def run_ugc(args, transcript, video_path: str = "", metadata: dict = None, srt_content: str = ""):
    args.project = "ugc"
    run_opc(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)


def run_stocks(args, transcript, video_path: str = "", metadata: dict = None, srt_content: str = ""):
    args.project = "stocks"
    run_opc(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)


def run_higashi(args, transcript, video_path: str = "", metadata: dict = None, srt_content: str = ""):
    args.project = "higashi"
    run_opc(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Capture Pipeline v2")
    parser.add_argument("url")
    parser.add_argument("--project",
                        choices=["book", "brazil", "usa", "opc", "ugc", "stocks", "higashi",
                                 # legacy aliases — normalized to canonical names below
                                 "news", "content"],
                        default="book",
                        help="book | brazil | usa | opc | ugc | stocks | higashi (routing.py is source of truth)")
    parser.add_argument("--story-id", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument("--credits", action="store_true",
                        help="Fetch creator info via Apify for caption attribution")
    args = parser.parse_args()

    # Normalize legacy project names → canonical names.
    # Brazil and USA are DISTINCT projects with DIFFERENT Drive folders (routing.py).
    # Both use the News pipeline flow (run_news) but keep project="brazil" or "usa"
    # so get_capture_folder(args.project) routes to the correct niche folder.
    _alias = {"sovereign": "brazil", "content": "opc", "news": "brazil"}
    args.project = _alias.get(args.project, args.project)

    if not args.story_id:
        _prefixes = {"book": "BCI", "brazil": "NWS", "usa": "NWS", "opc": "CNT",
                     "ugc": "UGC", "stocks": "STK", "higashi": "HIG"}
        prefix = _prefixes.get(args.project, "CNT")
        args.story_id = f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M')}"

    print(f"\n{'='*50}\nCAPTURE PIPELINE v2\nURL: {args.url}\nProject: {args.project.upper()}\nStory ID: {args.story_id}\n{'='*50}")

    # Step 0: Fetch metadata — YouTube Data API for YouTube, Apify for Instagram
    metadata = {}
    is_ig = "instagram.com" in args.url
    is_yt = _is_youtube(args.url)
    if is_yt:
        # YouTube: use official Data API (no yt-dlp, no IP blocking)
        metadata = _fetch_youtube_metadata_via_api(args.url)
    elif args.credits or is_ig:
        # Instagram/TikTok: use Apify for creator info + videoUrl fallback
        metadata = fetch_reel_metadata(args.url)
        if metadata and args.credits:
            args.notes = (args.notes or "") + (
                f"\n\nCREDITS — Original creator: @{metadata['creator_handle']}"
                f" ({metadata['creator_name']})"
                f"\nOriginal caption: {metadata['caption'][:200]}"
                f"\nSource: {metadata['source_url']}"
            )

    with tempfile.TemporaryDirectory() as tmp:
        audio = download_audio(args.url, tmp, metadata=metadata)
        transcript = transcribe_audio(audio, args.url)
        save_transcript(transcript, args.url, args.story_id, args.project)

        # Download video file for Content Hub (non-fatal — transcript is the priority)
        # Skip for YouTube transcript-only path — GitHub runner IPs are blocked by YouTube,
        # so download always fails and just produces a spurious failure notification.
        if audio == "__youtube_transcript_fallback__":
            video_path = ""
        else:
            video_path = download_video(args.url, tmp)

        # Cookie health check — only relevant when yt-dlp is used for YouTube.
        # When YOUTUBE_API_KEY is set we skip yt-dlp entirely, so no cookie alerts needed.
        if is_yt and not YOUTUBE_API_KEY:
            if _YT_COOKIE_FAILURE:
                _yt_cookie_alert(resolved=False)
            elif video_path:
                _yt_cookie_alert(resolved=True)

        srt_content = get_caption_srt(audio) if audio and not _is_youtube(args.url) else ""
        if args.project == "book":
            run_book(args, transcript)
        elif args.project in ("brazil", "usa"):
            # Both niches share the News pipeline flow but land in separate Drive folders
            # (routing.py::capture_folder returns the correct Brazil or USA folder).
            srt_content = get_caption_srt(audio) if audio else ""
            run_news(args, transcript, video_path=video_path or "", srt_content=srt_content, creator_name=metadata.get("creator_name", ""))
        elif args.project == "ugc":
            run_ugc(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)
        elif args.project == "stocks":
            run_stocks(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)
        elif args.project == "higashi":
            run_higashi(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)
        else:  # opc (default)
            run_opc(args, transcript, video_path=video_path, metadata=metadata, srt_content=srt_content)

    # Print credits summary if available
    if metadata:
        print(f"\n{'='*50}")
        print("CREDITS FOR CAPTION:")
        print(f"  Creator: @{metadata['creator_handle']}")
        print(f"  Name: {metadata['creator_name']}")
        print(f"  Source: {metadata['source_url']}")
        print(f"{'='*50}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"\nFATAL ERROR:\n{tb}")
        send_notification_email(
            subject="CAPTURE FAILED — check GitHub Actions",
            body=f"Pipeline crashed.\n\nError: {exc}\n\nTraceback:\n{tb}\n\nArgs: {' '.join(sys.argv[1:])}",
        )
        sys.exit(1)
