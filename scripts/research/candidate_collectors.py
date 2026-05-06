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
import time
import urllib.parse
import urllib.request
from typing import Optional

APIFY_API_KEY = os.environ.get("APIFY_API_KEY", "")
APIFY_BASE    = "https://api.apify.com/v2"
CLAUDE_KEY    = os.environ.get("CLAUDE_KEY_4_CONTENT", "")


# ── YouTube candidate search (yt-dlp ytsearchN) ──────────────────────────────

def search_youtube_candidates(queries: list[str], max_per_query: int = 5,
                              max_duration_sec: int = 1800) -> list[dict]:
    """Use yt-dlp ytsearchN to find candidate videos.
    Excludes videos > max_duration_sec (default 30 min) to skip long sermons —
    we want the punchy clips, not full 2hr lectures."""
    try:
        import yt_dlp
    except ImportError:
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
    return results


# ── Instagram candidate search (Apify instagram-reel-scraper) ────────────────

def search_instagram_candidates(queries: list[str], max_per_query: int = 5) -> list[dict]:
    """Apify instagram-reel-scraper by hashtag.
    Strips spaces/# from queries to make hashtag-friendly tokens.
    Returns up to max_per_query reels per hashtag."""
    if not APIFY_API_KEY:
        print("  APIFY_API_KEY not set — skipping Instagram search")
        return []

    actor = "apify~instagram-reel-scraper"
    hashtags = []
    for q in queries:
        h = q.strip().replace("#", "").replace(" ", "")
        if h and h not in hashtags:
            hashtags.append(h)
    if not hashtags:
        return []

    payload = {
        "hashtags": hashtags[:8],   # Apify caps usefulness around 5-8 tags per run
        "resultsLimit": max_per_query * len(hashtags[:8]),
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
    except Exception as e:
        print(f"  Apify IG search start failed: {e}")
        return []

    status = ""
    for _ in range(18):
        time.sleep(10)
        try:
            s = json.loads(urllib.request.urlopen(
                f"{APIFY_BASE}/actor-runs/{run_id}?token={APIFY_API_KEY}", timeout=15
            ).read())
            status = s["data"]["status"]
        except Exception:
            continue
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status != "SUCCEEDED":
        print(f"  Apify IG search ended: {status}")
        return []

    try:
        items = json.loads(urllib.request.urlopen(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items?token={APIFY_API_KEY}&format=json",
            timeout=30
        ).read())
    except Exception as e:
        print(f"  Apify IG dataset fetch failed: {e}")
        return []

    results = []
    for item in items or []:
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
            "upload_date": item.get("timestamp", "")[:10].replace("-", ""),
            "query": ",".join(item.get("hashtags", [])[:3]) or "(hashtag-batch)",
        })
    return results


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
                           seed_excerpt: str = "") -> dict:
    """Call Claude Haiku to generate query buckets. Returns dict with 5 keys.
    Falls back to a small hand-built default if Claude unavailable."""
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
    if not CLAUDE_KEY:
        print("  CLAUDE_KEY_4_CONTENT not set — using fallback queries")
        return fallback

    prompt = _QUERY_BUCKET_PROMPT.format(
        person_name=person_name,
        requirement=requirement[:1000],
        seed_excerpt=(seed_excerpt or "(none)")[:800],
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # tolerate ```json fences
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
        data = json.loads(raw)
        # validate keys
        for k in ["youtube_queries", "instagram_queries", "exact_phrase_queries",
                  "negative_queries", "context_queries"]:
            if k not in data or not isinstance(data[k], list):
                data[k] = fallback.get(k, [])
        return data
    except Exception as e:
        print(f"  Query bucket generation failed: {e} — using fallback")
        return fallback


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

    queries = generate_query_buckets(person_name, requirement, seed_excerpt)

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
            raw_ig = search_instagram_candidates(ig_queries[:8], max_per_query=5)
            print(f"  Instagram candidates: {len(raw_ig)}")
    except Exception as e:
        _fail("instagram_search", e)

    candidates = dedupe_candidates(raw_yt + raw_ig)
    print(f"  After dedupe: {len(candidates)} unique candidates")
    return candidates, queries
