"""
broll_finder.py — Finds B-roll clips per script topic.
Sources: Pexels (free stock) + YouTube (real-world clips via Data API).
Returns portrait-oriented clips for Reels / TikTok format.
"""
import os, requests

PEXELS_API_KEY  = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
PEXELS_BASE     = "https://api.pexels.com/videos"
PIXABAY_BASE    = "https://pixabay.com/api/videos"
YOUTUBE_BASE    = "https://www.googleapis.com/youtube/v3"


def _best_file(video_files):
    if not video_files:
        return {}
    return sorted(video_files, key=lambda f: f.get("width", 0), reverse=True)[0]


def search_pexels(query, per_page=10, orientation="portrait"):
    if not PEXELS_API_KEY:
        return []
    try:
        response = requests.get(
            f"{PEXELS_BASE}/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": per_page, "orientation": orientation},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("videos", [])
    except Exception as e:
        print(f"[broll] Pexels error for '{query}': {e}")
        return []


def search_pixabay(query, per_page=10):
    """Fallback to Pixabay if Pexels returns too few results. Returns Pexels-shaped dicts."""
    if not PIXABAY_API_KEY:
        return []
    try:
        response = requests.get(
            PIXABAY_BASE,
            params={"key": PIXABAY_API_KEY, "q": query, "per_page": max(per_page, 3), "video_type": "film"},
            timeout=15,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
        videos = []
        for h in hits:
            vfiles = h.get("videos", {})
            best = (vfiles.get("large") or vfiles.get("medium") or vfiles.get("small") or {})
            if not best.get("url"):
                continue
            videos.append({
                "url":          h.get("pageURL", ""),
                "duration":     h.get("duration", 0),
                "video_files":  [{
                    "link":   best.get("url", ""),
                    "width":  best.get("width", 0),
                    "height": best.get("height", 0),
                }],
            })
        return videos
    except Exception as e:
        print(f"[broll] Pixabay error for '{query}': {e}")
        return []


def search_youtube(query, max_results=5, published_after=None):
    """Search YouTube for recent videos matching query. Returns list of clip dicts."""
    if not YOUTUBE_API_KEY:
        return []
    try:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "order": "relevance",
            "videoDuration": "short",   # under 4 minutes — better for B-roll
            "key": YOUTUBE_API_KEY,
        }
        if published_after:
            params["publishedAfter"] = published_after  # e.g. "2025-01-01T00:00:00Z"

        response = requests.get(
            f"{YOUTUBE_BASE}/search",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        clips = []
        for item in items:
            vid_id = item["id"].get("videoId", "")
            if not vid_id:
                continue
            snippet = item.get("snippet", {})
            clips.append({
                "source":       "youtube",
                "youtube_url":  f"https://www.youtube.com/watch?v={vid_id}",
                "pexels_url":   "",
                "download_url": "",
                "title":        snippet.get("title", ""),
                "channel":      snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt", ""),
                "duration_secs": 0,
                "width":        0,
                "height":       0,
            })
        return clips
    except Exception as e:
        print(f"[broll] YouTube error for '{query}': {e}")
        return []


def get_broll_for_script(topic, script_text, count=4):  # 4 Pexels + 4 YouTube = 8 max total
    """
    Find B-roll clips from Pexels (free stock) and YouTube (real-world).
    Returns combined list — up to count from each source.
    """
    # Pexels: free stock footage
    pexels_videos = search_pexels(topic, per_page=count + 5)
    if len(pexels_videos) < 3:
        keywords = " ".join([w for w in script_text.split()[:10] if len(w) > 4])
        pexels_videos += search_pexels(keywords, per_page=count)

    # Pixabay fallback — fill out remaining slots if Pexels came up short
    stock_source = "pexels"
    if len(pexels_videos) < count:
        missing = count - len(pexels_videos)
        pixabay_videos = search_pixabay(topic, per_page=missing + 2)
        if pixabay_videos:
            pexels_videos += pixabay_videos
            stock_source = "mixed" if pexels_videos[:count - missing] else "pixabay"

    pexels_clips = []
    for video in pexels_videos[:count]:
        best = _best_file(video.get("video_files", []))
        pexels_clips.append({
            "source":       stock_source,
            "pexels_url":   video.get("url", ""),
            "youtube_url":  "",
            "download_url": best.get("link", ""),
            "title":        "",
            "channel":      "",
            "published_at": "",
            "duration_secs": video.get("duration", 0),
            "width":        best.get("width", 0),
            "height":       best.get("height", 0),
        })

    # YouTube: real-world clips, last 90 days
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    youtube_clips = search_youtube(topic, max_results=count, published_after=cutoff)[:4]

    all_clips = pexels_clips + youtube_clips
    print(f"[broll] '{topic}': {len(pexels_clips)} Pexels + {len(youtube_clips)} YouTube = {len(all_clips)} total")
    return all_clips
