#!/usr/bin/env python3
"""
video_downloader.py
===================
Tiny yt-dlp wrapper for the Video Resource Downloader flow.

Two modes:
  - download_url(url)        Flow A: direct URL from notes. Uses --cookies-from-browser
                             safari locally; falls back to PRI_OP_YT_COOKIES /
                             PRI_OP_IG_COOKIES files in CI.
  - search_youtube(query)    Flow B: ytsearch5:<query>, returns top N candidates
                             (default 3) downloaded to staging dir.

Output: list of dicts with local_path, duration_sec, source_url, title.

Staging dir defaults to /tmp/clips/<story_id>/ but can be overridden.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

DEFAULT_STAGING_ROOT = Path("/tmp/clips")
APIFY_BASE = "https://api.apify.com/v2"
APIFY_API_KEY = os.environ.get("APIFY_API_KEY", "")

# Cookie file paths the capture_pipeline already writes when secrets are set
_YT_COOKIES_FILE = Path(os.environ.get("YT_COOKIES_FILE", "/tmp/yt_cookies.txt"))
_IG_COOKIES_FILE = Path(os.environ.get("IG_COOKIES_FILE", "/tmp/ig_cookies.txt"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _which(cmd: str) -> str:
    path = shutil.which(cmd)
    return path or ""


def _is_youtube(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)", url or "", re.IGNORECASE))


def _is_instagram(url: str) -> bool:
    return "instagram.com" in (url or "").lower()


def _is_tiktok(url: str) -> bool:
    return "tiktok.com" in (url or "").lower()


def _write_env_cookie_file(env_var: str, target: Path) -> str:
    """Write cookies from env to a file. Accepts base64 or raw Netscape text."""
    blob = os.environ.get(env_var, "")
    if not blob:
        return ""
    try:
        import base64
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            decoded = base64.b64decode(blob, validate=True)
            if b"\n" in decoded or b"\t" in decoded:
                target.write_bytes(decoded)
            else:
                target.write_text(blob, encoding="utf-8")
        except Exception:
            target.write_text(blob, encoding="utf-8")
        return str(target)
    except Exception:
        return ""


def _cookie_args(url: str, prefer_browser: bool = True) -> list[str]:
    """Build cookie args for yt-dlp.

    Local dev: try --cookies-from-browser safari (per Priscila's spec).
    CI (no Safari): fall back to cookie files from PRI_OP_YT_COOKIES /
                    PRI_OP_IG_COOKIES env vars.
    """
    # CI mode — env-provided cookies
    if _is_youtube(url):
        if _YT_COOKIES_FILE.exists() or _write_env_cookie_file("PRI_OP_YT_COOKIES", _YT_COOKIES_FILE):
            return ["--cookies", str(_YT_COOKIES_FILE)]
    if _is_instagram(url) or _is_tiktok(url):
        if _IG_COOKIES_FILE.exists() or _write_env_cookie_file("PRI_OP_IG_COOKIES", _IG_COOKIES_FILE):
            return ["--cookies", str(_IG_COOKIES_FILE)]

    # Local mode — Safari cookies (per spec)
    if prefer_browser and sys.platform == "darwin" and not os.environ.get("CI"):
        return ["--cookies-from-browser", "safari"]

    return []


_BOT_PATTERNS = ("403", "sign in", "login required", "cookie", "captcha", "bot detected",
                  "private video", "video unavailable")


def _should_skip_retry(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(pat in low for pat in _BOT_PATTERNS)


def _run_with_retry(
    cmd: list[str],
    *,
    max_attempts: int = 2,
    backoff_sec: int = 5,
    timeout: int | None = None,
) -> "subprocess.CompletedProcess[str]":
    """Run cmd up to max_attempts times. Skip second attempt on bot-detection patterns."""
    for attempt in range(max_attempts):
        kw: dict = {"capture_output": True, "text": True}
        if timeout:
            kw["timeout"] = timeout
        proc = subprocess.run(cmd, **kw)
        if proc.returncode == 0:
            return proc
        if attempt < max_attempts - 1 and not _should_skip_retry(proc.stderr):
            time.sleep(backoff_sec)
            continue
        return proc
    return proc  # unreachable but satisfies type checkers


def _probe_duration(path: str) -> float:
    """Use ffprobe to get duration in seconds. Returns 0.0 on failure."""
    if not _which("ffprobe"):
        return 0.0
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return float(proc.stdout.strip() or 0.0)
    except Exception:
        pass
    return 0.0


def _dump_metadata(url: str, cookie_args: list[str]) -> dict:
    """Best-effort yt-dlp --dump-single-json for duration/title."""
    if not _which("yt-dlp"):
        return {}
    cmd = ["yt-dlp", "--skip-download", "--dump-single-json", "--no-warnings", "--no-playlist"]
    cmd.extend(cookie_args)
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception:
        pass
    return {}


def _download_http_media(media_url: str, target: Path, *, min_bytes: int = 100_000) -> bool:
    """Download a direct media URL to target. Used for Apify CDN results."""
    if not media_url:
        return False
    try:
        import requests
        target.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(
            media_url,
            timeout=180,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as resp:
            resp.raise_for_status()
            with target.open("wb") as fh:
                for chunk in resp.iter_content(1024 * 256):
                    if chunk:
                        fh.write(chunk)
        return target.exists() and target.stat().st_size > min_bytes
    except Exception as exc:
        if target.exists() and target.stat().st_size > min_bytes:
            return True
        print(f"  [video_downloader] direct media download failed: {str(exc)[:180]}")
        return False


def _apify_run_items(actor_id: str, payload: dict, *, limit: int = 1) -> list[dict]:
    """Run an Apify actor and return dataset items. Empty list on any failure."""
    if not APIFY_API_KEY:
        return []
    try:
        import requests
        run_resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            params={"token": APIFY_API_KEY},
            json=payload,
            timeout=30,
        )
        if run_resp.status_code in (402, 403):
            print(f"  [video_downloader] Apify {actor_id} blocked/limited: {run_resp.text[:180]}")
            return []
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]

        status = ""
        for _ in range(18):
            time.sleep(10)
            status_resp = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": APIFY_API_KEY},
                timeout=15,
            )
            status = status_resp.json().get("data", {}).get("status", "")
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
        if status != "SUCCEEDED":
            print(f"  [video_downloader] Apify {actor_id} ended: {status}")
            return []

        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "limit": limit, "format": "json"},
            timeout=30,
        )
        items = items_resp.json()
        return items if isinstance(items, list) else []
    except Exception as exc:
        print(f"  [video_downloader] Apify {actor_id} failed: {str(exc)[:180]}")
        return []


def _extract_media_url(item: dict) -> str:
    """Find direct video/audio URL from the actor schemas already used in the repo."""
    if not isinstance(item, dict):
        return ""
    for key in (
        "downloadedVideo", "videoUrl", "video_url", "videoUrlBackup",
        "videoUrlMain", "media_url", "mediaUrl", "audioUrl", "fileUrl",
        "downloadUrl",
    ):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    sd = item.get("streamingData") or {}
    for group in ("formats", "adaptiveFormats"):
        for fmt in sd.get(group) or []:
            val = fmt.get("url") if isinstance(fmt, dict) else ""
            if isinstance(val, str) and val.startswith("http") and "youtube.com" not in val:
                return val
    return ""


def _extract_image_url(item: dict) -> str:
    """Find a representative image URL for Instagram /p/ posts/carousels."""
    if not isinstance(item, dict):
        return ""
    images = item.get("images") or []
    if isinstance(images, list):
        for img in images:
            if isinstance(img, str) and img.startswith("http"):
                return img
            if isinstance(img, dict):
                val = img.get("url") or img.get("src")
                if isinstance(val, str) and val.startswith("http"):
                    return val
    for key in ("displayUrl", "display_url", "imageUrl", "thumbnailUrl"):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
    return ""


def _download_via_apify_instagram(url: str, staging: Path, filename_hint: str) -> dict:
    """Instagram route copied from capture flow: Apify metadata/videoUrl first."""
    if not APIFY_API_KEY:
        return {"ok": False, "error": "APIFY_API_KEY not set"}
    last_error = ""
    for proxy_groups in (["DATACENTER"], ["RESIDENTIAL"]):
        payload = {
            "directUrls": [url.split("?")[0]],
            "resultsType": "posts",
            "resultsLimit": 1,
            "addParentData": False,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": proxy_groups},
        }
        items = _apify_run_items("apify~instagram-scraper", payload, limit=1)
        if not items:
            last_error = f"Apify Instagram returned no items via {proxy_groups[0]}"
            continue
        item = items[0]
        media_url = _extract_media_url(item)
        if media_url:
            target = staging / f"{filename_hint}_apify.mp4"
            if _download_http_media(media_url, target):
                return {
                    "ok": True,
                    "source_url": url,
                    "local_path": str(target),
                    "duration_sec": _probe_duration(str(target)),
                    "title": (item.get("caption") or "")[:120],
                    "media_kind": "video",
                    "error": "",
                }
            last_error = f"Apify Instagram media download failed via {proxy_groups[0]}"
            continue
        image_url = _extract_image_url(item)
        if image_url:
            target = staging / f"{filename_hint}_apify.jpg"
            if _download_http_media(image_url, target, min_bytes=10_000):
                return {
                    "ok": True,
                    "source_url": url,
                    "local_path": str(target),
                    "duration_sec": 0.0,
                    "title": (item.get("caption") or "")[:120],
                    "media_kind": "image",
                    "error": "",
                }
            last_error = f"Apify Instagram image download failed via {proxy_groups[0]}"
            continue
        keys = ",".join(sorted(item.keys())[:24])
        last_error = f"Apify Instagram returned no direct media/image URL via {proxy_groups[0]} (keys={keys})"
    return {"ok": False, "error": last_error or "Apify Instagram returned no media"}


def _download_via_apify_youtube(url: str, staging: Path, filename_hint: str) -> dict:
    """YouTube fallback route from youtube_research.py: Apify proxy → media URL."""
    if not APIFY_API_KEY:
        return {"ok": False, "error": "APIFY_API_KEY not set"}
    payload = {"startUrls": [{"url": url}], "maxItems": 1}
    items = _apify_run_items("apidojo~youtube-scraper", payload, limit=1)
    if not items:
        return {"ok": False, "error": "Apify YouTube returned no items"}
    media_url = _extract_media_url(items[0])
    if not media_url:
        return {"ok": False, "error": "Apify YouTube returned no direct media URL"}
    target = staging / f"{filename_hint}_apify.mp4"
    if not _download_http_media(media_url, target):
        return {"ok": False, "error": "Apify YouTube media download failed"}
    return {
        "ok": True,
        "source_url": url,
        "local_path": str(target),
        "duration_sec": _probe_duration(str(target)),
        "title": items[0].get("title", "") or "",
        "error": "",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def staging_dir_for(story_id: str = "", subdir: str = "") -> Path:
    """Return /tmp/clips/<story_id>/<subdir>/ — created if missing."""
    parts = [DEFAULT_STAGING_ROOT]
    if story_id:
        parts.append(re.sub(r"[^A-Za-z0-9._-]+", "_", story_id))
    if subdir:
        parts.append(re.sub(r"[^A-Za-z0-9._-]+", "_", subdir))
    d = Path(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_url(
    url: str,
    *,
    staging: Path | str | None = None,
    filename_hint: str = "",
    max_duration_sec: int = 600,
) -> dict:
    """Flow A: Download a single URL with yt-dlp.

    Returns a dict:
        {
          "ok": bool,
          "source_url": str,
          "local_path": str,
          "duration_sec": float,
          "title": str,
          "error": str,
        }
    """
    staging = Path(staging) if staging else staging_dir_for()
    staging.mkdir(parents=True, exist_ok=True)

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename_hint or "clip")[:60] or "clip"
    out_tpl = str(staging / f"{safe}_%(id)s.%(ext)s")

    # Instagram is already handled successfully elsewhere in the capture system
    # through Apify videoUrl/CDN routes. Use that first so this flow does not
    # depend on manually refreshed browser cookies.
    if _is_instagram(url):
        apify_res = _download_via_apify_instagram(url, staging, safe)
        if apify_res.get("ok"):
            return apify_res
        print(f"  [video_downloader] Apify Instagram fallback missed: {apify_res.get('error','')}")

    if not _which("yt-dlp"):
        return {"ok": False, "source_url": url, "local_path": "", "duration_sec": 0.0,
                "title": "", "error": "yt-dlp not installed"}

    cookie_args = _cookie_args(url)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "--format", "mp4/bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "-o", out_tpl,
    ]
    if max_duration_sec:
        # Filter out clips that exceed the cap (no point downloading hour-long lectures)
        cmd.extend(["--match-filter", f"duration <= {max_duration_sec}"])
    cmd.extend(cookie_args)
    cmd.append(url)

    proc = _run_with_retry(cmd, max_attempts=2, backoff_sec=5)
    if proc.returncode != 0:
        if _is_youtube(url):
            apify_res = _download_via_apify_youtube(url, staging, safe)
            if apify_res.get("ok"):
                return apify_res
        return {"ok": False, "source_url": url, "local_path": "", "duration_sec": 0.0,
                "title": "", "error": (proc.stderr or "")[:400]}

    # Find the newest mp4 in staging dir matching our prefix
    candidates = sorted(
        [p for p in staging.glob(f"{safe}_*") if p.suffix.lower() in (".mp4", ".mkv", ".webm")],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not candidates:
        return {"ok": False, "source_url": url, "local_path": "", "duration_sec": 0.0,
                "title": "", "error": "yt-dlp completed but no output file found"}

    local_path = candidates[0]
    duration = _probe_duration(str(local_path))
    if duration == 0.0:
        md = _dump_metadata(url, cookie_args)
        duration = float(md.get("duration", 0) or 0)
        title = md.get("title", "") or ""
    else:
        title = ""

    return {
        "ok": True,
        "source_url": url,
        "local_path": str(local_path),
        "duration_sec": duration,
        "title": title,
        "error": "",
    }


def search_youtube(
    query: str,
    *,
    n_results: int = 3,
    search_size: int = 5,
    staging: Path | str | None = None,
    max_duration_sec: int = 600,
) -> list[dict]:
    """Flow B: ytsearch<search_size>:<query>, download top n_results.

    Returns a list of per-clip dicts (same shape as download_url).
    """
    if not _which("yt-dlp"):
        return [{"ok": False, "source_url": "", "local_path": "", "duration_sec": 0.0,
                 "title": "", "error": "yt-dlp not installed"}]

    if not query or not query.strip():
        return [{"ok": False, "source_url": "", "local_path": "", "duration_sec": 0.0,
                 "title": "", "error": "empty query"}]

    staging = Path(staging) if staging else staging_dir_for(subdir="search")
    staging.mkdir(parents=True, exist_ok=True)

    # Step 1 — list candidate URLs (cheap, no download)
    search_term = f"ytsearch{max(search_size, n_results)}:{query.strip()}"
    list_cmd = [
        "yt-dlp", "--skip-download", "--no-warnings", "--quiet",
        "--print", "%(webpage_url)s\t%(title)s\t%(duration)s",
        "--match-filter", f"duration <= {max_duration_sec}" if max_duration_sec else "duration > 0",
        search_term,
    ]
    proc = _run_with_retry(list_cmd, max_attempts=2, backoff_sec=5, timeout=120)
    if proc.returncode != 0:
        return [{"ok": False, "source_url": "", "local_path": "", "duration_sec": 0.0,
                 "title": "", "error": (proc.stderr or "")[:400]}]

    candidates = []
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 1 and parts[0].startswith("http"):
            candidates.append({
                "url": parts[0],
                "title": parts[1] if len(parts) > 1 else "",
                "duration": float(parts[2]) if (len(parts) > 2 and parts[2] not in ("", "NA")) else 0.0,
            })
        if len(candidates) >= n_results:
            break

    if not candidates:
        return [{"ok": False, "source_url": "", "local_path": "", "duration_sec": 0.0,
                 "title": "", "error": "no candidates returned by ytsearch"}]

    # Step 2 — download each
    results: list[dict] = []
    for idx, cand in enumerate(candidates, start=1):
        hint = f"candidate_{idx:02d}"
        out = download_url(
            cand["url"],
            staging=staging,
            filename_hint=hint,
            max_duration_sec=max_duration_sec,
        )
        if not out.get("title") and cand.get("title"):
            out["title"] = cand["title"]
        if not out.get("duration_sec") and cand.get("duration"):
            out["duration_sec"] = cand["duration"]
        results.append(out)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="", help="Flow A: direct URL")
    p.add_argument("--query", default="", help="Flow B: ytsearch query")
    p.add_argument("--n", type=int, default=3, help="Flow B: number of candidates")
    p.add_argument("--staging", default="", help="Staging dir override")
    p.add_argument("--story-id", default="", help="Story id for staging path")
    p.add_argument("--max-duration", type=int, default=600)
    args = p.parse_args()

    staging = Path(args.staging) if args.staging else staging_dir_for(args.story_id)

    if args.url:
        result = download_url(args.url, staging=staging, max_duration_sec=args.max_duration)
        print(json.dumps(result, indent=2))
    elif args.query:
        results = search_youtube(args.query, n_results=args.n, staging=staging,
                                 max_duration_sec=args.max_duration)
        print(json.dumps(results, indent=2))
    else:
        p.error("Provide --url or --query")
