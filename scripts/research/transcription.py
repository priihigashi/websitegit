"""transcription.py — URL -> transcript cascade.

Routes:
  YouTube     -> YouTubeTranscriptApi -> yt-dlp+Whisper -> yt-dlp+iOS+Whisper -> Apify+Whisper
  Instagram   -> yt-dlp+IG cookies+Whisper -> Apify instagram-scraper+Whisper
  TikTok      -> yt-dlp+Whisper -> Apify clockworks+Whisper
  Other       -> yt-dlp+Whisper

Returns: {"transcript": str, "source": str, "duration": int|None, "error": str|None}

Env vars used:
  OPENAI_API_KEY, APIFY_API_KEY, PRI_OP_YT_COOKIES, PRI_OP_IG_COOKIES
"""

from __future__ import annotations
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from typing import Optional

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
APIFY_API_KEY   = os.environ.get("APIFY_API_KEY", "")
YT_COOKIES_RAW  = os.environ.get("PRI_OP_YT_COOKIES", "")
IG_COOKIES_RAW  = os.environ.get("PRI_OP_IG_COOKIES", "")
APIFY_BASE      = "https://api.apify.com/v2"

_apify_limit_hit = False


# ── helpers ──────────────────────────────────────────────────────────────────

def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _is_instagram(url: str) -> bool:
    return "instagram.com" in url


def _is_tiktok(url: str) -> bool:
    return "tiktok.com" in url


def _yt_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _ig_shortcode(url: str) -> str:
    m = re.search(r"/(?:reel|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else ""


_YT_COOKIES_PATH = ""
_IG_COOKIES_PATH = ""


def _write_cookies(raw: str, name: str) -> str:
    if not raw.strip():
        return ""
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, "w") as f:
        f.write(raw)
    return path


def _yt_cookies_file() -> str:
    global _YT_COOKIES_PATH
    if not _YT_COOKIES_PATH:
        _YT_COOKIES_PATH = _write_cookies(YT_COOKIES_RAW, "yt_cookies.txt")
    return _YT_COOKIES_PATH


def _ig_cookies_file() -> str:
    global _IG_COOKIES_PATH
    if not _IG_COOKIES_PATH:
        _IG_COOKIES_PATH = _write_cookies(IG_COOKIES_RAW, "ig_cookies.txt")
    return _IG_COOKIES_PATH


def _whisper_transcribe(audio_path: str) -> str:
    if not OPENAI_API_KEY:
        return ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1", file=f, response_format="text"
            )
        return resp if isinstance(resp, str) else getattr(resp, "text", "")
    except Exception as e:
        print(f"    Whisper failed: {e}")
        return ""


def _ytdlp_audio(url: str, tmp_dir: str, extra_args: Optional[list] = None) -> str:
    """Download audio with yt-dlp. Returns audio path or ''."""
    out = os.path.join(tmp_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "0", "--output", out,
        "--no-playlist", "--quiet", "--no-warnings",
    ]
    if _is_youtube(url):
        ck = _yt_cookies_file()
        if ck:
            cmd.extend(["--cookies", ck])
    elif _is_instagram(url):
        ck = _ig_cookies_file()
        if ck:
            cmd.extend(["--cookies", ck])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:
        print(f"    yt-dlp error: {e}")
        return ""
    if r.returncode != 0:
        print(f"    yt-dlp failed: {r.stderr[:200].strip()}")
        return ""
    for f in os.listdir(tmp_dir):
        if f.endswith(".mp3"):
            return os.path.join(tmp_dir, f)
    return ""


def _ytdlp_duration(url: str) -> Optional[int]:
    try:
        cmd = ["yt-dlp", "--no-playlist", "--quiet", "--print", "%(duration)s", url]
        if _is_instagram(url):
            ck = _ig_cookies_file()
            if ck:
                cmd[3:3] = ["--cookies", ck]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except Exception:
        pass
    return None


# ── YouTube transcript cascade ───────────────────────────────────────────────

def _yt_transcript_api(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    for kwargs in [{"languages": ["pt", "en", "en-US", "en-GB", "es"]}, {}]:
        try:
            api = YouTubeTranscriptApi()
            t = api.fetch(video_id, **kwargs)
            return " ".join(seg.text for seg in t)
        except Exception:
            time.sleep(2)
    return ""


def _ytdlp_whisper(video_id: str, ios: bool = False) -> str:
    if not OPENAI_API_KEY:
        return ""
    url = f"https://www.youtube.com/watch?v={video_id}"
    extra = ["--extractor-args", "youtube:player_client=ios,web_creator"] if ios else None
    with tempfile.TemporaryDirectory() as td:
        path = _ytdlp_audio(url, td, extra)
        if not path:
            return ""
        return _whisper_transcribe(path)


def _apify_yt_whisper(video_id: str) -> str:
    global _apify_limit_hit
    if _apify_limit_hit or not APIFY_API_KEY:
        return ""
    actor = "bernardo~youtube-scraper"
    payload = {
        "startUrls": [{"url": f"https://www.youtube.com/watch?v={video_id}"}],
        "maxResults": 1,
        "proxy": {"useApifyProxy": True},
    }
    try:
        run = json.loads(urllib.request.urlopen(
            urllib.request.Request(
                f"{APIFY_BASE}/acts/{actor}/runs?token={APIFY_API_KEY}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            ), timeout=30
        ).read())
        run_id = run["data"]["id"]
        status = ""
        for _ in range(18):
            time.sleep(10)
            s = json.loads(urllib.request.urlopen(
                f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_API_KEY}", timeout=15
            ).read())
            status = s["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        if status != "SUCCEEDED":
            return ""
        items = json.loads(urllib.request.urlopen(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items?token={APIFY_API_KEY}&limit=1&format=json",
            timeout=30
        ).read())
        if not items:
            return ""
        media = (items[0].get("mediaUrl") or items[0].get("videoUrl")
                 or items[0].get("audioUrl") or "")
        if not media or "youtube.com" in str(media):
            return ""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audio.mp3")
            with urllib.request.urlopen(media, timeout=120) as dl:
                with open(path, "wb") as f:
                    f.write(dl.read())
            if os.path.getsize(path) < 5_000:
                return ""
            return _whisper_transcribe(path)
    except Exception as e:
        msg = str(e)
        if "limit" in msg.lower() and "403" in msg:
            _apify_limit_hit = True
        print(f"    Apify YT failed: {msg[:200]}")
        return ""


def transcribe_youtube(video_id: str) -> dict:
    """4-tier YouTube cascade. Returns {transcript, source, error}."""
    text = _yt_transcript_api(video_id)
    if text:
        return {"transcript": text, "source": "youtube_transcript_api", "error": None}
    text = _ytdlp_whisper(video_id, ios=False)
    if text:
        return {"transcript": text, "source": "ytdlp_whisper", "error": None}
    text = _ytdlp_whisper(video_id, ios=True)
    if text:
        return {"transcript": text, "source": "ytdlp_ios_whisper", "error": None}
    text = _apify_yt_whisper(video_id)
    if text:
        return {"transcript": text, "source": "apify_whisper", "error": None}
    return {"transcript": "", "source": "", "error": "all_tiers_exhausted"}


# ── Instagram transcript ─────────────────────────────────────────────────────

def _apify_ig_audio(reel_url: str) -> str:
    """Apify instagram-scraper -> videoUrl -> Whisper."""
    global _apify_limit_hit
    if _apify_limit_hit or not APIFY_API_KEY:
        return ""
    actor = "apify~instagram-scraper"
    payload = {
        "directUrls": [reel_url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["DATACENTER"]},
    }
    try:
        run = json.loads(urllib.request.urlopen(
            urllib.request.Request(
                f"{APIFY_BASE}/acts/{actor}/runs?token={APIFY_API_KEY}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            ), timeout=30
        ).read())
        run_id = run["data"]["id"]
        status = ""
        for _ in range(12):
            time.sleep(10)
            s = json.loads(urllib.request.urlopen(
                f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_API_KEY}", timeout=15
            ).read())
            status = s["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        if status != "SUCCEEDED":
            return ""
        items = json.loads(urllib.request.urlopen(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items?token={APIFY_API_KEY}&limit=1&format=json",
            timeout=30
        ).read())
        if not items:
            return ""
        video_url = items[0].get("videoUrl", "")
        if not video_url:
            return ""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audio.mp4")
            with urllib.request.urlopen(video_url, timeout=120) as dl:
                with open(path, "wb") as f:
                    f.write(dl.read())
            if os.path.getsize(path) < 5_000:
                return ""
            return _whisper_transcribe(path)
    except Exception as e:
        if "limit" in str(e).lower() and "403" in str(e):
            _apify_limit_hit = True
        print(f"    Apify IG failed: {str(e)[:200]}")
        return ""


def transcribe_instagram(reel_url: str) -> dict:
    """yt-dlp+IG cookies+Whisper -> Apify+Whisper."""
    with tempfile.TemporaryDirectory() as td:
        path = _ytdlp_audio(reel_url, td)
        if path:
            text = _whisper_transcribe(path)
            if text:
                return {"transcript": text, "source": "ytdlp_ig_whisper", "error": None}
    text = _apify_ig_audio(reel_url)
    if text:
        return {"transcript": text, "source": "apify_ig_whisper", "error": None}
    return {"transcript": "", "source": "", "error": "all_tiers_exhausted"}


# ── TikTok / generic ─────────────────────────────────────────────────────────

def transcribe_generic(url: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        path = _ytdlp_audio(url, td)
        if path:
            text = _whisper_transcribe(path)
            if text:
                return {"transcript": text, "source": "ytdlp_whisper", "error": None}
    return {"transcript": "", "source": "", "error": "all_tiers_exhausted"}


# ── public dispatcher ────────────────────────────────────────────────────────

def transcribe_url(url: str) -> dict:
    """Single entry point. Returns {transcript, source, duration, error, url, platform}."""
    url = url.strip()
    platform = "other"
    result = {"transcript": "", "source": "", "duration": None, "error": None,
              "url": url, "platform": platform}

    if _is_youtube(url):
        platform = "youtube"
        vid = _yt_video_id(url)
        if not vid:
            result.update({"platform": platform, "error": "no_video_id"})
            return result
        r = transcribe_youtube(vid)
    elif _is_instagram(url):
        platform = "instagram"
        r = transcribe_instagram(url)
    elif _is_tiktok(url):
        platform = "tiktok"
        r = transcribe_generic(url)
    else:
        r = transcribe_generic(url)

    result["platform"] = platform
    result["transcript"] = r.get("transcript", "")
    result["source"] = r.get("source", "")
    result["error"] = r.get("error")
    if result["transcript"]:
        result["duration"] = _ytdlp_duration(url)
    return result
