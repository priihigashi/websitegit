"""
broll_finder.py — Finds 3-5 free B-roll clips per script topic using Pexels API.
Returns portrait-oriented video clips for Reels / TikTok format.
"""
import os, requests

PEXELS_API_KEY = os.environ["PEXELS_API_KEY"]
PEXELS_BASE    = "https://api.pexels.com/videos"


def _best_file(video_files):
    """Pick highest-resolution video file from Pexels video_files list."""
    if not video_files:
        return {}
    return sorted(video_files, key=lambda f: f.get("width", 0), reverse=True)[0]


def search_pexels(query, per_page=15, orientation="portrait"):
    headers  = {"Authorization": PEXELS_API_KEY}
    response = requests.get(
        f"{PEXELS_BASE}/search",
        headers=headers,
        params={"query": query, "per_page": per_page, "orientation": orientation},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("videos", [])


def get_broll_for_script(topic, script_text, count=5):
    """
    Find 3-5 B-roll clips relevant to a topic.
    Falls back to a broader search if the specific topic returns few results.
    """
    videos = search_pexels(topic, per_page=count + 5)

    # Fallback: try key words from the script if topic returns nothing
    if len(videos) < 3:
        keywords = " ".join([w for w in script_text.split()[:10] if len(w) > 4])
        videos += search_pexels(keywords, per_page=count)

    clips = []
    for video in videos[:count]:
        best = _best_file(video.get("video_files", []))
        clips.append({
            "pexels_url":    video.get("url", ""),
            "download_url":  best.get("link", ""),
            "duration_secs": video.get("duration", 0),
            "width":         best.get("width", 0),
            "height":        best.get("height", 0),
        })

    return clips
