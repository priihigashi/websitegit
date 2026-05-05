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
    # Auto-tag by target type
    series_override = ""
    fake_news_route = ""
    if "VERIFICAMOS" in target_type.upper():
        series_override = "Fact-Checked" if niche.upper() in ("NEWS/WORLD", "NEWS", "USA") else "Verificamos"
    elif "DEBUNK SOURCE" in target_type.upper():
        series_override = "Verdade Pela Metade"
        fake_news_route = "debunk"  # refined to mode_a/mode_b by Haiku classifier (GAP 4)

    return {
        "url":             url,
        "views":           _extract_views(item),
        "caption":         (item.get("caption") or item.get("text") or "")[:300],
        "platform":        "tiktok" if "tiktok" in url.lower() else "instagram",
        "likes":           item.get("likesCount") or item.get("diggCount") or 0,
        "niche":           niche,
        "target_type":     target_type,
        "target_value":    target_value,
        "series_override": series_override,
        "fake_news_route": fake_news_route,
    }


def _post_timestamp(item):
    """Return the post's Unix timestamp (float). Falls back to 0 if unavailable."""
    ts = item.get("timestamp") or item.get("takenAtTimestamp") or item.get("taken_at") or 0
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _fetch_inspo_urls():
    """Return a set of normalized URLs already saved in Inspiration Library column C."""
    import json, urllib.request, urllib.parse
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        return set()
    try:
        td = json.loads(raw)
        data = urllib.parse.urlencode({
            "client_id": td["client_id"], "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
        token = resp["access_token"]
    except Exception as e:
        print(f"[debunk] _fetch_inspo_urls auth failed: {e}")
        return set()

    sheet_id = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
    enc = urllib.parse.quote("'📥 Inspiration Library'!C:C", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read()
        ).get("values", [])
        return {row[0].strip() for row in rows[1:] if row and row[0].strip()}
    except Exception as e:
        print(f"[debunk] _fetch_inspo_urls sheet read failed: {e}")
        return set()


def scrape_debunk_source(username, niche):
    """Scrape debunk source account — top 1 non-duplicate post from past 7 days.
    Runs on Tuesdays only. Returns a single normalised item dict, or None."""
    if datetime.today().weekday() != 1:  # 0=Mon, 1=Tue, ..., 6=Sun
        print("[debunk] Not Tuesday, skipping")
        return None

    cutoff = datetime.now(et).timestamp() - 7 * 86400

    raw, _, _, _ = scrape_instagram_account(username, niche)
    if not raw:
        print(f"[debunk] No posts returned for {username}")
        return None

    # Filter to posts from past 7 days
    recent = [item for item in raw if _post_timestamp(item) >= cutoff]
    if not recent:
        print(f"[debunk] No posts within 7 days for {username} — using full batch as fallback")
        recent = raw

    # Sort by engagement score descending
    recent.sort(key=_extract_score, reverse=True)

    # Dedup against Inspiration Library
    existing_urls = _fetch_inspo_urls()

    for item in recent:
        short_code = item.get("shortCode", "")
        url = item.get("url") or (
            f"https://www.instagram.com/p/{short_code}/" if short_code else ""
        )
        if not url:
            continue
        # Skip if shortCode already appears in any saved URL
        if url in existing_urls or (short_code and any(short_code in u for u in existing_urls)):
            print(f"[debunk] {short_code} already in Inspiration Library — skipping")
            continue
        normalised = _normalise(item, niche, "DEBUNK SOURCE", username)
        # GAP 4: classify caption as mode_a or mode_b via Haiku
        mode = _classify_debunk_mode(normalised["caption"])
        normalised["fake_news_route"] = mode
        # GAP 5: mode_a only — Sonnet attribution research
        if mode == "mode_a":
            normalised["research_brief"] = _research_attribution(normalised["caption"])
        return normalised

    print(f"[debunk] All candidates already in Inspiration Library for {username}")
    return None


def _research_attribution(caption):
    """Sonnet call: find who is actually responsible for the claim in the caption.
    Returns a JSON string with keys: responsible_party, decision_name, year, source_url."""
    import json as _json, urllib.request as _req
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    if not api_key:
        print("[debunk] No ANTHROPIC_API_KEY — skipping attribution research")
        return ""
    prompt = (
        f"Given this claim: {caption[:300]}\n\n"
        "Who is actually responsible? Find the legislation, vote record, or government decision. "
        "Return JSON with keys: responsible_party, decision_name, year, source_url."
    )
    try:
        body = _json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = _req.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp = _json.loads(_req.urlopen(request, timeout=60).read())
        result = resp["content"][0]["text"].strip()
        print(f"[debunk] Sonnet attribution research complete ({len(result)} chars)")
        return result
    except Exception as e:
        print(f"[debunk] Sonnet research failed: {e}")
        return ""


def _classify_debunk_mode(caption):
    """Haiku classifier: returns 'mode_a' (wrong attribution) or 'mode_b' (distorted numbers).
    Uses caption text as the transcript proxy since scraper has no Whisper access."""
    import json as _json, urllib.request as _req
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    if not api_key:
        print("[debunk] No ANTHROPIC_API_KEY — defaulting to mode_b")
        return "mode_b"
    prompt = (
        "Given this transcript, classify: "
        "(A) real fact pinned to wrong person/government, or "
        "(B) fear/exaggeration with real but distorted numbers. "
        "Return one word: mode_a or mode_b.\n\n"
        f"Transcript: {caption[:800]}"
    )
    try:
        body = _json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = _req.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp = _json.loads(_req.urlopen(request, timeout=30).read())
        text = resp["content"][0]["text"].strip().lower()
        result = "mode_a" if "mode_a" in text else "mode_b"
        print(f"[debunk] Haiku classified: {result}")
        return result
    except Exception as e:
        print(f"[debunk] Haiku classify failed: {e} — defaulting mode_b")
        return "mode_b"


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
        "resultsLimit": 12,
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


INSTAGRAM_TYPES = {"ACCOUNT", "HASHTAG", "KEYWORD", "TOPIC COLLECTING", "HASHTAG — VERIFICAMOS", "DEBUNK SOURCE"}


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
                    elif target_type == "DEBUNK SOURCE":
                        item = scrape_debunk_source(value, niche)
                        if item:
                            total_scraped += 1
                            all_results.append(item)
                            print(f"[scraper] DEBUNK SOURCE/{niche}/{value}: 1 candidate queued")
                        else:
                            print(f"[scraper] DEBUNK SOURCE/{niche}/{value}: no candidate (skipped or not Tuesday)")
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
