"""candidate_collectors.py — keyword -> candidate URLs across platforms.

Functions:
  generate_query_buckets(person_name, requirement, seed_excerpt) -> dict
  search_youtube_candidates(queries, max_per_query)              -> list[dict]
  search_instagram_candidates(queries, max_per_query)            -> list[dict]
  dedupe_candidates(items)                                       -> list[dict]

Candidate dict shape:
  {
    "platform": "youtube" | "instagram",
    "url": "https://...",
    "id": "...",
    "title": "...",
    "uploader": "...",          # YT channel name or IG username
    "duration": int|None,
    "upload_date": "YYYYMMDD" | "",
    "query": "search query that surfaced this",
  }
"""

from __future__ import annotations
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Sibling import shim (route_state, llm_router) so this module is callable
# both as `research.candidate_collectors` and as a standalone import.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from route_state import get_state  # noqa: E402

APIFY_API_KEY = os.environ.get("APIFY_API_KEY", "")
APIFY_BASE    = "https://api.apify.com/v2"
CLAUDE_KEY    = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
SERP_API_KEY  = os.environ.get("SERP_API_KEY", "")  # Free fallback when Apify down

# Apify billing/quota marker. Actor-specific 403/provider-access errors are
# stage-scoped because another actor (for example direct Reel lookup) may still
# be healthy with the same key. We still try the SERP fallback in that case.
_apify_search_limit_hit = False


def _apify_failure_disables_route(reason: str) -> bool:
    """True only for account/provider-level failures shared by Apify actors."""
    low = (reason or "").lower()
    if "insufficient-permissions" in low or "provider-access" in low:
        return False
    markers = (
        "401", "402", "429",
        "auth", "unauthorized",
        "credit", "billing", "quota", "limit",
    )
    return any(m in low for m in markers)


# ── YouTube candidate search (yt-dlp ytsearchN) ──────────────────────────────

def search_youtube_candidates(queries: list[str], max_per_query: int = 5,
                              max_duration_sec: int = 1800) -> list[dict]:
    """Use yt-dlp ytsearchN to find candidate videos.
    Excludes videos > max_duration_sec (default 30 min) to skip long sermons —
    we want the punchy clips, not full 2hr lectures."""
    state = get_state()
    try:
        import yt_dlp
    except ImportError:
        state.mark_unavailable("youtube", "yt_dlp_not_installed")
        print("  yt-dlp not installed")
        return []

    results = []
    for q in queries:
        if not q.strip():
            continue
        opts = {
            "quiet": True,
            "extract_flat": True,
            "default_search": f"ytsearch{max_per_query}",
            "match_filter": yt_dlp.utils.match_filter_func(f"duration < {max_duration_sec}"),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{max_per_query}:{q}", download=False)
                for entry in info.get("entries", []) or []:
                    if not entry:
                        continue
                    vid = entry.get("id", "")
                    if not vid:
                        continue
                    results.append({
                        "platform": "youtube",
                        "id": vid,
                        "url": f"https://youtube.com/watch?v={vid}",
                        "title": entry.get("title", ""),
                        "uploader": entry.get("uploader", "") or entry.get("channel", ""),
                        "duration": entry.get("duration"),
                        "upload_date": entry.get("upload_date", "") or "",
                        "query": q,
                        "route": "youtube_search",
                    })
        except Exception as e:
            print(f"  YT search '{q}': {e}")
    if results:
        state.mark_used("youtube")
    return results


# ── Instagram candidate search (Apify instagram-reel-scraper) ────────────────

def search_instagram_candidates(queries: list[str], max_per_query: int = 5,
                                 person_name: str = "",
                                 seed_url: str = "",
                                 on_failure=None) -> list[dict]:
    """Instagram candidate search — subject-first cascade.

    Discovery principle: the seed uploader is usually a reposter, not the
    subject person. Search by person name + evidence keywords first, try likely
    subject handles second, and only scrape the seed uploader after a tiny
    relevance probe says their feed is about the named person.

    Pipeline-resilience non-negotiable per CLAUDE.md: search must NEVER hard-
    fail when Apify returns 403 / credit exhausted. fallback_mode env gates
    Apify entirely when set to no_paid_anthropic_apify.
    """
    state = get_state()

    collected: list[dict] = []

    # 1) Name + keyword search first. This catches repost/commentary accounts
    # and avoids assuming the seed uploader is the person of interest.
    name_keyword = _ig_via_web_search(
        queries, person_name=person_name, max_results=max_per_query * 4,
        route="ig_name_keyword",
    )
    collected.extend(name_keyword)

    if state.should_try_apify() and APIFY_API_KEY:
        # 2) Direct handle attempts for the named person. Guesses are enriched
        # by public web profile search when SerpAPI/DDG is available.
        handles = _handle_variations(person_name, queries)
        handles.extend(_ig_profile_handles_from_web(person_name, max_results=6))
        handle_results = _ig_via_username(
            handles, max_per_query=max_per_query, route="ig_person_handle",
            on_failure=on_failure,
        )
        collected.extend(handle_results)

        # 3) Hashtag discovery is still useful, but it should not outrank
        # person-name/handle routes.
        collected.extend(_ig_via_apify(queries, max_per_query, on_failure=on_failure))

        # 4) Seed uploader scrape is last, and only after a small relevance
        # probe shows that account is actually posting about the named person.
        collected.extend(_ig_via_verified_seed_uploader(
            seed_url, person_name=person_name, max_per_query=max_per_query,
            on_failure=on_failure,
        ))

    collected = dedupe_candidates(collected)
    if collected:
        return collected

    # No route found candidates. Log the most useful reason.
    if not state.should_try_apify():
        print("  Instagram: Apify disabled by fallback_mode — using web search")
    elif not APIFY_API_KEY:
        print("  Instagram: APIFY_API_KEY not set — falling back to web search")
    elif _apify_search_limit_hit:
        print("  Instagram: Apify limit hit — falling back to web search")
    else:
        print("  Instagram: subject-first routes returned 0")
    return collected


def _scrub_apify(s: str) -> str:
    """Strip Apify tokens before logging the body of an error response."""
    if not s:
        return s
    return re.sub(r"(token=)[^&\s\"']+", r"\1REDACTED", s, flags=re.IGNORECASE)


def _apify_post_run(actor: str, payload: dict, timeout_s: int = 30):
    """Start an Apify actor run via `requests`. Returns (run_id, error_body).
    On non-2xx, error_body contains the JSON message Apify returned (scrubbed)
    instead of just `HTTP 4xx`. Caller decides whether to retry / fall through.
    """
    try:
        import requests
    except ImportError:
        return None, "requests_not_installed"
    try:
        resp = requests.post(
            f"{APIFY_BASE}/acts/{actor}/runs",
            params={"token": APIFY_API_KEY},
            json=payload, timeout=timeout_s,
        )
    except Exception as e:
        return None, _scrub_apify(str(e))[:400]
    # Try to surface Apify's JSON error body (it carries actionable detail
    # like "Field input.username is required" — opaque "HTTP 400" doesn't).
    body_text = ""
    try:
        body = resp.json()
    except Exception:
        body = None
        body_text = (resp.text or "")[:400]
    if resp.status_code >= 400 or (isinstance(body, dict) and "error" in body):
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            msg = (f"HTTP {resp.status_code} [{err.get('type','?')}] "
                   f"{err.get('message','?')}")
        else:
            msg = f"HTTP {resp.status_code} {body_text or '(no body)'}"
        return None, _scrub_apify(msg)[:400]
    if not isinstance(body, dict) or "data" not in body or "id" not in body.get("data", {}):
        return None, _scrub_apify(f"unexpected_response: {str(body)[:200]}")
    return body["data"]["id"], None


def _apify_poll_run(run_id: str, max_attempts: int = 18) -> str:
    try:
        import requests
    except ImportError:
        return ""
    status = ""
    for _ in range(max_attempts):
        time.sleep(10)
        try:
            s = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": APIFY_API_KEY}, timeout=15,
            ).json()
        except Exception:
            continue
        status = s.get("data", {}).get("status", "")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    return status


def _apify_fetch_items(run_id: str) -> list:
    try:
        import requests
        items = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "format": "json"}, timeout=30,
        ).json()
        return items if isinstance(items, list) else []
    except Exception:
        return []


_IG_VIDEO_KEYS = (
    "videoUrl", "video_url", "videoUrlBackup", "downloadedVideo",
    "videoUrlMain", "media_url", "mediaUrl", "audioUrl",
)


def _item_has_video_media(item: dict) -> bool:
    """True when an Apify IG item exposes an actual video/audio field.
    `displayUrl` is deliberately excluded: it is usually an image thumbnail
    and caused Whisper invalid-format failures in live SH-104 runs."""
    if not isinstance(item, dict):
        return False
    if item.get("videoDuration") or item.get("videoViewCount"):
        return True
    for k in _IG_VIDEO_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return True
        if isinstance(v, list) and v:
            for child in v:
                if isinstance(child, str) and child.startswith(("http://", "https://")):
                    return True
                if isinstance(child, dict) and _item_has_video_media(child):
                    return True
    for child in item.get("childPosts") or []:
        if isinstance(child, dict) and _item_has_video_media(child):
            return True
    return False


def _candidate_from_ig_item(item: dict, query: str, route: str) -> dict | None:
    url = item.get("url") or item.get("postUrl") or ""
    if not url and item.get("shortCode"):
        url = f"https://www.instagram.com/reel/{item['shortCode']}/"
    if not url:
        return None
    return {
        "platform": "instagram",
        "id": item.get("shortCode") or item.get("id", ""),
        "url": url,
        "title": (item.get("caption") or "")[:200],
        "uploader": item.get("ownerUsername") or item.get("username", ""),
        "duration": item.get("videoDuration"),
        "upload_date": (item.get("timestamp", "") or "")[:10].replace("-", ""),
        "query": query,
        "route": route,
    }


def _person_tokens(person_name: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", (person_name or "").lower())
    compact = "".join(raw)
    out = []
    for token in raw + ([compact] if compact else []):
        if len(token) >= 3 and token not in out:
            out.append(token)
    return out


def _mentions_person(text: str, person_name: str) -> bool:
    low = (text or "").lower()
    tokens = _person_tokens(person_name)
    if not tokens:
        return False
    return all(t in low for t in tokens[:2]) or any(t in low for t in tokens if len(t) >= 6)


def _handle_variations(person_name: str, queries: list[str]) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", (person_name or "").lower())
    compact = "".join(tokens)
    handles: list[str] = []

    def add(value: str):
        h = re.sub(r"[^a-z0-9_.]", "", (value or "").lower()).strip("._")
        if len(h) >= 3 and h not in handles:
            handles.append(h)

    add(compact)
    if len(tokens) >= 2:
        add(".".join(tokens))
        add("_".join(tokens))
        add(tokens[-1] + tokens[0])
        add(tokens[0] + tokens[-1])
    if compact:
        add(f"{compact}oficial")
        add(f"oficial{compact}")
    for q in queries:
        q_clean = q.strip().lstrip("@#").lower()
        if q_clean and " " not in q_clean:
            add(q_clean)
    return handles[:12]


def _ig_seed_owner_username(seed_url: str, on_failure=None) -> str:
    """Resolve the public seed Reel to its owner username via directUrls."""
    state = get_state()
    if not seed_url or "instagram.com" not in seed_url:
        return ""
    if not state.should_try_apify() or not APIFY_API_KEY:
        return ""
    actor = "apify~instagram-scraper"
    payload = {
        "directUrls": [seed_url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": {"useApifyProxy": True},
    }
    run_id, err = _apify_post_run(actor, payload)
    if err is not None:
        if _apify_failure_disables_route(err):
            state.mark_failed("apify", f"ig_seed_owner_start:{actor}", err,
                              on_failure=on_failure)
        else:
            state.mark_stage_failed("apify", f"ig_seed_owner_start:{actor}", err,
                                    on_failure=on_failure)
        print(f"  Apify IG seed owner lookup failed: {err[:300]}")
        return ""
    status = _apify_poll_run(run_id, max_attempts=12)
    if status != "SUCCEEDED":
        state.mark_stage_failed("apify", "ig_seed_owner_run", f"run_status:{status}",
                                on_failure=on_failure)
        return ""
    items = _apify_fetch_items(run_id)
    if not items:
        state.mark_stage_failed("apify", "ig_seed_owner_dataset", "empty_dataset",
                                on_failure=on_failure)
        return ""
    item = items[0] if isinstance(items[0], dict) else {}
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    username = item.get("ownerUsername") or item.get("username") or owner.get("username") or ""
    username = (username or "").strip().lstrip("@")
    if username:
        state.mark_used("apify")
        print(f"  Instagram seed owner: @{username}")
    return username


def _ig_via_username_from_seed(seed_url: str, max_per_query: int = 5,
                               on_failure=None) -> list[dict]:
    username = _ig_seed_owner_username(seed_url, on_failure=on_failure)
    if not username:
        return []
    return _ig_via_username([username], max_per_query=max_per_query,
                            route="ig_seed_uploader", on_failure=on_failure)


def _ig_via_verified_seed_uploader(seed_url: str, person_name: str,
                                   max_per_query: int = 5,
                                   on_failure=None) -> list[dict]:
    username = _ig_seed_owner_username(seed_url, on_failure=on_failure)
    if not username:
        return []
    if _mentions_person(username, person_name):
        return _ig_via_username(
            [username], max_per_query=max_per_query, route="ig_seed_uploader",
            on_failure=on_failure,
        )

    probe = _ig_via_username(
        [username], max_per_query=2, route="ig_seed_uploader_probe",
        on_failure=on_failure,
    )
    relevant = [
        c for c in probe
        if _mentions_person(" ".join([c.get("title", ""), c.get("uploader", "")]),
                            person_name)
    ]
    if not relevant:
        print(f"  Instagram seed uploader @{username}: skipped (not subject-relevant)")
        return []
    deep = _ig_via_username(
        [username], max_per_query=max_per_query, route="ig_seed_uploader",
        on_failure=on_failure,
    )
    return dedupe_candidates(relevant + deep)


def _ig_via_username(usernames: list[str], max_per_query: int = 5,
                     route: str = "ig_username",
                     on_failure=None) -> list[dict]:
    """Apify instagram-reel-scraper username discovery.

    Smoke-tested schema:
        {"username": ["handle_without_at"], "resultsLimit": N}
    """
    state = get_state()
    if not state.should_try_apify() or not APIFY_API_KEY:
        return []
    handles = []
    for u in usernames:
        h = (u or "").strip().lstrip("@")
        if h and h not in handles:
            handles.append(h)
    if not handles:
        return []
    actor = "apify~instagram-reel-scraper"
    payload = {
        "username": handles[:3],
        "resultsLimit": max_per_query * len(handles[:3]),
    }
    run_id, err = _apify_post_run(actor, payload)
    if err is not None:
        if _apify_failure_disables_route(err):
            state.mark_failed("apify", f"ig_username_start:{actor}", err,
                              on_failure=on_failure)
        else:
            state.mark_stage_failed("apify", f"ig_username_start:{actor}", err,
                                    on_failure=on_failure)
        print(f"  Apify IG username search start failed: {err[:300]}")
        return []
    status = _apify_poll_run(run_id)
    if status != "SUCCEEDED":
        state.mark_stage_failed("apify", "ig_username_run", f"run_status:{status}",
                                on_failure=on_failure)
        print(f"  Apify IG username run ended: {status}")
        return []
    items = _apify_fetch_items(run_id)
    results = []
    for item in items:
        if not isinstance(item, dict) or item.get("error"):
            continue
        if not _item_has_video_media(item):
            continue
        cand = _candidate_from_ig_item(item, f"{route}:{','.join(handles[:3])}", route)
        if cand:
            results.append(cand)
    if results:
        state.mark_used("apify")
        print(f"  Instagram username reels ({route}): {len(results)}")
    return results


def _ig_via_apify(queries: list[str], max_per_query: int = 5,
                   on_failure=None) -> list[dict]:
    """Apify Instagram hashtag discovery — verified actor schema.

    Uses `apify~instagram-hashtag-scraper` with payload:
        {"hashtags": ["frei", "gilson"], "resultsLimit": N}

    This matches the schema used by scripts/4am_agent/scraper.py
    (scrape_instagram_hashtag) which is known-good. The previous
    `apify~instagram-reel-scraper` payload was wrong: that actor expects
    {"username": "..."} not hashtags, so it returned HTTP 400 every run.

    Returns up to max_per_query reels per hashtag. Honors fallback_mode
    via route_state.
    """
    global _apify_search_limit_hit
    state = get_state()
    if not state.should_try_apify():
        return []
    if not APIFY_API_KEY:
        state.mark_unavailable("apify", "no_api_key")
        return []
    if _apify_search_limit_hit:
        return []

    actor = "apify~instagram-hashtag-scraper"
    hashtags = []
    for q in queries:
        h = q.strip().replace("#", "").replace(" ", "")
        if h and h not in hashtags:
            hashtags.append(h)
    if not hashtags:
        return []

    payload = {
        "hashtags": hashtags[:8],
        "resultsLimit": max_per_query * len(hashtags[:8]),
    }
    run_id, err = _apify_post_run(actor, payload)
    if err is not None:
        if _apify_failure_disables_route(err):
            _apify_search_limit_hit = True
            state.mark_failed("apify", f"ig_hashtag_start:{actor}", err,
                              on_failure=on_failure)
        else:
            state.mark_stage_failed("apify", f"ig_hashtag_start:{actor}", err,
                                    on_failure=on_failure)
        print(f"  Apify IG hashtag search start failed: {err[:300]}")
        return []

    status = _apify_poll_run(run_id)
    if status != "SUCCEEDED":
        state.mark_stage_failed("apify", "ig_hashtag_run", f"run_status:{status}",
                                on_failure=on_failure)
        print(f"  Apify IG hashtag run ended: {status}")
        return []

    items = _apify_fetch_items(run_id)
    if not items:
        state.mark_stage_failed("apify", "ig_hashtag_dataset", "empty_dataset",
                                on_failure=on_failure)

    results = []
    for item in items:
        if not _item_has_video_media(item):
            continue
        cand = _candidate_from_ig_item(
            item, ",".join(item.get("hashtags", [])[:3]) or "(hashtag-batch)",
            "ig_hashtag",
        )
        if cand:
            results.append(cand)
    if results:
        state.mark_used("apify")
    return results


# ── Instagram fallback: web search (SerpAPI > DuckDuckGo) ────────────────────

def _ig_via_web_search(queries: list[str], person_name: str = "",
                       max_results: int = 20,
                       route: str = "ig_web_search") -> list[dict]:
    """Free fallback when Apify is down. Searches the open web for
    instagram.com/reel URLs that mention the person, then resolves each
    URL to a minimal candidate record (transcript fetched later by runner).

    Tier order:
      1. SerpAPI (if SERP_API_KEY set) — Google with site:instagram.com filter
      2. DuckDuckGo HTML — no key needed; rate-limited by DDG itself
    """
    pname = (person_name or "").strip()
    # Build search terms: hashtag tokens map back to keywords
    keywords = []
    for q in queries[:6]:
        token = q.strip().replace("#", "").replace("_", " ")
        if token:
            keywords.append(token)
    base = (pname + " " if pname else "") + " ".join(keywords[:3])
    if not base.strip():
        return []
    google_query = f'site:instagram.com/reel {base}'.strip()

    urls = []
    if SERP_API_KEY:
        urls = _serpapi_search(google_query, max_results)
        if urls:
            print(f"  Instagram fallback: SerpAPI returned {len(urls)} URLs")
    if not urls:
        urls = _duckduckgo_search(google_query, max_results)
        if urls:
            print(f"  Instagram fallback: DuckDuckGo returned {len(urls)} URLs")

    results = []
    seen = set()
    for u in urls:
        m = re.search(r"instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)", u)
        if not m:
            continue
        shortcode = m.group(1)
        if shortcode in seen:
            continue
        seen.add(shortcode)
        results.append({
            "platform": "instagram",
            "id": shortcode,
            "url": f"https://www.instagram.com/reel/{shortcode}/",
            "title": "",          # title comes from transcript step
            "uploader": "",
            "duration": None,
            "upload_date": "",
            "query": f"web-fallback:{base}",
            "route": route,
        })
        if len(results) >= max_results:
            break
    return results


def _ig_profile_handles_from_web(person_name: str, max_results: int = 6) -> list[str]:
    """Find likely public Instagram profile handles for the named person."""
    pname = (person_name or "").strip()
    if not pname:
        return []
    query = f"site:instagram.com {pname} Instagram perfil"
    urls = []
    if SERP_API_KEY:
        urls = _serpapi_search(query, max_results * 2)
    if not urls:
        urls = _duckduckgo_search(query, max_results * 2, include_profiles=True)
    handles: list[str] = []
    for u in urls:
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)(?:/|$)", u)
        if not m:
            continue
        handle = m.group(1).lower()
        if handle in {"p", "reel", "tv", "stories", "explore", "accounts"}:
            continue
        if handle not in handles:
            handles.append(handle)
        if len(handles) >= max_results:
            break
    if handles:
        print(f"  Instagram profile handles from web: {', '.join(handles[:5])}")
    return handles


def _serpapi_search(query: str, max_results: int) -> list[str]:
    """SerpAPI Google JSON. Free tier: 100 searches/month — adequate for
    occasional Apify outages. Returns list of URLs (newest first)."""
    state = get_state()
    if not SERP_API_KEY:
        state.mark_unavailable("serpapi", "no_api_key")
        return []
    try:
        params = urllib.parse.urlencode({
            "engine": "google", "q": query, "api_key": SERP_API_KEY,
            "num": max(10, min(max_results, 30)),
        })
        url = f"https://serpapi.com/search.json?{params}"
        data = json.loads(urllib.request.urlopen(url, timeout=20).read())
        results = data.get("organic_results", []) or []
        urls = [r.get("link", "") for r in results if r.get("link")]
        if urls:
            state.mark_used("serpapi")
        return urls
    except Exception as e:
        state.mark_failed("serpapi", "search", str(e)[:300])
        print(f"  SerpAPI fallback failed: {e}")
        return []


def _duckduckgo_search(query: str, max_results: int,
                       include_profiles: bool = False) -> list[str]:
    """DuckDuckGo HTML scraper. No API key. Last-resort free route.
    Parses the lite HTML endpoint which is the most stable interface.
    Honors a small delay to avoid rate-limit. Returns list of URLs."""
    state = get_state()
    try:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (oak-park-ai-hub SH-104 fallback)",
                "Accept-Language": "en-US,en;q=0.7",
            },
        )
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    except Exception as e:
        state.mark_failed("duckduckgo", "search", str(e)[:300])
        print(f"  DuckDuckGo fallback failed: {e}")
        return []
    # Extract result anchors. DDG uses /l/?uddg=<encoded URL>&...
    raw = re.findall(r'href="(?:https?://[^"]+|/l/\?[^"]+)"', html)
    out = []
    for href in raw:
        if href.startswith("/l/?"):
            qs = urllib.parse.parse_qs(href.split("?", 1)[1])
            real = (qs.get("uddg") or [""])[0]
            if real:
                href = real
        if "instagram.com/" in href and (include_profiles or "/reel/" in href):
            out.append(href)
        if len(out) >= max_results:
            break
    if out:
        state.mark_used("duckduckgo")
    return out


# ── Dedupe ───────────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """Strip query strings and tracking params for dedup key."""
    if not url:
        return ""
    u = url.split("?")[0].rstrip("/")
    u = u.replace("www.youtube.com", "youtube.com")
    u = u.replace("youtu.be/", "youtube.com/watch?v=")  # shouldn't have ? after split
    return u.lower()


def dedupe_candidates(items: list[dict]) -> list[dict]:
    """Dedupe by normalized URL. First occurrence wins; merges 'query' field."""
    seen: dict[str, dict] = {}
    for it in items:
        key = _normalize_url(it.get("url", ""))
        if not key:
            continue
        if key in seen:
            existing_q = seen[key].get("query", "")
            new_q = it.get("query", "")
            if new_q and new_q not in existing_q:
                seen[key]["query"] = f"{existing_q} | {new_q}".strip(" |")
        else:
            seen[key] = dict(it)
    return list(seen.values())


# ── Query bucket generation (Claude Haiku) ───────────────────────────────────

_QUERY_BUCKET_PROMPT = """You are generating search queries to find clips of a public figure where transcript evidence MAY support a claim. You are NOT making the claim — only generating discovery queries.

PERSON: {person_name}

EVIDENCE REQUIREMENT (what we are looking for in transcripts):
{requirement}

SEED CONTEXT (excerpt from the seed clip — for tone/topic anchor only):
{seed_excerpt}

Generate search queries in 5 buckets. Output ONLY valid JSON, no commentary:

{{
  "youtube_queries": ["6-10 broad-discovery queries combining person name + topic keywords. Mix Portuguese and topic-neutral framing. NO accusatory phrasing."],
  "instagram_queries": ["6-10 hashtag-friendly tokens. lowercase. no spaces. focus on the person's name handle if known + topic hashtags people would use."],
  "exact_phrase_queries": ["3-5 quoted phrases that fact-checkers/critics commonly use when documenting this person on this topic. NEUTRAL phrasing."],
  "negative_queries": ["3-5 patterns to EXCLUDE — people with similar names, unrelated topics, parodies."],
  "context_queries": ["3-5 broader context queries — interviews, sermons, public appearances where the topic might come up incidentally."]
}}

Rules:
- Queries must be DISCOVERY queries, not assertions.
- Prefer Portuguese for Brazilian figures, Spanish for Hispanic, English for US figures.
- Do not include profanity, slurs, or defamatory phrasing in queries.
- Hashtags: stripped of # and spaces.
- Output STRICTLY the JSON object. No prose."""


def generate_query_buckets(person_name: str, requirement: str,
                           seed_excerpt: str = "",
                           on_failure=None) -> dict:
    """Generate 5 query buckets via the LLM router (Anthropic Haiku → OpenAI
    cascade). Falls back to a small hand-built default if every LLM route is
    unavailable. Always returns a complete dict with all 5 keys."""
    fallback = {
        "youtube_queries": [
            f"{person_name}",
            f"{person_name} polêmica",
            f"{person_name} entrevista",
            f"{person_name} sermão",
            f"{person_name} fala sobre",
        ],
        "instagram_queries": [
            re.sub(r"[^a-z0-9]", "", person_name.lower()),
            re.sub(r"[^a-z0-9]", "", person_name.lower()) + "polemica",
        ],
        "exact_phrase_queries": [],
        "negative_queries": [],
        "context_queries": [f"{person_name} discurso", f"{person_name} pregação"],
    }

    prompt = _QUERY_BUCKET_PROMPT.format(
        person_name=person_name,
        requirement=requirement[:1000],
        seed_excerpt=(seed_excerpt or "(none)")[:800],
    )
    try:
        from llm_router import llm_json
        data = llm_json(prompt, max_tokens=1500, on_failure=on_failure)
    except Exception as e:
        # strict mode raises — surface to caller; auto-mode never raises.
        print(f"  Query bucket generation raised: {e} — using fallback")
        return fallback

    if not isinstance(data, dict) or not data:
        print("  Query bucket generation: no LLM available — using fallback")
        return fallback

    for k in ["youtube_queries", "instagram_queries", "exact_phrase_queries",
              "negative_queries", "context_queries"]:
        if k not in data or not isinstance(data[k], list):
            data[k] = fallback.get(k, [])
    return data


# ── orchestrator ─────────────────────────────────────────────────────────────

def collect_candidates(person_name: str, requirement: str, seed_excerpt: str = "",
                       seed_url: str = "",
                       target_count: int = 6,
                       on_failure=None) -> tuple[list[dict], dict]:
    """Generate queries -> search both platforms -> dedupe.
    Tries to collect at least 3-5x target_count to leave room for rejection.

    on_failure: optional callable(stage:str, error:str) for log_pipeline_failure wiring.

    Returns (candidates, queries_used)."""
    def _fail(stage, err):
        if on_failure:
            try:
                on_failure(stage, str(err))
            except Exception:
                pass

    queries = generate_query_buckets(person_name, requirement, seed_excerpt,
                                     on_failure=on_failure)

    yt_queries = (queries.get("youtube_queries", [])
                  + queries.get("context_queries", [])
                  + queries.get("exact_phrase_queries", []))
    ig_queries = queries.get("instagram_queries", [])

    target_pool = max(target_count * 4, 20)
    yt_per_query = max(3, target_pool // max(1, len(yt_queries)))

    raw_yt = []
    try:
        raw_yt = search_youtube_candidates(yt_queries[:10], max_per_query=yt_per_query)
        print(f"  YouTube candidates: {len(raw_yt)}")
    except Exception as e:
        _fail("youtube_search", e)

    raw_ig = []
    try:
        if ig_queries:
            raw_ig = search_instagram_candidates(
                ig_queries[:8], max_per_query=5, person_name=person_name,
                seed_url=seed_url,
                on_failure=on_failure,
            )
            print(f"  Instagram candidates: {len(raw_ig)}")
    except Exception as e:
        _fail("instagram_search", e)

    candidates = dedupe_candidates(raw_yt + raw_ig)
    print(f"  After dedupe: {len(candidates)} unique candidates")
    return candidates, queries
