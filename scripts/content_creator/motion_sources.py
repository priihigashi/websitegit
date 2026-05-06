"""motion_sources.py — Unified video-clip source chain with deep fallback cascade.

Goal (Priscila, 2026-04-20): "so many fallbacks that something will go through."

Public API:
    fetch_clip_with_fallback(slide_cfg, work_dir, filename, visual_hint="context-image")
        → absolute path (str) or "" if every tier missed.

    SOURCE_CHAIN — ordered list of tier functions, for observability.

Each tier:
    • Accepts (slide_cfg: dict, dest_path: Path)
    • Returns True on success (clip written to dest_path), False on miss
    • NEVER raises — all exceptions are caught and logged one-line
    • Writes a companion `<dest>.source.txt` on success with attribution metadata

Slide config keys consumed (all optional, youtube_query used as universal fallback):
    youtube_query, instagram_query, pexels_query, pixabay_query, archive_query,
    wikimedia_query, visual_hint

The caller (carousel_builder.fetch_clips) decides which slides get a clip,
what filename to use, and which tiers to skip for stock-only slots.

See MOTION_SOURCES_RESEARCH.md for full schema + failure behaviour spec.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

APIFY_KEY = os.environ.get("APIFY_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")  # optional — tier skips if unset
GIPHY_KEY = os.environ.get("GIPHY_API_KEY", "")      # optional — tier skips if unset
SHEETS_TOKEN_RAW = os.environ.get("SHEETS_TOKEN", "")
CLIP_COLLECTIONS_SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")

MIN_CLIP_BYTES = 10_000          # reject anything smaller as a broken stub
MAX_CLIP_BYTES = 50 * 1024 * 1024  # 50MB hard cap — don't pull full-length films
MAX_CLIP_DURATION_S = 300        # 5 min max — skip feature films / long lectures


def _write_sidecar(dest_path: Path, source_tier: str, source_url: str,
                   license_str: str = "", attribution: str = "",
                   query: str = "") -> None:
    """Write `<dest>.source.txt` next to the clip for attribution bookkeeping."""
    try:
        sidecar = dest_path.with_suffix(dest_path.suffix + ".source.txt")
        sidecar.write_text(
            f"tier={source_tier}\n"
            f"source_url={source_url}\n"
            f"license={license_str}\n"
            f"attribution={attribution}\n"
            f"query={query}\n"
            f"fetched_at={int(time.time())}\n"
        )
    except Exception:
        pass  # sidecar is nice-to-have, never block the clip


_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def _http_get_bytes(url: str, timeout: int = 60, max_bytes: int = MAX_CLIP_BYTES) -> bytes:
    """GET a URL, return bytes, enforce size cap. Empty bytes on miss."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Peek content-length if available
            cl = resp.headers.get("Content-Length", "")
            if cl.isdigit() and int(cl) > max_bytes:
                return b""
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return b""
            return raw
    except Exception:
        return b""


# ─── TIER 0 — Pre-loaded Clip Collections (video-research URLs) ──────────────

def _get_oauth_token() -> str:
    """Refresh SHEETS_TOKEN to get a short-lived access token."""
    if not SHEETS_TOKEN_RAW:
        return ""
    try:
        import urllib.parse as _up
        td = json.loads(SHEETS_TOKEN_RAW)
        data = _up.urlencode({
            "client_id": td["client_id"], "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
        return resp.get("access_token", "")
    except Exception:
        return ""


def tier_clip_collections(slide_cfg: dict, dest_path: Path) -> bool:
    """Tier 0 — Read pre-loaded clip URLs from Clip Collections tab (written by video-research.yml).
    Tries to download each URL via yt-dlp (Apify residential proxy bypass) or direct HTTP.
    Highest priority: these clips were already validated as high-relevance by Claude analysis."""
    topic = slide_cfg.get("topic") or slide_cfg.get("youtube_query") or ""
    if not topic or not SHEETS_TOKEN_RAW:
        return False
    try:
        token = _get_oauth_token()
        if not token:
            return False
        enc = urllib.parse.quote("'📋 Clip Collections'", safe="!:'")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{CLIP_COLLECTIONS_SHEET_ID}/values/{enc}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        rows = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("values", [])
        if len(rows) < 2:
            return False
        header = [h.strip().lower() for h in rows[0]]
        topic_col = next((i for i, h in enumerate(header) if h in ("topic", "topic / title", "title")), None)
        url_col = next((i for i, h in enumerate(header) if h == "url"), None)
        if topic_col is None or url_col is None:
            return False
        topic_lower = topic.strip().lower()
        candidates = []
        for row in rows[1:]:
            cell = row[topic_col].strip().lower() if topic_col < len(row) else ""
            clip_url = row[url_col].strip() if url_col < len(row) else ""
            if cell and clip_url and (cell in topic_lower or topic_lower in cell):
                candidates.append(clip_url)
        if not candidates:
            return False
        for clip_url in candidates[:3]:
            if not clip_url:
                continue
            # Try yt-dlp download of the specific URL (works for YouTube when Apify is active)
            if "youtube.com" in clip_url or "youtu.be" in clip_url:
                if shutil.which("yt-dlp"):
                    tmp_out = dest_path.parent / (dest_path.stem + ".cc.%(ext)s")
                    r = subprocess.run(
                        ["yt-dlp", "--no-warnings", "--no-playlist",
                         "--format", "mp4[height<=720]/best[height<=720]/best",
                         "--max-downloads", "1",
                         "--match-filter", f"duration < {MAX_CLIP_DURATION_S}",
                         "--output", str(tmp_out), clip_url],
                        capture_output=True, timeout=90
                    )
                    produced = sorted(dest_path.parent.glob(dest_path.stem + ".cc.*"))
                    if produced and produced[0].stat().st_size > MIN_CLIP_BYTES:
                        dest_path.write_bytes(produced[0].read_bytes())
                        produced[0].unlink(missing_ok=True)
                        _write_sidecar(dest_path, "clip_collections", clip_url,
                                       license_str="YouTube ToS (fair use editorial)",
                                       attribution=f"Pre-loaded via video-research — {clip_url}",
                                       query=topic)
                        print(f"  motion_sources: Clip Collections → {dest_path.name} ({dest_path.stat().st_size//1024}KB)")
                        return True
            else:
                # Direct download (Pexels/Pixabay/Archive direct MP4 links)
                raw = _http_get_bytes(clip_url, timeout=60)
                if len(raw) > MIN_CLIP_BYTES:
                    dest_path.write_bytes(raw)
                    _write_sidecar(dest_path, "clip_collections", clip_url,
                                   license_str="See source",
                                   attribution=f"Pre-loaded via video-research — {clip_url}",
                                   query=topic)
                    print(f"  motion_sources: Clip Collections (direct) → {dest_path.name} ({len(raw)//1024}KB)")
                    return True
        return False
    except Exception as e:
        print(f"  motion_sources: Clip Collections tier error (non-fatal): {e}")
        return False


# ─── FREE YouTube downloader fallbacks (no extra API key) ────────────────────

def _try_ytdlp_download(dest_path: Path, query: str, extra_args: Optional[list] = None) -> bool:
    """Use yt-dlp search+download for one query, then normalize to dest_path."""
    if not query:
        return False
    if shutil.which("yt-dlp") is None:
        return False
    tmp_out = dest_path.parent / (dest_path.stem + ".ytdlp.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--format", "mp4[height<=720]/best[height<=720]/best",
        "--max-downloads", "1",
        "--match-filter", f"duration < {MAX_CLIP_DURATION_S}",
        "--output", str(tmp_out),
        f"ytsearch1:{query}",
    ]
    if extra_args:
        cmd.extend(extra_args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            return False
        produced = sorted(dest_path.parent.glob(dest_path.stem + ".ytdlp.*"))
        if not produced:
            return False
        src = produced[0]
        raw = src.read_bytes()
        if len(raw) < MIN_CLIP_BYTES:
            src.unlink(missing_ok=True)
            return False
        dest_path.write_bytes(raw)
        src.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def tier_ytdlp_search(slide_cfg: dict, dest_path: Path) -> bool:
    """Free fallback: yt-dlp query search + direct download."""
    query = slide_cfg.get("youtube_query") or slide_cfg.get("query") or ""
    if not query:
        return False
    ok = _try_ytdlp_download(dest_path, query)
    if not ok:
        print(f"  motion_sources: yt-dlp search miss for '{query[:40]}'")
        return False
    _write_sidecar(
        dest_path,
        "ytdlp_search",
        f"ytsearch1:{query}",
        license_str="YouTube ToS (fair use editorial)",
        attribution=f"via yt-dlp search — {query}",
        query=query,
    )
    print(f"  motion_sources: yt-dlp search → {dest_path.name} ({dest_path.stat().st_size//1024}KB)")
    return True


def tier_ytdlp_ios(slide_cfg: dict, dest_path: Path) -> bool:
    """Free fallback: yt-dlp with iOS client args (sometimes bypasses blocks)."""
    query = slide_cfg.get("youtube_query") or slide_cfg.get("query") or ""
    if not query:
        return False
    ok = _try_ytdlp_download(
        dest_path,
        query,
        ["--extractor-args", "youtube:player_client=ios,web_creator"],
    )
    if not ok:
        print(f"  motion_sources: yt-dlp iOS miss for '{query[:40]}'")
        return False
    _write_sidecar(
        dest_path,
        "ytdlp_ios",
        f"ytsearch1:{query}",
        license_str="YouTube ToS (fair use editorial)",
        attribution=f"via yt-dlp iOS client — {query}",
        query=query,
    )
    print(f"  motion_sources: yt-dlp iOS → {dest_path.name} ({dest_path.stat().st_size//1024}KB)")
    return True


# ─── TIER 1 — Apify YouTube ───────────────────────────────────────────────────

def _apify_run(actor_id: str, input_body: dict, wait: int = 120) -> list:
    """Synchronous Apify actor run. Returns dataset items list or []."""
    if not APIFY_KEY:
        return []
    try:
        actor_slug = actor_id.replace("/", "~")
        run_url = f"https://api.apify.com/v2/acts/{actor_slug}/runs?token={APIFY_KEY}&waitForFinish={wait}"
        body = json.dumps(input_body).encode()
        req = urllib.request.Request(
            run_url, data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=wait + 30).read())
        run_data = resp.get("data", {})
        if run_data.get("status") != "SUCCEEDED":
            return []
        dataset_id = run_data.get("defaultDatasetId", "")
        if not dataset_id:
            return []
        items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_KEY}"
        items = json.loads(urllib.request.urlopen(items_url, timeout=30).read())
        return items if isinstance(items, list) else []
    except Exception as e:
        print(f"  motion_sources: Apify {actor_id} error: {e}")
        return []


def _apify_yt_search(query: str) -> str:
    """Search YouTube via Apify streamers~youtube-scraper. Returns watch URL or ''."""
    # streamers~youtube-scraper is the verified-working actor (exists, not 404/403).
    # It accepts startUrls (YouTube search result pages) — NOT searchTerms field.
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
    for input_body in [
        # Primary: startUrls format (YouTube search results page)
        {"startUrls": [{"url": search_url}], "maxResults": 5,
         "downloadSubtitles": False, "downloadComments": False},
        # Fallback: searchTerms format in case actor supports it
        {"searchTerms": [query], "maxResults": 5},
    ]:
        items = _apify_run("streamers~youtube-scraper", input_body, wait=120)
        if not items:
            continue
        for item in items:
            vid_id = item.get("id") or item.get("videoId") or ""
            url = (item.get("url") or item.get("videoUrl")
                   or (vid_id and f"https://www.youtube.com/watch?v={vid_id}") or "")
            if url and "youtube.com/watch" in url:
                return url
    return ""


def tier_apify_youtube(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #1 — Apify YouTube scraper + yt-dlp download (residential proxy bypass)."""
    if not APIFY_KEY:
        return False
    query = slide_cfg.get("youtube_query") or slide_cfg.get("query") or ""
    if not query:
        return False
    try:
        video_url = _apify_yt_search(query)
        if not video_url:
            print(f"  motion_sources: Apify YouTube search miss for '{query[:40]}'")
            return False

        # Path A: yt-dlp on direct URL (sometimes bypasses GHA block on specific URLs)
        if shutil.which("yt-dlp"):
            tmp_out = dest_path.parent / (dest_path.stem + ".apyt.%(ext)s")
            subprocess.run(
                ["yt-dlp", "--no-warnings", "--no-playlist",
                 "--format", "mp4[height<=720]/best[height<=720]/best",
                 "--max-downloads", "1",
                 "--match-filter", f"duration < {MAX_CLIP_DURATION_S}",
                 "--output", str(tmp_out), video_url],
                capture_output=True, timeout=90
            )
            produced = sorted(dest_path.parent.glob(dest_path.stem + ".apyt.*"))
            if produced and produced[0].stat().st_size > MIN_CLIP_BYTES:
                dest_path.write_bytes(produced[0].read_bytes())
                produced[0].unlink(missing_ok=True)
                _write_sidecar(dest_path, "apify_youtube", video_url,
                               license_str="YouTube ToS (fair use editorial)",
                               attribution=f"via YouTube — {video_url}", query=query)
                print(f"  motion_sources: Apify+yt-dlp → {dest_path.name} ({dest_path.stat().st_size//1024}KB)")
                return True

        # Path B: pytubefix — extracts direct CDN stream URL, CDN often not IP-restricted
        try:
            from pytubefix import YouTube as _YT
            yt = _YT(video_url, use_oauth=False)
            stream = yt.streams.filter(file_extension="mp4", progressive=True).order_by("resolution").last()
            if not stream:
                stream = yt.streams.filter(file_extension="mp4").order_by("resolution").first()
            if stream and stream.url:
                raw = _http_get_bytes(stream.url, timeout=120)
                if len(raw) > MIN_CLIP_BYTES:
                    dest_path.write_bytes(raw)
                    _write_sidecar(dest_path, "apify_youtube", video_url,
                                   license_str="YouTube ToS (fair use editorial)",
                                   attribution=f"via YouTube — {video_url}", query=query)
                    print(f"  motion_sources: Apify+pytubefix → {dest_path.name} ({len(raw)//1024}KB)")
                    return True
                print(f"  motion_sources: pytubefix CDN returned {len(raw)}B for '{video_url[:50]}'")
        except Exception as _pte:
            print(f"  motion_sources: pytubefix error ({video_url[:50]}): {_pte}")

        print(f"  motion_sources: Apify YouTube found URL but download failed: '{video_url[:60]}'")
        return False
    except Exception as e:
        print(f"  motion_sources: Apify YouTube error ({query[:40]}): {e}")
        return False


# ─── TIER 2 — Apify Instagram ─────────────────────────────────────────────────

def tier_apify_instagram(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #2 — Apify Instagram scraper. Creator reels + influencer video."""
    if not APIFY_KEY:
        return False
    query = (slide_cfg.get("instagram_query")
             or slide_cfg.get("youtube_query")
             or slide_cfg.get("query") or "")
    if not query:
        return False
    try:
        # Hashtag search path — cheapest + most predictable.
        # resultsType=reels so we get videoUrl directly.
        items = _apify_run(
            "apify~instagram-scraper",
            {"search": query, "searchType": "hashtag", "searchLimit": 1,
             "resultsType": "reels", "resultsLimit": 3},
            wait=120
        )
        video_url = ""
        post_url = ""
        for it in items:
            video_url = it.get("videoUrl") or ""
            post_url = it.get("url") or ""
            if video_url:
                break
        if not video_url:
            print(f"  motion_sources: Apify Instagram miss for '{query[:40]}'")
            return False

        raw = _http_get_bytes(video_url, timeout=60)
        if len(raw) < MIN_CLIP_BYTES:
            return False
        dest_path.write_bytes(raw)
        _write_sidecar(dest_path, "apify_instagram", post_url or video_url,
                       license_str="Instagram ToS (fair use editorial)",
                       attribution=f"via Instagram — {post_url}", query=query)
        print(f"  motion_sources: Apify Instagram → {dest_path.name} ({len(raw)//1024}KB)")
        return True
    except Exception as e:
        print(f"  motion_sources: Apify Instagram error ({query[:40]}): {e}")
        return False


# ─── TIER 3 — Pexels Videos ───────────────────────────────────────────────────

def tier_pexels(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #3 — Pexels stock videos. Places, events, institutions only."""
    if not PEXELS_KEY:
        return False
    query = (slide_cfg.get("pexels_query")
             or slide_cfg.get("youtube_query")
             or slide_cfg.get("query") or "")
    if not query:
        return False
    try:
        q = urllib.parse.urlencode({
            "query": query, "per_page": "5", "size": "medium", "orientation": "portrait"
        })
        req = urllib.request.Request(
            f"https://api.pexels.com/videos/search?{q}",
            headers={"Authorization": PEXELS_KEY}  # NOT Bearer
        )
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        videos = data.get("videos", [])
        if not videos:
            print(f"  motion_sources: Pexels miss for '{query[:40]}'")
            return False
        for v in videos:
            page_url = v.get("url", "")
            photographer = v.get("user", {}).get("name", "Pexels")
            # Sort: prefer portrait (height >= width), then accept landscape — clip uses object-fit:cover
            files = sorted(v.get("video_files", []), key=lambda x: (x.get("height", 0) < x.get("width", 1), x.get("width", 0)))
            for vf in files:
                if vf.get("file_type") != "video/mp4":
                    continue
                url = vf.get("link", "")
                if not url:
                    continue
                raw = _http_get_bytes(url, timeout=60)
                if len(raw) < MIN_CLIP_BYTES:
                    continue
                dest_path.write_bytes(raw)
                _write_sidecar(dest_path, "pexels", page_url or url,
                               license_str="Pexels License (free use)",
                               attribution=f"Video by {photographer} on Pexels",
                               query=query)
                print(f"  motion_sources: Pexels → {dest_path.name} ({len(raw)//1024}KB)")
                return True
        print(f"  motion_sources: Pexels no downloadable file for '{query[:40]}'")
        return False
    except Exception as e:
        print(f"  motion_sources: Pexels error ({query[:40]}): {e}")
        return False


# ─── TIER 4 — Pixabay Videos ──────────────────────────────────────────────────

def tier_pixabay(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #4 — Pixabay stock. Sibling to Pexels, different library."""
    if not PIXABAY_KEY:
        return False
    query = (slide_cfg.get("pixabay_query")
             or slide_cfg.get("pexels_query")
             or slide_cfg.get("youtube_query")
             or slide_cfg.get("query") or "")
    if not query:
        return False
    try:
        q = urllib.parse.urlencode({
            "key": PIXABAY_KEY, "q": query, "per_page": "3", "video_type": "film"
        })
        url = f"https://pixabay.com/api/videos/?{q}"
        data = json.loads(urllib.request.urlopen(url, timeout=15).read())
        hits = data.get("hits", [])
        if not hits:
            print(f"  motion_sources: Pixabay miss for '{query[:40]}'")
            return False
        for h in hits:
            videos = h.get("videos", {})
            # Try portrait sizes first (better fit for 9:16 slides), then landscape — clip uses object-fit:cover
            for size_key in ("medium", "small", "large", "tiny"):
                vf = videos.get(size_key, {})
                link = vf.get("url", "")
                if not link:
                    continue
                raw = _http_get_bytes(link, timeout=60)
                if len(raw) < MIN_CLIP_BYTES:
                    print(f"  motion_sources: Pixabay {size_key} too small ({len(raw)}B) url={link[:60]}")
                    continue
                dest_path.write_bytes(raw)
                page_url = h.get("pageURL", link)
                user = h.get("user", "Pixabay")
                _write_sidecar(dest_path, "pixabay", page_url,
                               license_str="Pixabay Content License",
                               attribution=f"Video by {user} on Pixabay",
                               query=query)
                print(f"  motion_sources: Pixabay → {dest_path.name} ({len(raw)//1024}KB)")
                return True
        print(f"  motion_sources: Pixabay no downloadable file for '{query[:40]}'")
        return False
    except Exception as e:
        print(f"  motion_sources: Pixabay error ({query[:40]}): {e}")
        return False


# ─── TIER 5 — Archive.org ─────────────────────────────────────────────────────

def tier_archive_org(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #5 — Archive.org public-domain + CC video. Historical + news reels."""
    query = (slide_cfg.get("archive_query")
             or slide_cfg.get("youtube_query")
             or slide_cfg.get("query") or "")
    if not query:
        return False
    try:
        q = urllib.parse.urlencode({
            "q": f"{query} AND mediatype:movies",
            "fl[]": "identifier",
            "rows": "5",
            "sort[]": "downloads desc",
            "output": "json",
        }, doseq=True)
        search_url = f"https://archive.org/advancedsearch.php?{q}"
        data = json.loads(urllib.request.urlopen(search_url, timeout=15).read())
        docs = data.get("response", {}).get("docs", [])
        if not docs:
            print(f"  motion_sources: Archive.org miss for '{query[:40]}'")
            return False

        for doc in docs:
            identifier = doc.get("identifier", "")
            if not identifier:
                continue
            meta_url = f"https://archive.org/metadata/{identifier}"
            meta = json.loads(urllib.request.urlopen(meta_url, timeout=15).read())
            files = meta.get("files", [])
            # Pick first reasonable MP4 under the size cap
            mp4_file = None
            for f in files:
                name = f.get("name", "")
                fmt = f.get("format", "")
                size = int(f.get("size", "0") or "0")
                if name.lower().endswith(".mp4") and 0 < size < MAX_CLIP_BYTES:
                    mp4_file = f
                    break
                if "MPEG4" in fmt and 0 < size < MAX_CLIP_BYTES:
                    mp4_file = f
                    break
            if not mp4_file:
                continue
            dl_url = f"https://archive.org/download/{identifier}/{mp4_file['name']}"
            raw = _http_get_bytes(dl_url, timeout=120)
            if len(raw) < MIN_CLIP_BYTES:
                continue
            dest_path.write_bytes(raw)
            page_url = f"https://archive.org/details/{identifier}"
            _write_sidecar(dest_path, "archive_org", page_url,
                           license_str="Public domain or CC (see Archive.org item)",
                           attribution=f"via Internet Archive — {page_url}",
                           query=query)
            print(f"  motion_sources: Archive.org → {dest_path.name} ({len(raw)//1024}KB)")
            return True
        return False
    except Exception as e:
        print(f"  motion_sources: Archive.org error ({query[:40]}): {e}")
        return False


# ─── TIER 6 — Wikimedia Commons ───────────────────────────────────────────────

def tier_wikimedia(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #6 — Wikimedia Commons CC video. Historical / archival."""
    query = (slide_cfg.get("wikimedia_query")
             or slide_cfg.get("archive_query")
             or slide_cfg.get("youtube_query")
             or slide_cfg.get("query") or "")
    if not query:
        return False
    try:
        q = urllib.parse.urlencode({
            "action": "query",
            "generator": "search",
            "gsrsearch": f"{query} filetype:video",
            "gsrnamespace": "6",
            "gsrlimit": "5",
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
            "format": "json",
        })
        url = f"https://commons.wikimedia.org/w/api.php?{q}"
        req = urllib.request.Request(url, headers={"User-Agent": "oak-park-content-bot/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            print(f"  motion_sources: Wikimedia miss for '{query[:40]}'")
            return False
        for page in pages.values():
            infos = page.get("imageinfo", [])
            if not infos:
                continue
            info = infos[0]
            media_url = info.get("url", "")
            mime = info.get("mime", "")
            size = int(info.get("size", 0) or 0)
            if not media_url or size > MAX_CLIP_BYTES:
                continue
            if not any(x in mime for x in ("video/mp4", "video/webm", "video/ogg")):
                continue
            raw = _http_get_bytes(media_url, timeout=120)
            if len(raw) < MIN_CLIP_BYTES:
                continue
            # Note: webm/ogg may need ffmpeg transcode by caller.
            dest_path.write_bytes(raw)
            _write_sidecar(dest_path, "wikimedia", media_url,
                           license_str="Wikimedia Commons (see source page)",
                           attribution=f"via Wikimedia Commons — {media_url}",
                           query=query)
            print(f"  motion_sources: Wikimedia → {dest_path.name} ({len(raw)//1024}KB, {mime})")
            return True
        return False
    except Exception as e:
        print(f"  motion_sources: Wikimedia error ({query[:40]}): {e}")
        return False


# ─── TIER 7 — Free Stock Scrapers (Mixkit / Coverr / Videvo) ──────────────────

def tier_stock_scrapers(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #7 — free stock sites without official APIs.

    PLACEHOLDER: mixkit.co / coverr.co / videvo.net. These sites expose og:video
    meta tags on page URLs; a generic Apify web-scraper actor can extract them.
    Implementation deferred — returns False silently so Ken Burns kicks in.
    Wire here when Priscila authorizes the scraper cost.
    """
    query = (slide_cfg.get("query")
             or slide_cfg.get("youtube_query")
             or slide_cfg.get("pexels_query")
             or "")
    if query:
        print(f"  motion_sources: stock_scrapers tier disabled for '{query[:40]}'")
    return False


# ─── TIER 7b — GIPHY (silent skip if no key) ──────────────────────────────────

def tier_giphy(slide_cfg: dict, dest_path: Path) -> bool:
    """Optional tier: GIPHY search → download GIF → convert to MP4 via ffmpeg.
    Skips silently if GIPHY_API_KEY is unset. Good for reaction/UGC slides."""
    if not GIPHY_KEY:
        return False
    query = (
        slide_cfg.get("pexels_query")
        or slide_cfg.get("youtube_query")
        or slide_cfg.get("query") or ""
    )
    if not query:
        return False
    try:
        q = urllib.parse.urlencode({"api_key": GIPHY_KEY, "q": query, "limit": "3", "rating": "g"})
        url = f"https://api.giphy.com/v1/gifs/search?{q}"
        data = json.loads(urllib.request.urlopen(url, timeout=10).read())
        gifs = data.get("data", [])
        if not gifs:
            print(f"  motion_sources: GIPHY miss for '{query[:40]}'")
            return False
        for gif in gifs:
            gif_url = gif.get("images", {}).get("original", {}).get("url", "")
            if not gif_url:
                continue
            raw = _http_get_bytes(gif_url, timeout=30)
            if len(raw) < MIN_CLIP_BYTES:
                continue
            gif_path = dest_path.with_suffix(".gif")
            gif_path.write_bytes(raw)
            # Convert GIF → MP4 if ffmpeg available
            if shutil.which("ffmpeg"):
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(gif_path),
                     "-movflags", "+faststart", "-pix_fmt", "yuv420p",
                     "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                     str(dest_path)],
                    capture_output=True, timeout=30,
                )
                gif_path.unlink(missing_ok=True)
                if result.returncode != 0 or not dest_path.exists():
                    continue
            else:
                # No ffmpeg — keep GIF at dest_path directly
                shutil.move(str(gif_path), str(dest_path))
            if dest_path.stat().st_size < MIN_CLIP_BYTES:
                continue
            page_url = gif.get("url", gif_url)
            _write_sidecar(dest_path, "giphy", page_url,
                           license_str="GIPHY (attribution required)",
                           attribution=f"via GIPHY — {page_url}",
                           query=query)
            print(f"  motion_sources: GIPHY → {dest_path.name} ({dest_path.stat().st_size//1024}KB)")
            return True
        print(f"  motion_sources: GIPHY no downloadable GIF for '{query[:40]}'")
        return False
    except Exception as e:
        print(f"  motion_sources: GIPHY error ({query[:40]}): {e}")
        return False


# ─── Whisper + ffmpeg trim ────────────────────────────────────────────────────

def _trim_to_relevant_window(clip_path: Path, slide_cfg: dict,
                             target_duration: float = 4.0) -> Path:
    """Trim a fetched clip to target_duration seconds around the most relevant keyword.

    Flow:
      1. ffprobe: skip if clip already ≤ target+1 s
      2. Whisper (tiny model): locate keyword timestamp in audio
      3. ffmpeg: seek to start_time, copy target_duration seconds
    Non-fatal at every step — always returns a usable path.
    """
    if not shutil.which("ffmpeg"):
        return clip_path
    try:
        # 1. Probe duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(clip_path)],
            capture_output=True, text=True, timeout=10
        )
        if probe.returncode != 0:
            return clip_path
        duration = float(json.loads(probe.stdout).get("format", {}).get("duration", 0))
        if duration <= target_duration + 1:
            return clip_path  # already short enough — skip trim

        # 2. Derive keyword from query
        query = slide_cfg.get("youtube_query") or slide_cfg.get("query") or ""
        stopwords = {"from", "with", "that", "this", "have", "what", "when",
                     "where", "about", "brasil", "brazil", "united", "states"}
        words = [w for w in query.lower().split() if len(w) > 3 and w not in stopwords]
        keyword = words[0] if words else ""

        start_time = 0.0  # default: start of clip

        # 3. Whisper keyword detection
        if keyword and shutil.which("whisper"):
            import tempfile
            audio_tmp = Path(tempfile.mktemp(suffix=".wav"))
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(clip_path),
                     "-ar", "16000", "-ac", "1", "-vn", str(audio_tmp)],
                    capture_output=True, timeout=60
                )
                if audio_tmp.exists() and audio_tmp.stat().st_size > 0:
                    subprocess.run(
                        ["whisper", str(audio_tmp),
                         "--output_format", "json",
                         "--word_timestamps", "True",
                         "--output_dir", str(audio_tmp.parent),
                         "--model", "tiny"],
                        capture_output=True, timeout=120
                    )
                    json_out = audio_tmp.with_suffix(".json")
                    if json_out.exists():
                        wdata = json.loads(json_out.read_text())
                        for seg in wdata.get("segments", []):
                            for word_info in seg.get("words", []):
                                w = word_info.get("word", "").lower().strip(" ,.'\"")
                                if keyword in w or w in keyword:
                                    start_time = max(0.0, word_info.get("start", 0) - 0.5)
                                    break
                            else:
                                continue
                            break
            except Exception as w_err:
                print(f"  trim: Whisper detection skipped ({w_err})")
            finally:
                for f in [audio_tmp, audio_tmp.with_suffix(".json")]:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass

        # 4. ffmpeg trim
        trimmed = clip_path.with_stem(clip_path.stem + "_trimmed")
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start_time), "-i", str(clip_path),
             "-t", str(target_duration),
             "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
             str(trimmed)],
            capture_output=True, timeout=60
        )
        if result.returncode == 0 and trimmed.exists() and trimmed.stat().st_size > MIN_CLIP_BYTES:
            clip_path.unlink(missing_ok=True)
            shutil.move(str(trimmed), str(clip_path))  # shutil.move handles cross-device
            print(f"  trim: {target_duration}s window @ t={start_time:.1f}s → {clip_path.name}")
    except Exception as e:
        print(f"  trim: failed (non-fatal): {e}")
    return clip_path


# ─── Chain orchestrator ───────────────────────────────────────────────────────

TierFn = Callable[[dict, Path], bool]

SOURCE_CHAIN: List[tuple] = [
    ("clip_collections", tier_clip_collections, ("any",)),            # pre-loaded by video-research.yml
    ("ytdlp_search",     tier_ytdlp_search,     ("any",)),            # free yt-dlp search
    ("apify_youtube",    tier_apify_youtube,    ("any",)),            # residential proxy — YouTube
    ("ytdlp_ios",        tier_ytdlp_ios,        ("any",)),            # yt-dlp iOS client
    ("apify_instagram",  tier_apify_instagram,  ("any",)),            # residential proxy — Instagram
    ("pexels",           tier_pexels,           ("context-image", "place", "event", "product-photo")),
    ("pixabay",          tier_pixabay,          ("context-image", "place", "event", "product-photo")),
    ("archive_org",      tier_archive_org,      ("any",)),            # public domain
    ("wikimedia",        tier_wikimedia,        ("any",)),            # CC archival
    ("stock_scrapers",   tier_stock_scrapers,   ("context-image", "place", "event")),
    ("giphy",            tier_giphy,            ("context-image", "place", "event", "product-photo")),  # silent skip if no key
]


def fetch_clip_with_fallback(slide_cfg: dict, work_dir: str, filename: str,
                             visual_hint: str = "context-image") -> str:
    """Walk the source chain for one clip slot. Return path (str) or "".

    slide_cfg keys (all optional except at least one query):
        youtube_query, instagram_query, pexels_query, pixabay_query,
        archive_query, wikimedia_query, query, motion_prompt, visual_hint
    work_dir: per-post workdir. Clip goes to <work_dir>/clips/<filename>.
    visual_hint: used to gate stock tiers — bio-card / context-image / place / event / product-photo / none.
    """
    dest_dir = Path(work_dir) / "clips"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    # Cache: return early if we already have this file
    if dest_path.exists() and dest_path.stat().st_size > MIN_CLIP_BYTES:
        return str(dest_path)

    # Resolve effective visual hint (config > arg)
    effective_hint = slide_cfg.get("visual_hint", visual_hint) or visual_hint

    for tier_name, tier_fn, allowed_hints in SOURCE_CHAIN:
        # Gate stock-only tiers for people-specific slides
        if allowed_hints != ("any",) and effective_hint not in allowed_hints:
            continue
        try:
            if tier_fn(slide_cfg, dest_path):
                # Trim to a relevant 3-5s window — Whisper locates keyword, ffmpeg cuts
                _trim_to_relevant_window(dest_path, slide_cfg, target_duration=4.0)
                return str(dest_path)
        except Exception as e:
            print(f"  motion_sources: {tier_name} unexpected error: {e}")

    print(f"  motion_sources: all sources empty for '{filename}' — Ken Burns will fall back on PNG")
    return ""


# ─── Module-level smoke test ──────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    import tempfile
    print("motion_sources.py — smoke test")
    print(f"  APIFY_API_KEY set: {bool(APIFY_KEY)}")
    print(f"  PEXELS_API_KEY set: {bool(PEXELS_KEY)}")
    print(f"  PIXABAY_API_KEY set: {bool(PIXABAY_KEY)}")
    print(f"  GIPHY_API_KEY set: {bool(GIPHY_KEY)}")
    print(f"  tiers: {[t[0] for t in SOURCE_CHAIN]}")
    # Light test: only runs if keys set. Use a query that should hit stock libs.
    if PEXELS_KEY:
        with tempfile.TemporaryDirectory() as td:
            p = fetch_clip_with_fallback(
                {"pexels_query": "city skyline aerial", "visual_hint": "context-image"},
                td, "smoke_test.mp4", visual_hint="context-image"
            )
            print(f"  Pexels smoke test result: {p}")
