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
  5. Routes based on --project:
     book      → Claude fact-checks → story doc in The Book Drive folder
                → Book Tracker Stories tab → Calendar task
     sovereign → Claude analyses   → study doc in SOVEREIGN Drive folder
                → Calendar task
     content   → Claude classifies niche → Inspiration Library tab
                → Calendar task

CREDITS / ATTRIBUTION:
  When --credits flag is set, the pipeline fetches the original creator's info
  via Apify and includes it in the output so captions can give proper credit.
  Fields saved: creator handle, creator name, original caption, source URL.

REQUIRED ENV VARS (all stored as GitHub Secrets in oak-park-ai-hub):
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  GOOGLE_SA_KEY   (base64-encoded service account JSON — same secret used by other workflows)
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

# ─── CONFIG ───────────────────────────────────────────────────────────────────

OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
# FYI: Apify API is used to fetch reel metadata (creator, caption, stats).
# Key stored in GitHub Secrets as APIFY_API_KEY.
# Get yours at: https://console.apify.com/account/integrations
APIFY_API_KEY      = os.getenv("APIFY_API_KEY", "")

# Spreadsheet IDs — hardcoded as defaults, can override via env
BOOK_TRACKER_ID    = os.getenv("BOOK_TRACKER_ID",    "1SeDFDisb0uNeyfyv5fCS_0x5EbkJRcFeS6CGuUmlH7c")
IDEAS_INBOX_ID     = os.getenv("IDEAS_INBOX_ID",     "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")

# Drive folder IDs — hardcoded as defaults
BOOK_FOLDER_ID              = "1HlY1tmUHmRZ_ZfPUzGpY_j7sHbe_OCz1"
SOVEREIGN_FOLDER_ID         = "1L89dLiVYfjNu3uz3l3S_rvZPxd2I8xjZ"
CONTENT_CREATION_FOLDER_ID = "1um7y2Yt8zi9KGxev6kfFJYgrkMYwrCNh"  # Drive > Marketing > Claude Code Workspace > Content Creation
CONTENT_HUB_FOLDER_ID     = "1p7s2Q7kCxzKdvaVRFxSoYAQ-IG_NhTqq"  # Drive > Marketing > Claude Code Workspace > Content Hub (transcripts + resources + video)

# Spreadsheet IDs for content pipeline
CONTENT_QUEUE_ID = "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg"  # Ideas Queue tab

GMAIL_FROM     = "priscila@oakpark-construction.com"
GMAIL_PASSWORD = os.getenv("PRI_OP_GMAIL_APP_PASSWORD", "")

TRANSCRIPTS_DIR = Path("transcripts")
TRANSCRIPTS_DIR.mkdir(exist_ok=True)


# ─── GOOGLE AUTH ──────────────────────────────────────────────────────────────

def _get_creds(scopes: list):
    """Return Google credentials. Uses GOOGLE_SA_KEY env var (base64 JSON)."""
    from google.oauth2.service_account import Credentials

    # oak-park-ai-hub uses GOOGLE_SA_KEY (base64 encoded)
    sa_b64 = os.getenv("GOOGLE_SA_KEY")
    if sa_b64:
        # Add == padding before decode — GitHub Secrets strips trailing = chars + whitespace
        sa_info = json.loads(base64.b64decode(sa_b64.strip() + "=="))
        return Credentials.from_service_account_info(sa_info, scopes=scopes)

    # Fallback: local file
    creds_path = Path("credentials/service_account.json")
    if creds_path.exists():
        return Credentials.from_service_account_file(str(creds_path), scopes=scopes)

    raise RuntimeError("No Google credentials. Set GOOGLE_SA_KEY secret.")


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

def fetch_reel_metadata(url: str) -> dict:
    """Fetch reel metadata via Apify. Returns dict with creator info + stats.

    FYI: Uses apify/instagram-scraper actor with directUrls.
    Non-fatal — returns empty dict if Apify unavailable or fails.
    """
    if not APIFY_API_KEY:
        print("  SKIP Apify metadata: APIFY_API_KEY not set")
        print("  (Get key at: https://console.apify.com/account/integrations)")
        return {}

    if "instagram.com" not in url:
        print("  SKIP Apify metadata: not an Instagram URL")
        return {}

    print(f"\n[0/3] Fetching reel metadata via Apify...")
    actor_id = "apify/instagram-scraper"
    input_data = {
        "directUrls": [url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }

    try:
        run_resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            params={"token": APIFY_API_KEY},
            json=input_data,
            timeout=30,
        )
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
            print(f"  WARNING: Apify run ended with status: {status}")
            return {}

        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
            timeout=30,
        )
        items = items_resp.json()
        if not items:
            print("  WARNING: Apify returned no results")
            return {}

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


# ─── STEP 1: DOWNLOAD ─────────────────────────────────────────────────────────

def _find_audio_file(tmp_dir: str) -> str:
    """Find the downloaded audio file in tmp_dir regardless of extension."""
    for ext in ["mp3", "m4a", "webm", "ogg", "wav", "opus"]:
        path = os.path.join(tmp_dir, f"audio.{ext}")
        if os.path.exists(path):
            return path
    return ""


def _try_ytdlp(url: str, tmp_dir: str, extra_args: list = None) -> str:
    """Try yt-dlp download with optional extra args. Returns audio path or empty string."""
    output = os.path.join(tmp_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "0", "--output", output,
        "--no-playlist", "--quiet",
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return _find_audio_file(tmp_dir)
    print(f"  yt-dlp failed: {result.stderr[:200]}")
    return ""


def _try_apify_youtube_download(url: str, tmp_dir: str) -> str:
    """Download YouTube audio via Apify actor. Returns audio path or empty string.
    Uses bernardo/youtube-scraper actor which can extract audio URLs.
    Falls back to streamers/youtube-scraper for direct download link.
    """
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


def download_audio(url: str, tmp_dir: str) -> str:
    """3-tier YouTube download: yt-dlp → Apify → transcript-api fallback.
    For non-YouTube URLs, uses yt-dlp only (works for IG/TikTok).
    """
    print(f"\n[1/3] Downloading audio: {url}")
    is_yt = _is_youtube(url)

    # Tier 1: yt-dlp standard (works for IG, TikTok, and sometimes YouTube)
    audio = _try_ytdlp(url, tmp_dir)
    if audio:
        size = os.path.getsize(audio) / 1024
        print(f"  Downloaded via yt-dlp ({size:.0f} KB)")
        return audio

    # Tier 1b: yt-dlp with iOS client trick (YouTube only — bypasses some bot checks)
    if is_yt:
        print("  Retrying yt-dlp with iOS client workaround...")
        audio = _try_ytdlp(url, tmp_dir, [
            "--extractor-args", "youtube:player_client=ios,web_creator",
        ])
        if audio:
            size = os.path.getsize(audio) / 1024
            print(f"  Downloaded via yt-dlp iOS trick ({size:.0f} KB)")
            return audio

    # Tier 2: Apify YouTube download (cloud, reliable, costs ~$0.05)
    if is_yt:
        audio = _try_apify_youtube_download(url, tmp_dir)
        if audio:
            return audio

    # Tier 3: transcript-api fallback (text only, no audio file)
    if is_yt:
        print("  All download methods failed — falling back to transcript API (text only)")
        return "__youtube_transcript_fallback__"

    # Non-YouTube URL and yt-dlp failed — nothing else to try
    print("  ERROR: yt-dlp failed and no fallback available for this platform")
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
        # IG/TikTok: download best quality (reels are short)
        cmd = [
            "yt-dlp",
            "-f", "best",
            "--merge-output-format", "mp4",
            "--output", output,
            "--no-playlist", "--quiet",
        ]

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
        print(f"  Video download failed (non-fatal): {result.stderr[:200]}")
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
            print(f"  ERROR youtube-transcript-api: {e}")
            sys.exit(1)

    if not OPENAI_API_KEY:
        print("  ERROR: OPENAI_API_KEY not set")
        sys.exit(1)
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="text"
        )
    print(f"  Transcribed via Whisper ({len(result)} chars)")
    return result


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

def analyze_book(transcript: str, url: str, story_id: str, notes: str) -> str:
    if not ANTHROPIC_API_KEY:
        return f"[PENDING — ANTHROPIC_API_KEY required]\n\n{transcript}"
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
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

SOVEREIGN POST ANGLE:
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


def analyze_sovereign(transcript: str, url: str, story_id: str, notes: str) -> str:
    if not ANTHROPIC_API_KEY:
        return f"[PENDING — ANTHROPIC_API_KEY required]\n\n{transcript}"
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("  Claude (claude-opus-4-6) SOVEREIGN analysis...")
    prompt = f"""Analyze this content for the SOVEREIGN political inspiration page.
Study the format and identify how to do it better — more examples, more teaching, not just negatives.

Story ID: {story_id}
Source URL: {url}
Notes: {notes or "None"}

TRANSCRIPT:
{transcript}

Produce SOVEREIGN CAPTURE DOCUMENT (no markdown tables):

STORY ID: {story_id}
PROJECT: SOVEREIGN
DATE: {datetime.now().strftime("%Y-%m-%d")}
SOURCE URL: {url}

SPEAKER ANALYSIS:
  Who: [name, title, platform/following]
  Credibility: HIGH / MEDIUM / LOW / UNVERIFIED
  Red flags: [vague? no sources? only negatives?]

CONTENT ANALYSIS:
  Main message: [one sentence]
  Emotional tone: [anger / fear / inspiration / outrage]
  What works: [specific format strengths]
  What's missing: [e.g. no examples, only complaints, no solutions]

SOVEREIGN POST ANGLE:
  Hook: [opening line that stops the scroll]
  Core message: [what SOVEREIGN says differently — with concrete examples]
  Teaching moment: [what audience learns and can apply]
  Format: [talking head / carousel / before-after / text overlay]
  CTA: [what action we want]

PORTUGUESE ANGLE:
  Relevant to Brazilian audience: YES / NO
  PT-BR hook: [if YES]

STUDY NOTES (3 specific ways to do it better):
  1. [Improvement]
  2. [Improvement]
  3. [Improvement]

CONTENT READY: YES / NO / NEEDS REFINEMENT"""
    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def analyze_content(transcript: str, url: str, notes: str) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"niche": "Oak Park", "classification": "NEEDS_REVIEW", "summary": transcript[:150]}
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("  Claude (claude-sonnet-4-6) classifying...")
    prompt = f"""Classify this video transcript for Oak Park Construction content pipeline.
URL: {url}
Notes: {notes or "None"}
TRANSCRIPT: {transcript}

Respond with JSON only:
{{"niche": "Oak Park" or "Brazil" or "UGC" or "News", "content_type": "Talking Head/Expert" or "Project Progress/Before-After" or "Product Tips" or "Other", "classification": "READY" or "NEEDS_REVIEW" or "NOT_RELEVANT", "summary": "one sentence", "hook": "suggested hook for Oak Park repost", "notes": "why classified this way"}}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=400,
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


def update_inspiration_library(url, transcript, classification, hub_url="", doc_url=""):
    gc = get_sheets_client()
    if not gc:
        return
    try:
        sh = gc.open_by_key(IDEAS_INBOX_ID)
        lib = sh.worksheet("📥 Inspiration Library")
        lib.append_row([
            datetime.now().strftime("%Y-%m-%d"), url,
            classification.get("summary", ""),
            classification.get("niche", "Oak Park"),
            classification.get("content_type", ""),
            classification.get("classification", "NEEDS_REVIEW"),
            transcript[:300],
            classification.get("hook", ""),
            classification.get("notes", ""),
            "",            # J — Hook Type (header-aligned placeholder)
            "",            # K — Views (header-aligned placeholder)
            hub_url,       # L — Content Hub folder link
            doc_url,       # M — Content Brief doc link
        ])
        print("  Inspiration Library updated")
    except Exception as e:
        print(f"  WARNING Sheets: {e}")


# ─── CALENDAR ─────────────────────────────────────────────────────────────────

def create_calendar_task(story_id, project, url, doc_url, preview, notes, hub_url=""):
    cal = get_calendar_service()
    if not cal:
        return
    labels = {"book": "BOOK CAPTURE", "sovereign": "SOVEREIGN CAPTURE", "content": "CONTENT CAPTURE"}
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


# ─── PIPELINES ────────────────────────────────────────────────────────────────

def run_book(args, transcript):
    print("\n[BOOK] Running fact-check pipeline...")
    analysis = analyze_book(transcript, args.url, args.story_id, args.notes or "")
    path = TRANSCRIPTS_DIR / f"{args.story_id}_analysis.txt"
    path.write_text(analysis, encoding="utf-8")
    print(f"  Analysis saved: {path}")
    doc_title = f"{args.story_id} — {datetime.now().strftime('%Y-%m-%d')}"
    doc_url = create_drive_doc(doc_title, analysis, BOOK_FOLDER_ID)
    update_book_tracker(args.story_id, args.url, doc_url, analysis, args.notes or "")
    create_calendar_task(args.story_id, args.project, args.url, doc_url, transcript[:400], args.notes or "")
    print(f"\n{'='*50}\nBOOK CAPTURE DONE\nStory ID: {args.story_id}\nDoc: {doc_url or 'check artifacts'}\n{'='*50}")


def run_sovereign(args, transcript):
    print("\n[SOVEREIGN] Running format analysis...")
    analysis = analyze_sovereign(transcript, args.url, args.story_id, args.notes or "")
    path = TRANSCRIPTS_DIR / f"{args.story_id}_sovereign.txt"
    path.write_text(analysis, encoding="utf-8")
    doc_url = create_drive_doc(f"{args.story_id} — SOVEREIGN — {datetime.now().strftime('%Y-%m-%d')}", analysis, SOVEREIGN_FOLDER_ID)
    create_calendar_task(args.story_id, args.project, args.url, doc_url, transcript[:400], args.notes or "")
    print(f"\n{'='*50}\nSOVEREIGN CAPTURE DONE\nStory ID: {args.story_id}\nDoc: {doc_url or 'check artifacts'}\n{'='*50}")


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

def generate_content_brief(transcript: str, url: str, classification: dict, notes: str) -> str:
    """Ask Claude to generate carousel + reel + topic breakdowns from transcript.
    Returns plain text content brief (no markdown tables — avoids Docs API 400 errors).
    Falls back to transcript + classification JSON if ANTHROPIC_API_KEY not set.
    """
    if not ANTHROPIC_API_KEY:
        return f"SOURCE: {url}\nNOTES: {notes or 'None'}\n\nTRANSCRIPT:\n{transcript}\n\nClassification:\n{json.dumps(classification, indent=2)}"
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    niche = classification.get("niche", "General")
    prompt = f"""You are a bilingual content creator (EN + PT-BR). Analyze this transcript and produce a CONTENT BRIEF.

Source URL: {url}
Niche: {niche}
Notes: {notes or 'None'}

TRANSCRIPT:
{transcript}

Output plain text only — NO markdown tables. Use this structure:

CONTENT BRIEF
Date: {datetime.now().strftime('%Y-%m-%d')}
Source: {url}
Niche: {niche}
Status: DRAFT

KEY FACTS (list the 5-8 most important verifiable claims from the transcript with sources if mentioned):

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

SOURCES (list from transcript or verified):
1.
2.
3.

STATUS: DRAFT — text ready, art needed"""

    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def save_to_content_hub(story_id: str, url: str, transcript: str, classification: dict, video_path: str = "") -> str:
    """Save transcript + resources + video to Content Hub story folder. Returns folder URL."""
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
                  "parents": [CONTENT_HUB_FOLDER_ID]},
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


def create_content_workspace(story_id: str, title: str, transcript: str,
                              classification: dict, url: str, notes: str = "") -> tuple:
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
    brief = generate_content_brief(transcript, url, classification, notes)

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
        print(f"  Content brief doc: {doc_url}")
    except Exception as e:
        print(f"  WARNING doc creation: {e}")

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
                f"Drive: {folder_url} | Brief: {doc_url} | Captured: {datetime.now().strftime('%Y-%m-%d')}",
                url,
            ])
            print("  Ideas Queue: row added")
        except Exception as e:
            print(f"  WARNING Ideas Queue: {e}")

    return folder_url, doc_url


def run_content(args, transcript, video_path: str = ""):
    print("\n[CONTENT] Running classification...")
    cl = analyze_content(transcript, args.url, args.notes or "")
    sid = args.story_id or f"CNT-{datetime.now().strftime('%Y%m%d%H%M')}"

    # Save raw transcript + resources + video to Content Hub (permanent home)
    hub_url = save_to_content_hub(sid, args.url, transcript, cl, video_path=video_path)

    # Create Drive workspace: folder + Art/Caption/Reel subfolders + content brief doc + Ideas Queue row
    title = (cl.get("summary") or sid)[:60].strip()
    folder_url, doc_url = create_content_workspace(sid, title, transcript, cl, args.url, args.notes or "")

    # Log to Inspiration Library WITH Drive links (must come after hub + workspace created)
    update_inspiration_library(args.url, transcript, cl, hub_url=hub_url, doc_url=doc_url)

    create_calendar_task(sid, args.project, args.url, doc_url or "", transcript[:400], args.notes or "", hub_url=hub_url)
    # Auto-trigger Topic Cluster Scraper for Brazil captures
    if cl.get("niche") == "Brazil" and os.getenv("APIFY_API_KEY"):
        _trigger_topic_scraper(cl)

    niche = cl.get("niche", "")
    summary = cl.get("summary", title)
    print(f"\n{'='*50}\nCONTENT CAPTURE DONE\nNiche: {niche}\nType: {cl.get('content_type')}\nStatus: {cl.get('classification')}\nFolder: {folder_url or 'check artifacts'}\nBrief: {doc_url or 'check artifacts'}\n{'='*50}")

    # UX Fix: send completion email so Priscila knows it worked
    video_note = "Video: uploaded to Content Hub" if video_path else "Video: download failed (transcript still captured)"
    send_notification_email(
        subject=f"Capture done — {niche} | {summary[:50]}",
        body=(
            f"Content Hub: {hub_url or 'check Drive'}\n"
            f"Content Brief: {doc_url or 'check artifacts'}\n"
            f"Production Folder: {folder_url or 'check Drive'}\n"
            f"{video_note}\n"
            f"Sheets: row added to Inspiration Library\n\n"
            f"Source: {args.url}\n"
            f"Niche: {niche}\n"
            f"Transcript preview:\n{transcript[:400]}"
        ),
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Capture Pipeline v2")
    parser.add_argument("url")
    parser.add_argument("--project", choices=["book", "sovereign", "content"], default="book")
    parser.add_argument("--story-id", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument("--credits", action="store_true",
                        help="Fetch creator info via Apify for caption attribution")
    args = parser.parse_args()

    if not args.story_id:
        prefix = {"book": "BCI", "sovereign": "SVG", "content": "CNT"}[args.project]
        args.story_id = f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M')}"

    print(f"\n{'='*50}\nCAPTURE PIPELINE v2\nURL: {args.url}\nProject: {args.project.upper()}\nStory ID: {args.story_id}\n{'='*50}")

    # Step 0: Fetch reel metadata via Apify (creator info for credits)
    # FYI: This uses the Apify API — see docstring at top of file.
    metadata = {}
    if args.credits:
        metadata = fetch_reel_metadata(args.url)
        if metadata:
            args.notes = (args.notes or "") + (
                f"\n\nCREDITS — Original creator: @{metadata['creator_handle']}"
                f" ({metadata['creator_name']})"
                f"\nOriginal caption: {metadata['caption'][:200]}"
                f"\nSource: {metadata['source_url']}"
            )

    with tempfile.TemporaryDirectory() as tmp:
        audio = download_audio(args.url, tmp)
        transcript = transcribe_audio(audio, args.url)
        save_transcript(transcript, args.url, args.story_id, args.project)

        # Download video file for Content Hub (non-fatal — transcript is the priority)
        video_path = download_video(args.url, tmp)

        if args.project == "book":
            run_book(args, transcript)
        elif args.project == "sovereign":
            run_sovereign(args, transcript)
        else:
            run_content(args, transcript, video_path=video_path)

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
