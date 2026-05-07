#!/usr/bin/env python3
"""
youtube_research.py — General-purpose YouTube/Shorts/Reels research agent

Searches for recent videos on any topic, pulls transcripts (no download needed),
Claude analyzes each for technique, tools, quality, key takeaways.

Flow: search 5 → analyze → expand keywords → search 5 more → expand → search 5 more = 15 total
Saves to:
  - Drive: Resources/Video Creation Flow/<topic>/ → raw transcripts + master findings doc
  - Sheet: Ideas & Inbox → 📥 Inspiration Library tab (one row per video)

Usage (local):
  python youtube_research.py --topic "kling ai talking head" --queries "kling ai tutorial 2025,kling 3.0 video" --max 5

GitHub Action: trigger via video-research.yml with workflow_dispatch
"""

import os
import sys
import json
import re
import argparse
import subprocess
from datetime import datetime

try:
    import anthropic
except ImportError:
    os.system("pip install anthropic -q")
    import anthropic

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    os.system("pip install youtube-transcript-api -q")
    from youtube_transcript_api import YouTubeTranscriptApi

try:
    import yt_dlp
except ImportError:
    os.system("pip install yt-dlp -q")
    import yt_dlp

try:
    import gspread
    from google.oauth2 import service_account
except ImportError:
    os.system("pip install gspread google-auth -q")
    import gspread
    from google.oauth2 import service_account

import urllib.request
import urllib.parse
import time

# ── CONFIG ────────────────────────────────────────────────────────────────────
CLAUDE_KEY_4_CONTENT = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
GOOGLE_SA_KEY     = os.environ.get("GOOGLE_SA_KEY", "")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
APIFY_API_KEY     = os.environ.get("APIFY_API_KEY", "")
APIFY_BASE        = "https://api.apify.com/v2"
_apify_limit_hit  = False
SHEET_ID          = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
DRIVE_FOLDER_ID   = "1-QRf4xToJf_7cnS5UW7BiDUjd6lXot6o"  # Resources/Video Creation Flow
INSP_TAB          = "📥 Inspiration Library"
CLIP_COLLECTIONS_TAB = "📋 Clip Collections"
TARGET_VIDEOS     = 15
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Failure accumulator — populated by log_pipeline_failure(). Non-empty => script exits 1.
PIPELINE_FAILURES = []
GHA_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

# ── OAUTH TOKEN REFRESH ───────────────────────────────────────────────────────
# Same pattern as content_creator/main.py and capture_pipeline.py.
# SHEETS_TOKEN is a JSON refresh-token blob, not a raw access token.
import time
_token_cache = {}
def get_oauth_token():
    if _token_cache.get("t") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["t"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        return ""
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["t"] = resp["access_token"]
    _token_cache["exp"] = time.time() + resp.get("expires_in", 3500) - 60
    return resp["access_token"]

# ── YOUTUBE SEARCH ────────────────────────────────────────────────────────────
def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """Use yt-dlp to search YouTube, return list of {url, title, id, duration}"""
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "default_search": f"ytsearch{max_results}",
        "match_filter": yt_dlp.utils.match_filter_func("duration < 900"),  # max 15min
    }
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            for entry in info.get("entries", []):
                if entry:
                    results.append({
                        "id": entry.get("id", ""),
                        "title": entry.get("title", ""),
                        "url": f"https://youtube.com/watch?v={entry.get('id','')}",
                        "duration": entry.get("duration", 0),
                        "uploader": entry.get("uploader", ""),
                        "upload_date": entry.get("upload_date", ""),
                    })
        except Exception as e:
            print(f"  Search error for '{query}': {e}")
    return results

# ── TRANSCRIPT ────────────────────────────────────────────────────────────────
_YT_COOKIES_FILE = ""

def _write_yt_cookies_file() -> str:
    """Write PRI_OP_YT_COOKIES secret to a Netscape cookies file. Returns path or ''."""
    raw = os.environ.get("PRI_OP_YT_COOKIES", "")
    if not raw.strip():
        return ""
    import tempfile as _tmp
    fd, path = _tmp.mkstemp(suffix=".txt", prefix="ytcookies_")
    with os.fdopen(fd, "w") as f:
        f.write(raw)
    return path


def _ytdlp_whisper_fallback(video_id: str, extra_args: list = None) -> str:
    """Tier 2/3: yt-dlp (with PRI_OP_YT_COOKIES) → OpenAI Whisper. Mirrors capture_pipeline.py.
    extra_args lets callers add things like --extractor-args youtube:player_client=ios."""
    global _YT_COOKIES_FILE
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return ""
    import tempfile as _tmp
    import subprocess as _sp
    if not _YT_COOKIES_FILE:
        _YT_COOKIES_FILE = _write_yt_cookies_file()
    url = f"https://www.youtube.com/watch?v={video_id}"
    with _tmp.TemporaryDirectory() as td:
        out_pattern = os.path.join(td, "audio.%(ext)s")
        cmd = [
            "yt-dlp", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            "--output", out_pattern, "--no-playlist", "--quiet", "--no-warnings",
        ]
        if _YT_COOKIES_FILE:
            cmd.extend(["--cookies", _YT_COOKIES_FILE])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)
        try:
            r = _sp.run(cmd, capture_output=True, text=True, timeout=180)
        except Exception as e:
            print(f"    yt-dlp error: {e}")
            return ""
        if r.returncode != 0:
            print(f"    yt-dlp failed: {r.stderr[:200].strip()}")
            return ""
        audio_path = ""
        for f in os.listdir(td):
            if f.endswith(".mp3"):
                audio_path = os.path.join(td, f)
                break
        if not audio_path:
            return ""
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            with open(audio_path, "rb") as af:
                resp = client.audio.transcriptions.create(
                    model="whisper-1", file=af, response_format="text"
                )
            text = resp if isinstance(resp, str) else getattr(resp, "text", "")
            print(f"    Whisper transcribed ({len(text)} chars)")
            return text
        except Exception as e:
            print(f"    Whisper failed: {e}")
            return ""


def _whisper_transcribe(audio_path: str) -> str:
    """Send mp3 to OpenAI Whisper. Returns text or ''."""
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key or not audio_path or not os.path.exists(audio_path):
        return ""
    try:
        import openai
        client = openai.OpenAI(api_key=openai_key)
        with open(audio_path, "rb") as af:
            resp = client.audio.transcriptions.create(
                model="whisper-1", file=af, response_format="text"
            )
        text = resp if isinstance(resp, str) else getattr(resp, "text", "")
        print(f"    Whisper transcribed ({len(text)} chars)")
        return text
    except Exception as e:
        print(f"    Whisper failed: {e}")
        return ""


def _try_apify_youtube_audio(video_id: str) -> str:
    """Tier 4: Apify YouTube actor → audio file → Whisper.
    Mirrors capture_pipeline.py::_try_apify_youtube_download.
    Routes via Apify proxy network so YouTube doesn't see the GHA runner IP.
    Returns transcript text or ''.
    """
    global _apify_limit_hit
    if _apify_limit_hit or not APIFY_API_KEY:
        if not APIFY_API_KEY:
            print("    SKIP Apify: APIFY_API_KEY not set")
        return ""

    print("    Trying Apify YouTube download...")
    # Actor swap 2026-05-07: bernardo~youtube-scraper returns HTTP 404 (does not
    # exist on Apify Store). Using streamers~youtube-scraper (public, accessible,
    # same startUrls payload shape).
    actor_id = "streamers~youtube-scraper"
    input_data = {
        "startUrls": [{"url": f"https://www.youtube.com/watch?v={video_id}"}],
        "maxResults": 1,
        "proxy": {"useApifyProxy": True},
    }
    try:
        run_resp = urllib.request.urlopen(
            urllib.request.Request(
                f"{APIFY_BASE}/acts/{actor_id}/runs?token={APIFY_API_KEY}",
                data=json.dumps(input_data).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=30,
        )
        run_id = json.loads(run_resp.read())["data"]["id"]
        print(f"    Apify run: {run_id}")

        # Poll up to ~3 min
        status = ""
        for _ in range(18):
            time.sleep(10)
            sresp = urllib.request.urlopen(
                f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_API_KEY}", timeout=15
            )
            status = json.loads(sresp.read())["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        if status != "SUCCEEDED":
            print(f"    Apify run ended: {status}")
            return ""

        items_resp = urllib.request.urlopen(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items?token={APIFY_API_KEY}&limit=1&format=json",
            timeout=30,
        )
        items = json.loads(items_resp.read())
        if not items:
            print("    Apify: no results")
            return ""
        item = items[0]
        media_url = (
            item.get("mediaUrl") or item.get("videoUrl")
            or item.get("audioUrl") or item.get("url")
        )
        if not media_url or "youtube.com" in str(media_url):
            print("    Apify: no direct media URL in result")
            return ""

        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as td:
            audio_path = os.path.join(td, "audio.mp3")
            print(f"    Downloading from Apify result...")
            with urllib.request.urlopen(media_url, timeout=120) as dl:
                with open(audio_path, "wb") as f:
                    f.write(dl.read())
            size_kb = os.path.getsize(audio_path) / 1024
            if size_kb < 5:
                print(f"    Apify: file too small ({size_kb:.0f} KB)")
                return ""
            print(f"    Apify download OK ({size_kb:.0f} KB)")
            return _whisper_transcribe(audio_path)
    except Exception as e:
        msg = str(e)
        if "limit" in msg.lower() and "403" in msg:
            _apify_limit_hit = True
            print(f"    Apify monthly limit hit — skipping for rest of run")
        else:
            print(f"    Apify download failed (non-fatal): {e}")
        return ""


def get_transcript(video_id: str) -> str:
    """Cascade — never give up on first failure:
      Tier 1: youtube-transcript-api (free, blocked on GHA Azure IP)
      Tier 2: yt-dlp + cookies + Whisper (cheap, breaks when cookies stale)
      Tier 3: yt-dlp iOS client trick + Whisper (sometimes bypasses bot detection)
      Tier 4: Apify YouTube actor + Whisper (paid, routes via Apify proxy — never blocked)
    """
    last_error = None
    # Tier 1
    for attempt, kwargs in enumerate([
        {"languages": ["en", "en-US", "en-GB", "pt", "es"]},
        {},
    ]):
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id, **kwargs)
            return " ".join(t.text for t in transcript)
        except Exception as e:
            last_error = e
            if attempt == 0:
                time.sleep(3)
    print(f"    Tier 1 blocked ({type(last_error).__name__}) — trying yt-dlp+Whisper...")

    # Tier 2: yt-dlp default
    fallback_text = _ytdlp_whisper_fallback(video_id)
    if fallback_text:
        return fallback_text

    # Tier 3: yt-dlp iOS client trick
    print(f"    Tier 2 failed — retrying yt-dlp with iOS client...")
    fallback_text = _ytdlp_whisper_fallback(
        video_id,
        extra_args=["--extractor-args", "youtube:player_client=ios,web_creator"],
    )
    if fallback_text:
        return fallback_text

    # Tier 4: Apify (paid, bypasses YouTube IP block)
    print(f"    Tier 3 failed — trying Apify YouTube...")
    fallback_text = _try_apify_youtube_audio(video_id)
    if fallback_text:
        return fallback_text

    print(f"    All 4 tiers exhausted")
    return f"[transcript unavailable: {last_error}]"

# ── CLAUDE ANALYSIS ───────────────────────────────────────────────────────────
def analyze_with_claude(video: dict, transcript: str, research_context: str) -> dict:
    """Claude analyzes a video — uses transcript if available, falls back to metadata only"""
    if not CLAUDE_KEY_4_CONTENT:
        return {"summary": "No API key", "watch_priority": "low", "relevance_score": 0}
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    
    has_transcript = transcript and "[transcript unavailable" not in transcript
    
    if has_transcript:
        content_block = f"TRANSCRIPT:\n{transcript[:4000]}"
        mode_note = "You have the full transcript to analyze."
    else:
        content_block = f"NOTE: Transcript unavailable. Analyze based on title, channel, and date only."
        mode_note = "No transcript — use title and channel to infer what this video likely covers."
    
    prompt = f"""You are analyzing a YouTube video for research on: {research_context}

Video: "{video['title']}" by {video.get('uploader', 'unknown')}
Published: {video.get('upload_date', 'unknown')}
URL: {video['url']}

{content_block}

{mode_note}

Extract and return JSON with:
{{
  "summary": "2-3 sentence summary of what this video shows/teaches (infer from title if no transcript)",
  "tools_used": ["list of AI tools, software, platforms mentioned or likely mentioned"],
  "technique": "specific technique or workflow demonstrated",
  "quality_assessment": "honest assessment — note if this is inferred from title only",
  "key_tips": ["up to 3 likely actionable tips based on title/topic"],
  "use_case": "what this is best for",
  "relevant_to_us": true/false,
  "relevance_reason": "why or why not relevant to Oak Park Construction / Hig Negocios",
  "watch_priority": "high / medium / low",
  "relevance_score": 5,
  "has_transcript": {str(has_transcript).lower()}
}}

relevance_score is 1-10. If no transcript, cap at 6 (needs manual verification).
Return only valid JSON, no markdown."""

    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as e:
        return {"summary": f"Analysis failed: {e}", "watch_priority": "low", "relevance_score": 0, "has_transcript": has_transcript}

# ── KEYWORD EXPANSION ─────────────────────────────────────────────────────────
def expand_keywords(topic: str, results_so_far: list, round_num: int) -> list[str]:
    """Ask Claude to generate 5 new search queries based on videos analyzed so far"""
    if not CLAUDE_KEY_4_CONTENT:
        return []
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    
    summaries = []
    for r in results_so_far[-10:]:
        score = r["analysis"].get("relevance_score", 0)
        summaries.append(f"- [{score}/10] {r['title']}: {r['analysis'].get('summary', '')[:150]}")
    
    prompt = f"""You are a YouTube research assistant expanding research on: {topic}
Round: {round_num} of 3. Target: 15 total videos.

Videos analyzed so far:
{chr(10).join(summaries)}

Generate 5 new YouTube search queries to find MORE useful videos.
- If high-scoring videos exist, go deeper on that specific angle
- If all scores are low, pivot to a different angle of the same topic
- Avoid queries that would return the same videos already found
- Each query should target a specific subtopic or technique

Return ONLY a JSON array of 5 query strings, nothing else:
["query 1", "query 2", "query 3", "query 4", "query 5"]"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if Haiku wrapped the JSON
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if not raw:
            raise ValueError(f"Haiku returned empty body (model=claude-haiku-4-5-20251001, round={round_num})")
        print(f"  [round {round_num}] raw response (first 200 chars): {raw[:200]}")
        return json.loads(raw)
    except Exception as e:
        print(f"  Keyword expansion failed: {e}")
        log_pipeline_failure(f"Round {round_num} keyword expansion", e)
        return []

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def get_sheet():
    if not GOOGLE_SA_KEY:
        return None
    try:
        creds_dict = json.loads(GOOGLE_SA_KEY)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        gc = gspread.authorize(creds)
        return gc.open_by_key(SHEET_ID)
    except Exception as e:
        print(f"  Sheet error: {e}")
        return None

def save_to_sheet(sheet, video: dict, analysis: dict, topic: str):
    """Write a video-research row using header-name lookup so column reorders
    on the Inspiration Library tab can never shift the data again."""
    try:
        ws = sheet.worksheet(INSP_TAB)
        headers = ws.row_values(1)
        col_pos = {h.strip().lower(): i for i, h in enumerate(headers)}
        width = (max(col_pos.values()) + 1) if col_pos else 29
        row = [""] * width

        def put(col_name, val):
            i = col_pos.get(col_name.lower())
            if i is not None and i < width:
                row[i] = "" if val is None else str(val)

        put("date added",        datetime.now().strftime("%Y-%m-%d %H:%M"))
        put("topic / title",     f"[VIDEO RESEARCH] {topic}")
        put("description",       video.get("title", ""))
        put("url",               video.get("url", ""))
        put("creator / account", video.get("uploader", ""))
        put("brief / angle",     analysis.get("summary", ""))
        put("what's working",    ", ".join(analysis.get("tools_used", [])))
        put("visual hook",       analysis.get("technique", ""))
        put("hook type",         analysis.get("quality_assessment", ""))
        put("ai score (1-5)",    analysis.get("watch_priority", ""))
        put("comments",          analysis.get("relevance_reason", ""))
        put("platform",          "YouTube")
        put("status",            "NEEDS_REVIEW")

        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"  Saved to sheet: {video['title'][:50]}")
    except Exception as e:
        print(f"  Sheet save error: {e}")

_CLIP_THRESHOLD = 8  # must match CLIP_THRESHOLD in content_creator/main.py


def _check_clip_threshold(ws, topic: str, niche: str) -> None:
    """Send a one-shot email when a topic's Clip Collections count first hits _CLIP_THRESHOLD.
    Fires only at exactly the threshold count to avoid repeat emails on subsequent runs."""
    try:
        all_rows = ws.get_all_values()
        if not all_rows:
            return
        headers = [h.strip().lower() for h in all_rows[0]]
        topic_col = next(
            (i for i, h in enumerate(headers) if h in ("topic", "topic / title")), None
        )
        if topic_col is None:
            return
        count = sum(
            1 for r in all_rows[1:]
            if len(r) > topic_col and r[topic_col].strip().lower() == topic.strip().lower()
        )
        if count != _CLIP_THRESHOLD:
            return
        subject = f"[Clip Gate] '{topic[:50]}' now has {count} clips — ready to build"
        body = (
            f"Clip Collections threshold reached.\n\n"
            f"Niche: {niche}\n"
            f"Topic: {topic}\n"
            f"Clips ready: {count} (threshold: {_CLIP_THRESHOLD})\n\n"
            f"Next step: approve the row in Content Queue.\n"
            f"content_creator.yml will build the carousel on the next run."
        )
        subprocess.run(
            ["gh", "workflow", "run", "send_email.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", "to=priscila@oakpark-construction.com",
             "-f", f"subject={subject}",
             "-f", f"body={body}"],
            check=False, timeout=30,
        )
        print(f"  Clip threshold email sent: '{topic[:50]}' has {count} clips ready.")
    except Exception as e:
        print(f"  _check_clip_threshold error (non-fatal): {e}")


def update_clip_collections(sheet, topic: str, video_url: str, video_title: str, niche: str):
    """Write a high-relevance video to the Clip Collections tab so motion_sources.py
    can find real clips for carousel builds. Reads headers by name — safe to reorder."""
    if not sheet:
        return
    try:
        ws = sheet.worksheet(CLIP_COLLECTIONS_TAB)
        headers = ws.row_values(1)
        col_pos = {h.strip().lower(): i for i, h in enumerate(headers)}
        width = (max(col_pos.values()) + 1) if col_pos else 10
        row = [""] * width

        def put(col_name, val):
            i = col_pos.get(col_name.lower())
            if i is not None and i < width:
                row[i] = "" if val is None else str(val)

        put("topic",        topic)
        put("topic / title", topic)
        put("title",        video_title)
        put("url",          video_url)
        put("niche",        niche)
        put("source",       "youtube_research")
        put("status",       "ready")
        put("date added",   datetime.now().strftime("%Y-%m-%d"))

        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"  Clip Collections: added [{niche}] {video_title[:50]}")
        _check_clip_threshold(ws, topic, niche)
    except Exception as e:
        print(f"  update_clip_collections error (non-fatal): {e}")


# ── DRIVE UPLOAD ──────────────────────────────────────────────────────────────

def _create_drive_subfolder(parent_folder_id: str, name: str, token: str) -> str:
    """Create a subfolder in Drive and return its ID. Returns '' on failure."""
    try:
        import json as _json
        body = _json.dumps({
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_folder_id],
        }).encode()
        req = urllib.request.Request(
            "https://www.googleapis.com/drive/v3/files?supportsAllDrives=true",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        result = json.loads(urllib.request.urlopen(req, timeout=15).read())
        folder_id = result.get("id", "")
        if folder_id:
            print(f"  Drive subfolder created: {name}/ → {folder_id}")
        return folder_id
    except Exception as e:
        print(f"  _create_drive_subfolder failed (non-fatal): {e}")
        return ""


def upload_clip_to_drive(local_path: str, filename: str, folder_id: str) -> str:
    """Upload a binary clip file (MP4/MP3) to a Drive folder via multipart upload.
    Returns the Drive file ID or '' on failure. SH-010.

    Uses resumable multipart with supportsAllDrives=true so clips land in
    shared drives (never silently in My Drive).
    """
    access_token = get_oauth_token()
    if not access_token:
        print(f"  No Drive token — skipping clip upload of {filename}")
        return ""
    try:
        import mimetypes
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(local_path, "rb") as f:
            clip_bytes = f.read()
        boundary = "boundary_clip_456"
        meta = json.dumps({"name": filename, "parents": [folder_id]})
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{meta}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + clip_bytes + f"\r\n--{boundary}--".encode()

        req = urllib.request.Request(
            "https://www.googleapis.com/upload/drive/v3/files"
            "?uploadType=multipart&supportsAllDrives=true",
            data=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            method="POST",
        )
        result = json.loads(urllib.request.urlopen(req, timeout=120).read())
        fid = result.get("id", "")
        if fid:
            print(f"  Clip uploaded to Drive: {filename} ({len(clip_bytes)//1024}KB)")
        return fid
    except Exception as e:
        print(f"  Clip upload failed for {filename} (non-fatal): {e}")
        return ""


def upload_clips_dir_to_drive(clips_local_dir: str, topic: str, timestamp: str) -> None:
    """Upload all .mp4/.mp3/.webm files in clips_local_dir to Drive under
    <DRIVE_FOLDER_ID>/clips_<topic_slug>_<timestamp>/. SH-010.

    Called at the end of run() after all research is complete. Non-blocking.
    """
    import glob
    import os
    clip_files = []
    for ext in ("*.mp4", "*.mp3", "*.webm", "*.m4a"):
        clip_files.extend(glob.glob(os.path.join(clips_local_dir, ext)))
    if not clip_files:
        print(f"  upload_clips_dir_to_drive: no clip files in {clips_local_dir}")
        return
    access_token = get_oauth_token()
    if not access_token:
        print("  upload_clips_dir_to_drive: no token — skipping")
        return
    topic_slug = re.sub(r"[^a-z0-9]+", "_", topic.lower())[:40]
    folder_name = f"clips_{topic_slug}_{timestamp}"
    clips_folder_id = _create_drive_subfolder(DRIVE_FOLDER_ID, folder_name, access_token)
    if not clips_folder_id:
        return
    uploaded = 0
    for fpath in sorted(clip_files):
        fname = os.path.basename(fpath)
        fsize = os.path.getsize(fpath)
        if fsize < 1024:  # skip tiny stubs
            continue
        fid = upload_clip_to_drive(fpath, fname, clips_folder_id)
        if fid:
            uploaded += 1
    print(
        f"  upload_clips_dir_to_drive: {uploaded}/{len(clip_files)} clips → "
        f"Drive/{folder_name}/ ({clips_folder_id})"
    )


def upload_to_drive(content: str, filename: str, folder_id: str, token: str = None):
    """Upload a text file to Drive. Refreshes SHEETS_TOKEN to get a fresh access_token."""
    # Always fetch a fresh access_token via refresh flow — the legacy `token`
    # arg was the raw JSON blob which Drive rejects with 401.
    access_token = get_oauth_token()
    if not access_token:
        print(f"  No Drive token — skipping upload of {filename}")
        return None

    boundary = "boundary_xyz_123"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps({'name': filename, 'parents': [folder_id]})}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/plain\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode()

    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            print(f"  Uploaded to Drive: {filename}")
            return result.get("id")
    except Exception as e:
        print(f"  Drive upload failed: {e}")
        log_pipeline_failure("Drive upload", e)
        return None

# ── FAILURE LOGGING ───────────────────────────────────────────────────────────
def log_pipeline_failure(stage: str, error: str, sheet=None):
    """Record a silent-failure event. Appends to '🚨 Pipeline Failures' tab and
    accumulates in PIPELINE_FAILURES so __main__ can exit non-zero."""
    PIPELINE_FAILURES.append({"stage": stage, "error": str(error)[:500]})
    print(f"  ❌ FAILURE [{stage}]: {str(error)[:200]}")
    if sheet is None:
        return
    try:
        ws = sheet.worksheet("🚨 Pipeline Failures")
    except Exception:
        return  # tab missing — failures still tracked in PIPELINE_FAILURES
    try:
        run_url = (
            f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{GHA_RUN_ID}"
            if GHA_RUN_ID else ""
        )
        ws.append_row([
            datetime.utcnow().isoformat() + "Z",
            "video-research.yml",
            GHA_RUN_ID,
            stage,
            str(error)[:500],
            run_url,
            "",  # RESOLVED checkbox — leave empty per checkbox rule
            "",  # NOTE
        ], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"  (failure-log write itself failed: {e})")

# ── TIER 3 FALLBACK: CLAUDE WEB SEARCH ────────────────────────────────────────
# When YouTube blocks transcripts entirely (the GHA-IP issue), we still need
# actionable research. Claude's native web_search tool lets the API do the
# searching + synthesis on Anthropic's side. One call per query, returns a list
# of result-shaped dicts that slot into all_results.
def claude_web_research(topic: str, query: str, max_results: int = 5) -> list[dict]:
    """Call Claude API with web_search enabled. Returns list of synthetic 'video'
    results so downstream sheet/report writers don't need changes. Source URLs
    become the 'url' field; quality assessment becomes the 'analysis' block."""
    if not CLAUDE_KEY_4_CONTENT:
        return []
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    prompt = f"""You are researching: {topic}
Specific query: {query}

Use web_search to find {max_results} of the most useful written sources (articles, official docs, blog posts, GitHub READMEs) on this query. Focus on actionable prompt examples, technique walk-throughs, and real-world workflows — not video listicles.

For each source, return JSON in this exact shape:
{{
  "title": "page title",
  "url": "full URL",
  "uploader": "publication / author / domain",
  "summary": "2-3 sentence summary of the actionable content",
  "tools_used": ["tools, models, or platforms covered"],
  "technique": "specific technique, prompt pattern, or workflow demonstrated",
  "key_tips": ["up to 3 concrete actionable tips — quote prompt examples verbatim where possible"],
  "use_case": "what this is best for",
  "relevant_to_us": true,
  "relevance_reason": "why or why not relevant to Oak Park Construction (US construction marketing) / Hig Negocios (Brazilian real estate marketing)",
  "watch_priority": "high / medium / low",
  "relevance_score": 1-10,
  "quality_assessment": "honest read on usefulness"
}}

Return ONLY a JSON array of {max_results} items. No markdown, no prose."""
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
            messages=[{"role": "user", "content": prompt}],
        )
        # Walk content blocks; the final text block holds the JSON answer
        text = ""
        for block in msg.content:
            if getattr(block, "type", "") == "text":
                text = block.text
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if not raw:
            return []
        items = json.loads(raw)
        # Adapt each item to the all_results shape used by save_to_sheet/report
        adapted = []
        for it in items:
            adapted.append({
                "id": "",  # web result, no YouTube ID
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "uploader": it.get("uploader", ""),
                "duration": 0,
                "upload_date": "",
                "analysis": {
                    "summary": it.get("summary", ""),
                    "tools_used": it.get("tools_used", []),
                    "technique": it.get("technique", ""),
                    "quality_assessment": it.get("quality_assessment", ""),
                    "key_tips": it.get("key_tips", []),
                    "use_case": it.get("use_case", ""),
                    "relevant_to_us": it.get("relevant_to_us", True),
                    "relevance_reason": it.get("relevance_reason", ""),
                    "watch_priority": it.get("watch_priority", "medium"),
                    "relevance_score": it.get("relevance_score", 5),
                    "has_transcript": True,  # full article content was synthesized
                    "source_kind": "web_article",
                },
                "transcript_excerpt": it.get("summary", "")[:500],
            })
        return adapted
    except Exception as e:
        print(f"  Claude web_search fallback failed for '{query}': {e}")
        return []


def run_claude_web_fallback(topic: str, queries: list[str], sheet, all_results: list, seen_ids: set):
    """Execute Tier 3 across all original queries when YouTube tier failed flat."""
    print(f"\n{'='*40}")
    print(f"TIER 3 FALLBACK — Claude web_search (YouTube transcripts unavailable)")
    print(f"{'='*40}")
    added = 0
    for query in queries:
        print(f"\n  [Web fallback] Searching: {query}")
        items = claude_web_research(topic, query, max_results=5)
        for item in items:
            url = item.get("url", "")
            if not url or url in seen_ids:
                continue
            seen_ids.add(url)
            all_results.append(item)
            added += 1
            if sheet:
                save_to_sheet(sheet, item, item["analysis"], topic)
            print(f"    + {item['title'][:70]}")
    print(f"\nTier 3 added: {added} web articles")
    return added


# ── MAIN ──────────────────────────────────────────────────────────────────────
def run(topic: str, queries: list[str], max_per_query: int = 5, niche: str = ""):
    print(f"\n=== VIDEO RESEARCH: {topic} ===")
    print(f"Initial queries: {queries}")
    print(f"Target: {TARGET_VIDEOS} transcribed videos across 3 rounds\n")

    sheet = get_sheet()
    all_results = []
    seen_ids = set()

    def process_batch(batch_queries: list[str], round_num: int) -> int:
        """Search + analyze a batch of queries. Returns count of new videos added."""
        new_count = 0
        for query in batch_queries:
            if len(all_results) >= TARGET_VIDEOS:
                break
            print(f"\n  [Round {round_num}] Searching: {query}")
            videos = search_youtube(query, max_per_query)

            for video in videos:
                if len(all_results) >= TARGET_VIDEOS:
                    break
                if video["id"] in seen_ids:
                    continue
                seen_ids.add(video["id"])

                print(f"  [{video['id']}] {video['title'][:60]}")
                time.sleep(2)  # avoid YouTube 429 rate limiting between transcript calls
                transcript = get_transcript(video["id"])

                has_transcript = "[transcript unavailable" not in transcript
                mode = "with transcript" if has_transcript else "metadata only"
                print(f"    Analyzing ({mode})...")
                analysis = analyze_with_claude(video, transcript, topic)

                result = {**video, "analysis": analysis, "transcript_excerpt": transcript[:500]}
                all_results.append(result)
                new_count += 1

                if sheet:
                    save_to_sheet(sheet, video, analysis, topic)
                    # High-relevance clips (score ≥ 7) also land in Clip Collections
                    # so motion_sources.py can find them for carousel build.
                    if analysis.get("relevance_score", 0) >= 7 and video.get("url"):
                        update_clip_collections(sheet, topic, video["url"], video.get("title", ""), niche)
        return new_count

    # Round 1 — initial queries
    print(f"\n{'='*40}")
    print(f"ROUND 1 — Initial search ({len(queries)} queries)")
    print(f"{'='*40}")
    process_batch(queries, 1)
    print(f"\nRound 1 done: {len(all_results)}/{TARGET_VIDEOS} videos")

    # Round 2 — expand keywords based on round 1 findings
    if len(all_results) < TARGET_VIDEOS and all_results:
        print(f"\n{'='*40}")
        print(f"ROUND 2 — Expanding keywords from Round 1 findings")
        print(f"{'='*40}")
        expanded = expand_keywords(topic, all_results, 2)
        if expanded:
            print(f"New queries: {expanded}")
            process_batch(expanded, 2)
            print(f"\nRound 2 done: {len(all_results)}/{TARGET_VIDEOS} videos")
        else:
            print(f"  Keyword expansion skipped (no API key or failed)")

    # Round 3 — expand again
    if len(all_results) < TARGET_VIDEOS and all_results:
        print(f"\n{'='*40}")
        print(f"ROUND 3 — Second expansion")
        print(f"{'='*40}")
        expanded2 = expand_keywords(topic, all_results, 3)
        if expanded2:
            print(f"New queries: {expanded2}")
            process_batch(expanded2, 3)
            print(f"\nRound 3 done: {len(all_results)}/{TARGET_VIDEOS} videos")
        else:
            print(f"  Keyword expansion skipped (no API key or failed)")

    # Build master findings report
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    high = [r for r in all_results if r["analysis"].get("watch_priority") == "high"]
    implementable = sorted(
        [r for r in all_results if r["analysis"].get("relevance_score", 0) >= 7],
        key=lambda r: r["analysis"].get("relevance_score", 0),
        reverse=True
    )
    
    doc_lines = [
        f"RESEARCH REPORT: {topic}",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Videos analyzed: {len(all_results)} (target: {TARGET_VIDEOS})",
        f"High priority: {len(high)} | Immediately implementable (score 7+): {len(implementable)}",
        "",
        "=" * 60,
        "",
    ]

    if implementable:
        doc_lines.append("BEST IDEAS TO IMPLEMENT NOW")
        doc_lines.append("-" * 40)
        for i, r in enumerate(implementable[:5], 1):
            score = r["analysis"].get("relevance_score", 0)
            doc_lines.append(f"{i}. [{score}/10] {r['title']}")
            doc_lines.append(f"   URL: {r['url']}")
            doc_lines.append(f"   {r['analysis'].get('summary', '')}")
            tips = r["analysis"].get("key_tips", [])
            if tips:
                doc_lines.append("   Key tips:")
                for tip in tips[:3]:
                    doc_lines.append(f"     - {tip}")
            doc_lines.append(f"   Why: {r['analysis'].get('relevance_reason', '')}")
            doc_lines.append("")

    doc_lines.append("=" * 60)
    doc_lines.append("ALL VIDEOS ANALYZED")
    doc_lines.append("-" * 40)
    for r in all_results:
        score = r["analysis"].get("relevance_score", 0)
        priority = r["analysis"].get("watch_priority", "?")
        doc_lines.append(f"[{score}/10 | {priority}] {r['title']}")
        doc_lines.append(f"  URL: {r['url']}")
        doc_lines.append(f"  {r['analysis'].get('summary', '')}")
        tools = r["analysis"].get("tools_used", [])
        if tools:
            doc_lines.append(f"  Tools: {', '.join(tools)}")
        doc_lines.append("")

    doc_content = "\n".join(doc_lines)
    filename = f"research_{topic.replace(' ','_')}_{timestamp}.txt"
    
    print(f"\nSaving report: {filename}")
    # Always write to /tmp first so the GHA artifact upload step can grab it,
    # even if Drive upload fails. This is the recovery path.
    local_path = f"/tmp/{filename}"
    with open(local_path, "w") as f:
        f.write(doc_content)
    print(f"  Saved locally to {local_path}")
    upload_to_drive(doc_content, filename, DRIVE_FOLDER_ID)
    
    # Bug 3 ceiling check: if 0 videos got transcripts, log it (YouTube IP block)
    # AND trigger Tier 3 web-article fallback so we still ship actionable research.
    transcripts_ok = sum(1 for r in all_results if r["analysis"].get("has_transcript"))
    if all_results and transcripts_ok == 0:
        log_pipeline_failure(
            "Transcription (all videos metadata-only)",
            f"0/{len(all_results)} videos returned a transcript — YouTube likely blocking GHA IP. "
            "Falling back to Claude web_search for written sources.",
            sheet,
        )
        # Tier 3: Claude web_search across original queries
        web_added = run_claude_web_fallback(topic, queries, sheet, all_results, seen_ids)
        if web_added > 0:
            # Re-sort implementable list now that web articles are included, then
            # rewrite the report so the Drive doc reflects fallback findings.
            implementable = sorted(
                [r for r in all_results if r["analysis"].get("relevance_score", 0) >= 7],
                key=lambda r: r["analysis"].get("relevance_score", 0),
                reverse=True,
            )
            doc_lines2 = [
                f"RESEARCH REPORT (with web fallback): {topic}",
                f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"YouTube videos: {len(all_results) - web_added} (metadata only — transcripts blocked)",
                f"Web articles via Claude: {web_added}",
                f"Immediately implementable (score 7+): {len(implementable)}",
                "",
                "=" * 60,
                "",
                "BEST IDEAS TO IMPLEMENT NOW",
                "-" * 40,
            ]
            for i, r in enumerate(implementable[:10], 1):
                score = r["analysis"].get("relevance_score", 0)
                kind = r["analysis"].get("source_kind", "youtube")
                doc_lines2.append(f"{i}. [{score}/10] [{kind}] {r['title']}")
                doc_lines2.append(f"   URL: {r['url']}")
                doc_lines2.append(f"   {r['analysis'].get('summary', '')}")
                tips = r["analysis"].get("key_tips", [])
                if tips:
                    doc_lines2.append("   Key tips:")
                    for tip in tips[:3]:
                        doc_lines2.append(f"     - {tip}")
                doc_lines2.append(f"   Why: {r['analysis'].get('relevance_reason', '')}")
                doc_lines2.append("")
            fallback_doc = "\n".join(doc_lines2)
            fb_filename = f"research_{topic.replace(' ','_')}_{timestamp}_WITH_FALLBACK.txt"
            with open(f"/tmp/{fb_filename}", "w") as f:
                f.write(fallback_doc)
            upload_to_drive(fallback_doc, fb_filename, DRIVE_FOLDER_ID)
            # Tier 3 succeeded — clear the transcript failure so run does not exit 1
            PIPELINE_FAILURES[:] = [
                fl for fl in PIPELINE_FAILURES
                if fl.get("stage") != "Transcription (all videos metadata-only)"
            ]
            print(f"  ✅ Tier 3 fallback recovered {web_added} usable sources — clearing failure flag")

    # Flush any failures recorded BEFORE sheet was available
    if sheet and PIPELINE_FAILURES:
        for f in PIPELINE_FAILURES:
            try:
                ws = sheet.worksheet("🚨 Pipeline Failures")
                run_url = (
                    f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{GHA_RUN_ID}"
                    if GHA_RUN_ID else ""
                )
                ws.append_row([
                    datetime.utcnow().isoformat() + "Z",
                    "video-research.yml",
                    GHA_RUN_ID,
                    f["stage"],
                    f["error"],
                    run_url,
                    "",
                    "(flushed at run end)",
                ], value_input_option="USER_ENTERED")
            except Exception:
                pass

    # SH-010: Upload any downloaded clip files to Drive resources/clips/ folder.
    # Clips land in /tmp/ during transcript extraction (yt-dlp audio downloads).
    # We scan /tmp for any mp4/mp3 files whose name matches the research run
    # timestamp pattern, then upload them to Drive under a clips_<topic>/ subfolder.
    try:
        import tempfile
        _tmp_dir = tempfile.gettempdir()
        upload_clips_dir_to_drive(_tmp_dir, topic, timestamp)
    except Exception as _clips_upload_err:
        print(f"  SH-010 clips upload skipped (non-fatal): {_clips_upload_err}")

    print(f"\n{'='*60}")
    print(f"DONE: {len(all_results)} videos analyzed")
    print(f"Implementable (7+): {len(implementable)}")
    print(f"High priority: {len(high)}")
    if PIPELINE_FAILURES:
        print(f"❌ {len(PIPELINE_FAILURES)} silent failure(s) — see '🚨 Pipeline Failures' tab in Ideas & Inbox")
        for f in PIPELINE_FAILURES:
            print(f"   - {f['stage']}: {f['error'][:120]}")
    if implementable:
        top = implementable[0]
        print(f"Top pick: {top['title']}")
        print(f"  {top['url']}")
    print(f"{'='*60}")
    return all_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Existing inputs (kept required ONLY when --mode is general/empty)
    parser.add_argument("--topic", default="", help="Research topic label (e.g. 'kling ai talking head')")
    parser.add_argument("--queries", default="", help="Comma-separated search queries")
    parser.add_argument("--max", type=int, default=5, help="Max results per query")
    parser.add_argument("--niche", default="", help="Niche label written to Clip Collections (brazil/usa/opc)")
    # SH-104: person evidence mining mode
    parser.add_argument("--mode", default="general",
                        choices=["general", "person_evidence_mining"],
                        help="Research mode. Default 'general' = legacy run().")
    parser.add_argument("--seed-url", dest="seed_url", default="",
                        help="(person_evidence_mining) Seed Reel/Video URL")
    parser.add_argument("--person-name", dest="person_name", default="",
                        help="(person_evidence_mining) Person to mine clips of")
    parser.add_argument("--evidence-requirement", dest="evidence_requirement", default="",
                        help="(person_evidence_mining) What we are looking for in transcripts")
    parser.add_argument("--target-clip-count", dest="target_clip_count", type=int, default=6,
                        help="(person_evidence_mining) Number of verified clips wanted")
    args = parser.parse_args()

    if args.mode == "person_evidence_mining":
        # Validate required inputs for new mode
        if not args.seed_url or not args.person_name or not args.evidence_requirement:
            print("ERROR: --mode person_evidence_mining requires --seed-url, "
                  "--person-name, --evidence-requirement")
            sys.exit(2)
        # scripts/ already on sys.path because youtube_research.py is run
        # from scripts/. research/ is a package with its own helper imports.
        from research.person_evidence_runner import (  # type: ignore
            run_person_evidence_mining, PIPELINE_FAILURES as RES_FAILURES,
        )
        rc = run_person_evidence_mining(
            seed_url=args.seed_url,
            person_name=args.person_name,
            evidence_requirement=args.evidence_requirement,
            target_clip_count=args.target_clip_count,
            niche=args.niche or "brazil",
        )
        # Surface inner failures into module-level list so the existing
        # exit gate below catches them too
        PIPELINE_FAILURES.extend(RES_FAILURES)
        sys.exit(rc if rc != 0 else (1 if PIPELINE_FAILURES else 0))

    # Legacy general mode
    if not args.topic or not args.queries:
        print("ERROR: --mode general requires --topic and --queries")
        sys.exit(2)
    queries = [q.strip() for q in args.queries.split(",")]
    run(args.topic, queries, args.max, args.niche)

    # Fail loud: any silent failure → non-zero exit so GitHub marks run ❌
    if PIPELINE_FAILURES:
        sys.exit(1)
