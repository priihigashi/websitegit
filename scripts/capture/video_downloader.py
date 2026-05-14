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
from pathlib import Path
from typing import Iterable

DEFAULT_STAGING_ROOT = Path("/tmp/clips")

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
    """Write base64-encoded cookies from env to a file. Returns path if written."""
    blob = os.environ.get(env_var, "")
    if not blob:
        return ""
    try:
        import base64
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(blob))
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
    if not _which("yt-dlp"):
        return {"ok": False, "source_url": url, "local_path": "", "duration_sec": 0.0,
                "title": "", "error": "yt-dlp not installed"}

    staging = Path(staging) if staging else staging_dir_for()
    staging.mkdir(parents=True, exist_ok=True)

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename_hint or "clip")[:60] or "clip"
    out_tpl = str(staging / f"{safe}_%(id)s.%(ext)s")

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

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
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
    proc = subprocess.run(list_cmd, capture_output=True, text=True, timeout=120)
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
