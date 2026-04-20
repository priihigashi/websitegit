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
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

APIFY_KEY = os.environ.get("APIFY_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")  # optional — tier skips if unset

MIN_CLIP_BYTES = 10_000          # reject anything smaller as a broken stub
MAX_CLIP_BYTES = 50 * 1024 * 1024  # 50MB hard cap — don't pull full-length films


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


def _http_get_bytes(url: str, timeout: int = 60, max_bytes: int = MAX_CLIP_BYTES) -> bytes:
    """GET a URL, return bytes, enforce size cap. Empty bytes on miss."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
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


def tier_apify_youtube(slide_cfg: dict, dest_path: Path) -> bool:
    """Priority #1 — Apify YouTube scraper + downloader. People + institutions."""
    if not APIFY_KEY:
        return False
    query = slide_cfg.get("youtube_query") or slide_cfg.get("query") or ""
    if not query:
        return False
    try:
        search_items = _apify_run(
            "streamers~youtube-scraper",
            {"searchTerms": [query], "maxResults": 1, "saveVideos": False}
        )
        if not search_items:
            print(f"  motion_sources: Apify YouTube search miss for '{query[:40]}'")
            return False
        video_url = search_items[0].get("url") or search_items[0].get("videoUrl") or ""
        if not video_url or "youtube" not in video_url:
            return False

        dl_items = _apify_run(
            "streamers~youtube-video-downloader",
            {"videoUrls": [{"url": video_url}], "url": video_url,
             "format": "mp4", "quality": "360p", "resolution": "360p"},
            wait=180
        )
        download_url = ""
        for item in dl_items:
            download_url = (item.get("downloadUrl") or item.get("url")
                            or item.get("videoUrl") or item.get("link") or "")
            if download_url:
                break
        if not download_url:
            return False

        raw = _http_get_bytes(download_url, timeout=120)
        if len(raw) < MIN_CLIP_BYTES:
            return False
        dest_path.write_bytes(raw)
        _write_sidecar(dest_path, "apify_youtube", video_url,
                       license_str="YouTube ToS (fair use editorial)",
                       attribution=f"via YouTube — {video_url}", query=query)
        print(f"  motion_sources: Apify YouTube → {dest_path.name} ({len(raw)//1024}KB)")
        return True
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
            for vf in sorted(v.get("video_files", []), key=lambda x: x.get("width", 0)):
                if (vf.get("file_type") == "video/mp4"
                        and vf.get("height", 0) >= vf.get("width", 1)):
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
            # Prefer medium quality (good balance of size + resolution)
            for size_key in ("medium", "small", "large", "tiny"):
                vf = videos.get(size_key, {})
                link = vf.get("url", "")
                w, hh = vf.get("width", 0), vf.get("height", 0)
                if not link or hh < w:  # require portrait-ish
                    continue
                raw = _http_get_bytes(link, timeout=60)
                if len(raw) < MIN_CLIP_BYTES:
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
    return False


# ─── Chain orchestrator ───────────────────────────────────────────────────────

TierFn = Callable[[dict, Path], bool]

SOURCE_CHAIN: List[tuple] = [
    ("apify_youtube",    tier_apify_youtube,    ("any",)),            # people + places
    ("apify_instagram",  tier_apify_instagram,  ("any",)),            # creator reels
    ("pexels",           tier_pexels,           ("context-image", "place", "event", "product-photo")),
    ("pixabay",          tier_pixabay,          ("context-image", "place", "event", "product-photo")),
    ("archive_org",      tier_archive_org,      ("any",)),            # historical
    ("wikimedia",        tier_wikimedia,        ("any",)),            # CC archival
    ("stock_scrapers",   tier_stock_scrapers,   ("context-image", "place", "event")),
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
    print(f"  tiers: {[t[0] for t in SOURCE_CHAIN]}")
    # Light test: only runs if keys set. Use a query that should hit stock libs.
    if PEXELS_KEY:
        with tempfile.TemporaryDirectory() as td:
            p = fetch_clip_with_fallback(
                {"pexels_query": "city skyline aerial", "visual_hint": "context-image"},
                td, "smoke_test.mp4", visual_hint="context-image"
            )
            print(f"  Pexels smoke test result: {p}")
