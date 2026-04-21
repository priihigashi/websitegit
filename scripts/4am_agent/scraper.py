"""
scraper.py — Apify Instagram/TikTok scraper with 10k+ views filter
Reads Scraping Targets tab to know what to scrape.
"""
import os, time, requests
import pytz
from datetime import datetime

APIFY_API_KEY = os.environ.get("APIFY_API_KEY", "")
APIFY_BASE    = "https://api.apify.com/v2"
MIN_VIEWS     = 10_000
MIN_ENGAGEMENT = 500   # fallback for image/carousel posts where views = 0
et            = pytz.timezone("America/New_York")


def _run_actor(actor_id, input_data, timeout_s=300):
    """Start an Apify actor run and poll until done, then return items."""
    resp = requests.post(
        f"{APIFY_BASE}/acts/{actor_id}/runs",
        params={"token": APIFY_API_KEY},
        json=input_data,
        timeout=30,
    )
    body = resp.json()
    if "error" in body:
        err = body["error"]
        raise RuntimeError(f"Apify API error [{err.get('type','?')}]: {err.get('message','?')}")
    if "data" not in body:
        raise RuntimeError(f"Apify unexpected response: {str(body)[:200]}")

    run_id = body["data"]["id"]

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(8)
        status_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY},
        ).json()
        if "error" in status_resp:
            raise RuntimeError(f"Apify status error: {status_resp['error']}")
        status = status_resp.get("data", {}).get("status", "UNKNOWN")
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended: {status}")

    items = requests.get(
        f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
        params={"token": APIFY_API_KEY},
    ).json()
    return items if isinstance(items, list) else []


def _extract_views(item):
    return (
        item.get("videoViewCount")
        or item.get("playCount")
        or item.get("viewCount")
        or 0
    )


def _extract_score(item):
    """
    Returns a unified performance score.
    For Reels/videos: use view count directly.
    For image posts/carousels (views=0): use engagement proxy scaled to views-equivalent.
    This fixes the issue where apify~instagram-scraper returns null views for non-video posts.
    """
    views = _extract_views(item)
    if views > 0:
        return views
    # Engagement fallback: likes + (comments * 2) scaled to views-equivalent
    likes    = item.get("likesCount") or item.get("diggCount") or 0
    comments = item.get("commentsCount") or 0
    return (likes + comments * 2) * 20  # ~20x multiplier aligns engagement with view scale


def _normalise(item, niche, target_type, target_value):
    url = item.get("url") or (
        f"https://www.instagram.com/p/{item['shortCode']}/"
        if item.get("shortCode") else ""
    )
    # Auto-tag verification series by target type
    series_override = ""
    if "VERIFICAMOS" in target_type.upper():
        series_override = "Fact-Checked" if niche.upper() in ("NEWS/WORLD", "NEWS", "USA") else "Verificamos"

    return {
        "url":            url,
        "views":          _extract_views(item),
        "caption":        (item.get("caption") or item.get("text") or "")[:300],
        "platform":       "tiktok" if "tiktok" in url.lower() else "instagram",
        "likes":          item.get("likesCount") or item.get("diggCount") or 0,
        "niche":          niche,
        "target_type":    target_type,
        "target_value":   target_value,
        "series_override": series_override,
    }


def scrape_instagram_account(username, niche):
    """Scrape general posts from an account."""
    raw = _run_actor("apify~instagram-scraper", {
        "directUrls":   [f"https://www.instagram.com/{username.lstrip('@')}/"],
        "resultsType":  "posts",
        "resultsLimit": 30,
    })
    return raw, niche, "ACCOUNT", username


def scrape_instagram_reels(username, niche):
    """
    Scrape Reels specifically using the Reel Scraper actor.
    This actor returns real view counts — the general scraper does not.
    Run alongside scrape_instagram_account to capture both post types.
    """
    try:
        raw = _run_actor("apify/instagram-reel-scraper", {
            "username":    username.lstrip("@"),
            "resultsLimit": 20,
        })
        return raw, niche, "REELS", username
    except Exception as e:
        print(f"[scraper] Reel scraper unavailable for {username}: {e} — skipping reels")
        return [], niche, "REELS", username


def scrape_instagram_hashtag(tag, niche, target_type):
    raw = _run_actor("apify~instagram-hashtag-scraper", {
        "hashtags":     [tag.lstrip("#")],
        "resultsLimit": 50,
    })
    return raw, niche, target_type, tag


def filter_and_normalise(raw, niche, target_type, target_value):
    """
    Filter content by performance score.
    Uses view count for Reels/video. Falls back to engagement proxy for image posts.
    Also applies dynamic top-20% threshold so we always pass something when
    all content is below the fixed MIN_VIEWS threshold.
    """
    if not raw:
        return [], 0

    scored = [(item, _extract_score(item)) for item in raw]

    # Dynamic threshold: top 20% of this batch OR MIN_VIEWS, whichever is lower
    scores_sorted = sorted([s for _, s in scored], reverse=True)
    top20_cutoff  = scores_sorted[max(0, len(scores_sorted) // 5 - 1)] if scores_sorted else 0
    threshold     = min(MIN_VIEWS, top20_cutoff) if top20_cutoff > 0 else MIN_VIEWS

    passed, rejected = [], 0
    for item, score in scored:
        if score >= threshold:
            passed.append(_normalise(item, niche, target_type, target_value))
        else:
            rejected += 1

    print(f"[scraper] threshold used: {threshold:,} (fixed={MIN_VIEWS:,}, top20={top20_cutoff:,})")
    return passed, rejected


INSTAGRAM_TYPES = {"ACCOUNT", "HASHTAG", "KEYWORD", "TOPIC COLLECTING", "HASHTAG — VERIFICAMOS"}


def scrape_all_targets(targets):
    """
    targets: {target_type: {niche: [value, ...]}}
    Returns: (results, total_scraped, total_rejected)
    """
    all_results, total_scraped, total_rejected = [], 0, 0

    for target_type, niches in targets.items():
        if target_type not in INSTAGRAM_TYPES:
            print(f"[scraper] Skipping non-Instagram row type: {target_type}")
            continue
        for niche, values in niches.items():
            for value in values:
                if not value.strip():
                    continue
                try:
                    if target_type == "ACCOUNT":
                        # General posts
                        raw, n, tt, tv = scrape_instagram_account(value, niche)
                        total_scraped += len(raw)
                        passed, rejected = filter_and_normalise(raw, n, tt, tv)
                        total_rejected += rejected
                        all_results.extend(passed)
                        print(f"[scraper] ACCOUNT/{niche}/{value}: "
                              f"{len(raw)} scraped -> {len(passed)} passed / {rejected} rejected")

                        # Also scrape Reels separately (returns real view counts)
                        reel_raw, rn, rtt, rtv = scrape_instagram_reels(value, niche)
                        if reel_raw:
                            total_scraped += len(reel_raw)
                            reel_passed, reel_rejected = filter_and_normalise(reel_raw, rn, rtt, rtv)
                            total_rejected += reel_rejected
                            all_results.extend(reel_passed)
                            print(f"[scraper] REELS/{niche}/{value}: "
                                  f"{len(reel_raw)} scraped -> {len(reel_passed)} passed / {reel_rejected} rejected")
                    else:
                        raw, n, tt, tv = scrape_instagram_hashtag(value, niche, target_type)
                        total_scraped += len(raw)
                        passed, rejected = filter_and_normalise(raw, n, tt, tv)
                        total_rejected += rejected
                        all_results.extend(passed)
                        print(f"[scraper] {target_type}/{niche}/{value}: "
                              f"{len(raw)} scraped -> {len(passed)} passed / {rejected} rejected")
                except Exception as e:
                    print(f"[scraper] ERROR on {target_type}/{niche}/{value}: {e}")


def scrape_website_articles(url, niche):
    """
    Fetches article titles + links from a website URL.
    Returns items compatible with save_scraped_to_inspiration_library.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print(f"[scraper] beautifulsoup4 not installed — skipping website scrape for {url}")
        return []

    full_url = url if url.startswith("http") else f"https://{url}"
    try:
        resp = requests.get(full_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:
        print(f"[scraper] Website fetch failed for {url}: {e}")
        return []

    from urllib.parse import urljoin
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    seen = set()

    for tag in soup.find_all(["article", "h2", "h3", "h4"]):
        a = tag.find("a", href=True) or (tag if tag.name == "a" else None)
        if a and a.name == "a":
            title = a.get_text(strip=True)
            href  = urljoin(full_url, a.get("href", ""))
        else:
            title = tag.get_text(strip=True)
            href  = full_url

        JUNK = ["sorry,", "failed to load", "please try again", "no results", "error loading", "no posts found", "coming soon"]
        if not title or len(title) < 10 or title in seen or any(j in title.lower() for j in JUNK):
            continue
        seen.add(title)

        articles.append({
            "url":          href,
            "caption":      title,
            "platform":     "website",
            "content_type": "Blog Idea",
            "views":        0,
            "niche":        niche,
            "target_type":  "WEBSITE",
            "target_value": url,
            "series_override": "",
        })

        if len(articles) >= 20:
            break

    print(f"[scraper] WEBSITE/{niche}/{url}: {len(articles)} articles found")
    return articles

    return all_results, total_scraped, total_rejected
