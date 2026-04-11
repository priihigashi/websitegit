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


def _normalise(item, niche, target_type, target_value):
    url = item.get("url") or (
        f"https://www.instagram.com/p/{item['shortCode']}/"
        if item.get("shortCode") else ""
    )
    return {
        "url":          url,
        "views":        _extract_views(item),
        "caption":      (item.get("caption") or item.get("text") or "")[:300],
        "platform":     "tiktok" if "tiktok" in url.lower() else "instagram",
        "likes":        item.get("likesCount") or item.get("diggCount") or 0,
        "niche":        niche,
        "target_type":  target_type,
        "target_value": target_value,
    }


def scrape_instagram_account(username, niche):
    raw = _run_actor("apify/instagram-scraper", {
        "directUrls":   [f"https://www.instagram.com/{username.lstrip('@')}/"],
        "resultsType":  "posts",
        "resultsLimit": 30,
    })
    return raw, niche, "ACCOUNT", username


def scrape_instagram_hashtag(tag, niche, target_type):
    raw = _run_actor("apify/instagram-hashtag-scraper", {
        "hashtags":     [tag.lstrip("#")],
        "resultsLimit": 50,
    })
    return raw, niche, target_type, tag


def filter_and_normalise(raw, niche, target_type, target_value):
    passed, rejected = [], 0
    for item in raw:
        if _extract_views(item) >= MIN_VIEWS:
            passed.append(_normalise(item, niche, target_type, target_value))
        else:
            rejected += 1
    return passed, rejected


def scrape_all_targets(targets):
    """
    targets: {target_type: {niche: [value, ...]}}
    Returns: (results, total_scraped, total_rejected)
    """
    all_results, total_scraped, total_rejected = [], 0, 0

    for target_type, niches in targets.items():
        for niche, values in niches.items():
            for value in values:
                if not value.strip():
                    continue
                try:
                    if target_type == "ACCOUNT":
                        raw, n, tt, tv = scrape_instagram_account(value, niche)
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

    return all_results, total_scraped, total_rejected
