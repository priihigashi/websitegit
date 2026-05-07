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

# Apify limit / 403 marker — once tripped on the primary actor, sibling
# actors share the same key, so cascading to a second actor is wasted spend.
# We still try the SERP fallback in that case.
_apify_search_limit_hit = False


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
                    })
        except Exception as e:
            print(f"  YT search '{q}': {e}")
    if results:
        state.mark_used("youtube")
    return results


# ── Instagram candidate search (Apify instagram-reel-scraper) ────────────────

def search_instagram_candidates(queries: list[str], max_per_query: int = 5,
                                 person_name: str = "",
                                 on_failure=None) -> list[dict]:
    """Instagram candidate search — 3-route cascade.

    Tier 1 (preferred): Apify instagram-reel-scraper by hashtag.
    Tier 2 (free fallback): SerpAPI Google search with site:instagram.com filter.
    Tier 3 (always-on): DuckDuckGo HTML search with site:instagram.com filter.

    Pipeline-resilience non-negotiable per CLAUDE.md: search must NEVER hard-
    fail when Apify returns 403 / credit exhausted. fallback_mode env gates
    Apify entirely when set to no_paid_anthropic_apify.
    """
    state = get_state()
    primary: list[dict] = []
    if state.should_try_apify() and APIFY_API_KEY:
        primary = _ig_via_apify(queries, max_per_query, on_failure=on_failure)
    if primary:
        return primary
    # Fallback ladder runs when Apify returned nothing OR was unavailable.
    if not state.should_try_apify():
        print("  Instagram: Apify disabled by fallback_mode — using web search")
    elif not APIFY_API_KEY:
        print("  Instagram: APIFY_API_KEY not set — falling back to web search")
    elif _apify_search_limit_hit:
        print("  Instagram: Apify limit hit — falling back to web search")
    else:
        print("  Instagram: Apify returned 0 — trying web fallbacks")
    fallback = _ig_via_web_search(queries, person_name=person_name,
                                  max_results=max_per_query * 4)
    if fallback:
        return fallback
    return primary


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
        low = err.lower()
        if "403" in err or "402" in err or "credit" in low or "billing" in low or "quota" in low:
            _apify_search_limit_hit = True
        state.mark_failed("apify", f"ig_hashtag_start:{actor}", err,
                          on_failure=on_failure)
        print(f"  Apify IG hashtag search start failed: {err[:300]}")
        return []

    status = _apify_poll_run(run_id)
    if status != "SUCCEEDED":
        state.mark_failed("apify", "ig_hashtag_run", f"run_status:{status}",
                          on_failure=on_failure)
        print(f"  Apify IG hashtag run ended: {status}")
        return []

    items = _apify_fetch_items(run_id)
    if not items:
        state.mark_failed("apify", "ig_hashtag_dataset", "empty_dataset",
                          on_failure=on_failure)

    results = []
    for item in items:
        url = item.get("url") or item.get("postUrl") or ""
        if not url and item.get("shortCode"):
            url = f"https://www.instagram.com/reel/{item['shortCode']}/"
        if not url:
            continue
        results.append({
            "platform": "instagram",
            "id": item.get("shortCode") or item.get("id", ""),
            "url": url,
            "title": (item.get("caption") or "")[:200],
            "uploader": item.get("ownerUsername", ""),
            "duration": item.get("videoDuration"),
            "upload_date": (item.get("timestamp", "") or "")[:10].replace("-", ""),
            "query": ",".join(item.get("hashtags", [])[:3]) or "(hashtag-batch)",
        })
    if results:
        state.mark_used("apify")
    return results


# ── Instagram fallback: web search (SerpAPI > DuckDuckGo) ────────────────────

def _ig_via_web_search(queries: list[str], person_name: str = "",
                       max_results: int = 20) -> list[dict]:
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
        })
        if len(results) >= max_results:
            break
    return results


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


def _duckduckgo_search(query: str, max_results: int) -> list[str]:
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
        if "instagram.com/" in href and "/reel/" in href:
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
                on_failure=on_failure,
            )
            print(f"  Instagram candidates: {len(raw_ig)}")
    except Exception as e:
        _fail("instagram_search", e)

    candidates = dedupe_candidates(raw_yt + raw_ig)
    print(f"  After dedupe: {len(candidates)} unique candidates")
    return candidates, queries
