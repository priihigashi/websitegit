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
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

# Sibling import shim — route_state lives next to this file when run as
# `research.transcription` AND when run as a flat script via youtube_research.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from route_state import get_state  # noqa: E402

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
    # Handle:
    #   ?v=ID  | youtu.be/ID | /shorts/ID | /embed/ID
    #   youtube-nocookie.com/embed/ID
    m = re.search(
        r"(?:v=|youtu\.be/|/shorts/|/embed/|/v/)([A-Za-z0-9_-]{11})", url
    )
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
    # Owner read/write only — cookies carry session bearers; do not leak to
    # other processes / users that share /tmp on shared runners.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    fd = os.open(path, flags, mode)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(raw)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        raise
    # Belt + suspenders for systems where umask intervened.
    try:
        os.chmod(path, mode)
    except Exception:
        pass
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


# Module-level last-error trace so callers can distinguish "no transcript"
# from "Whisper down" / "yt-dlp down" without adding a callback to every
# helper. transcribe_url() reads + clears this between dispatches.
_LAST_ERROR: dict | None = None


def _record_error(stage: str, error) -> None:
    """Stash a structured error trace for the most recent failed step.
    Read by transcribe_url() and surfaced to the runner via the result dict
    so person_evidence_runner._fail() can log it to 🚨 Pipeline Failures."""
    global _LAST_ERROR
    msg = _scrub(str(error))[:400]
    _LAST_ERROR = {"stage": stage, "error": msg}


def _consume_last_error() -> dict | None:
    global _LAST_ERROR
    e = _LAST_ERROR
    _LAST_ERROR = None
    return e


def _scrub(s: str) -> str:
    """Strip query strings that may carry signed tokens / API keys before
    logging. Cheap regex — not a full sanitiser, but prevents the obvious
    leak of ?token=..., ?Signature=..., ?key=... in error messages."""
    if not s:
        return s
    out = re.sub(r"([?&])(token|Signature|sig|key|apikey|api_key|access_token|auth)=[^&\s]+",
                 r"\1\2=REDACTED", s, flags=re.IGNORECASE)
    return out


def _whisper_transcribe(audio_path: str) -> str:
    if not OPENAI_API_KEY:
        _record_error("whisper", "no_openai_api_key")
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
        # Surface to the runner — was previously swallowed, making "no quote"
        # indistinguishable from "Whisper API outage".
        msg = _scrub(str(e))
        _record_error("whisper", msg)
        print(f"    Whisper failed: {msg[:200]}")
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
        msg = _scrub(str(e))
        _record_error("ytdlp_audio", msg)
        print(f"    yt-dlp error: {msg[:200]}")
        return ""
    if r.returncode != 0:
        err = _scrub(r.stderr or "").strip()
        _record_error("ytdlp_audio", err[:300] or f"returncode_{r.returncode}")
        print(f"    yt-dlp failed: {err[:200]}")
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
    state = get_state()
    if not state.should_try_apify():
        return ""
    if _apify_limit_hit or not APIFY_API_KEY:
        if not APIFY_API_KEY:
            state.mark_unavailable("apify", "no_api_key")
        return ""
    actor = "bernardo~youtube-scraper"
    payload = {
        "startUrls": [{"url": f"https://www.youtube.com/watch?v={video_id}"}],
        "maxResults": 1,
        "proxy": {"useApifyProxy": True},
    }
    body, err = _apify_request(
        "POST", f"/acts/{actor}/runs",
        params={"token": APIFY_API_KEY}, json_body=payload, timeout=30,
    )
    if err is not None:
        low = err.lower()
        if "403" in err or "402" in err or "credit" in low or "billing" in low:
            _apify_limit_hit = True
        state.mark_failed("apify", "yt_audio_start", err)
        _record_error("apify_yt", err)
        print(f"    Apify YT start failed: {err[:300]}")
        return ""
    run_id = body["data"]["id"]
    status = ""
    for _ in range(18):
        time.sleep(10)
        s, err = _apify_request(
            "GET", f"/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY}, timeout=15,
        )
        if err or not isinstance(s, dict):
            continue
        status = s.get("data", {}).get("status", "")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status != "SUCCEEDED":
        state.mark_failed("apify", "yt_audio_run", f"run_status:{status}")
        _record_error("apify_yt", f"run_status:{status}")
        return ""
    items_body, err = _apify_request(
        "GET", f"/actor-runs/{run_id}/dataset/items",
        params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
        timeout=30,
    )
    items = items_body if isinstance(items_body, list) else []
    if not items:
        state.mark_failed("apify", "yt_audio_dataset", "empty_dataset")
        _record_error("apify_yt", "empty_dataset")
        return ""
    media = (items[0].get("mediaUrl") or items[0].get("videoUrl")
             or items[0].get("audioUrl") or "")
    if not media or "youtube.com" in str(media):
        keys_seen = sorted(items[0].keys())[:30]
        state.mark_failed("apify", "yt_audio_no_media",
                          f"keys_seen={keys_seen}")
        _record_error("apify_yt", f"no_media_url; keys={keys_seen}")
        return ""
    try:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audio.mp3")
            with urllib.request.urlopen(media, timeout=120) as dl:
                with open(path, "wb") as f:
                    f.write(dl.read())
            if os.path.getsize(path) < 5_000:
                state.mark_failed("apify", "yt_audio_tiny", "size<5k")
                return ""
            return _whisper_transcribe(path)
    except Exception as e:
        msg = _scrub(str(e))
        state.mark_failed("apify", "yt_audio_download", msg)
        _record_error("apify_yt", msg)
        print(f"    Apify YT download failed: {msg[:200]}")
        return ""


def transcribe_youtube(video_id: str) -> dict:
    """4-tier YouTube cascade. Returns {transcript, source, error, error_trace}."""
    text = _yt_transcript_api(video_id)
    if text:
        _consume_last_error()
        return {"transcript": text, "source": "youtube_transcript_api", "error": None, "error_trace": None}
    text = _ytdlp_whisper(video_id, ios=False)
    if text:
        _consume_last_error()
        return {"transcript": text, "source": "ytdlp_whisper", "error": None, "error_trace": None}
    text = _ytdlp_whisper(video_id, ios=True)
    if text:
        _consume_last_error()
        return {"transcript": text, "source": "ytdlp_ios_whisper", "error": None, "error_trace": None}
    text = _apify_yt_whisper(video_id)
    if text:
        _consume_last_error()
        return {"transcript": text, "source": "apify_whisper", "error": None, "error_trace": None}
    trace = _consume_last_error()
    return {"transcript": "", "source": "", "error": "all_tiers_exhausted",
            "error_trace": trace}


# ── Instagram transcript ─────────────────────────────────────────────────────

_IG_MEDIA_FIELDS = (
    "videoUrl", "video_url", "videoUrlBackup", "downloadedVideo",
    "videoUrlMain", "media_url", "displayUrl",  # displayUrl last — image fallback (no audio)
)


def _extract_ig_media_url(item: dict) -> tuple[str, str]:
    """Return (url, source_field). Walks the known set of media URL keys
    Apify's IG actors emit. videoUrl/displayUrl naming varies by actor +
    post type (single reel, carousel, story-replay) so we try them all
    rather than hardcoding one. Returns ('', '') if none usable for audio."""
    if not isinstance(item, dict):
        return "", ""
    for k in _IG_MEDIA_FIELDS:
        v = item.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v, k
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, str) and first.startswith(("http://", "https://")):
                return first, k
            if isinstance(first, dict):
                for sub in ("url", "src", "videoUrl"):
                    if isinstance(first.get(sub), str):
                        return first[sub], f"{k}[0].{sub}"
    return "", ""


def _apify_request(method: str, path: str, *, params: dict | None = None,
                   json_body: dict | None = None, timeout: int = 30):
    """Thin wrapper: returns (json_or_text, error_msg). error_msg surfaces
    Apify's JSON `error.message` field instead of a bare HTTP status code,
    so quota/credit issues are distinguishable from schema/input bugs."""
    try:
        import requests
    except ImportError:
        return None, "requests_not_installed"
    try:
        if method == "POST":
            resp = requests.post(f"{APIFY_BASE}{path}", params=params or {},
                                 json=json_body, timeout=timeout)
        else:
            resp = requests.get(f"{APIFY_BASE}{path}", params=params or {},
                                timeout=timeout)
    except Exception as e:
        return None, _scrub(str(e))[:400]
    try:
        body = resp.json()
    except Exception:
        body = None
    if resp.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            msg = (f"HTTP {resp.status_code} [{err.get('type','?')}] "
                   f"{err.get('message','?')}")
        else:
            msg = f"HTTP {resp.status_code} {(resp.text or '')[:300]}"
        return None, _scrub(msg)[:400]
    return body, None


def _apify_ig_audio(reel_url: str) -> str:
    """Apify instagram-scraper -> mediaUrl -> Whisper.

    Used as a transcription fallback when yt-dlp gets rate-limited or
    login-walled on a public reel (common from GitHub runner IPs).

    Tries the full set of known media URL fields rather than just
    `videoUrl` — some actors return `video_url`, `videoUrlBackup`, or
    embed the URL inside an array under `downloadedVideo`. Logs which
    field actually carried a usable URL so debugging future actor schema
    drift is fast.
    """
    global _apify_limit_hit
    state = get_state()
    if not state.should_try_apify():
        return ""
    if _apify_limit_hit or not APIFY_API_KEY:
        if not APIFY_API_KEY:
            state.mark_unavailable("apify", "no_api_key")
        return ""
    actor = "apify~instagram-scraper"
    payload = {
        "directUrls": [reel_url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["DATACENTER"]},
    }
    body, err = _apify_request(
        "POST", f"/acts/{actor}/runs",
        params={"token": APIFY_API_KEY}, json_body=payload, timeout=30,
    )
    if err is not None:
        low = err.lower()
        if "403" in err or "402" in err or "credit" in low or "billing" in low:
            _apify_limit_hit = True
        state.mark_failed("apify", "ig_audio_start", err)
        _record_error("apify_ig", err)
        print(f"    Apify IG start failed: {err[:300]}")
        return ""
    run_id = body["data"]["id"]

    status = ""
    for _ in range(12):
        time.sleep(10)
        s, err = _apify_request(
            "GET", f"/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY}, timeout=15,
        )
        if err or not isinstance(s, dict):
            continue
        status = s.get("data", {}).get("status", "")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status != "SUCCEEDED":
        state.mark_failed("apify", "ig_audio_run", f"run_status:{status}")
        _record_error("apify_ig", f"run_status:{status}")
        print(f"    Apify IG run ended: {status}")
        return ""

    items_body, err = _apify_request(
        "GET", f"/actor-runs/{run_id}/dataset/items",
        params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
        timeout=30,
    )
    items = items_body if isinstance(items_body, list) else []
    if not items:
        state.mark_failed("apify", "ig_audio_dataset", "empty_dataset")
        _record_error("apify_ig", "empty_dataset")
        print("    Apify IG dataset empty")
        return ""

    video_url, src_field = _extract_ig_media_url(items[0])
    if not video_url:
        # Actor SUCCEEDED but no usable media URL — likely a carousel image
        # post or an actor schema change. Log fields seen so we can adapt.
        keys_seen = sorted(items[0].keys())[:30]
        state.mark_failed("apify", "ig_audio_no_media",
                          f"keys_seen={keys_seen}")
        _record_error("apify_ig", f"no_media_url; keys={keys_seen}")
        print(f"    Apify IG: no media URL found. keys={keys_seen}")
        return ""

    print(f"    Apify IG media URL via field='{src_field}'")
    try:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "audio.mp4")
            with urllib.request.urlopen(video_url, timeout=120) as dl:
                with open(path, "wb") as f:
                    f.write(dl.read())
            if os.path.getsize(path) < 5_000:
                state.mark_failed("apify", "ig_audio_tiny",
                                  f"size<5k via {src_field}")
                _record_error("apify_ig", f"audio_tiny via {src_field}")
                return ""
            return _whisper_transcribe(path)
    except Exception as e:
        msg = _scrub(str(e))
        state.mark_failed("apify", "ig_audio_download", msg)
        _record_error("apify_ig", msg)
        print(f"    Apify IG download failed: {msg[:200]}")
        return ""


def transcribe_instagram(reel_url: str) -> dict:
    """yt-dlp+IG cookies+Whisper -> Apify+Whisper."""
    with tempfile.TemporaryDirectory() as td:
        path = _ytdlp_audio(reel_url, td)
        if path:
            text = _whisper_transcribe(path)
            if text:
                _consume_last_error()
                return {"transcript": text, "source": "ytdlp_ig_whisper", "error": None, "error_trace": None}
    text = _apify_ig_audio(reel_url)
    if text:
        _consume_last_error()
        return {"transcript": text, "source": "apify_ig_whisper", "error": None, "error_trace": None}
    trace = _consume_last_error()
    return {"transcript": "", "source": "", "error": "all_tiers_exhausted",
            "error_trace": trace}


# ── TikTok / generic ─────────────────────────────────────────────────────────

def transcribe_generic(url: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        path = _ytdlp_audio(url, td)
        if path:
            text = _whisper_transcribe(path)
            if text:
                _consume_last_error()
                return {"transcript": text, "source": "ytdlp_whisper", "error": None, "error_trace": None}
    trace = _consume_last_error()
    return {"transcript": "", "source": "", "error": "all_tiers_exhausted",
            "error_trace": trace}


# ── public dispatcher ────────────────────────────────────────────────────────

def transcribe_url(url: str) -> dict:
    """Single entry point. Returns {transcript, source, duration, error, url, platform}."""
    url = url.strip()
    platform = "other"
    result = {"transcript": "", "source": "", "duration": None, "error": None,
              "error_trace": None, "url": url, "platform": platform}

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
    result["error_trace"] = r.get("error_trace")
    if result["transcript"]:
        result["duration"] = _ytdlp_duration(url)
    return result
