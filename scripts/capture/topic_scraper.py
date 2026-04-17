#!/usr/bin/env python3
"""
topic_scraper.py
================
Topic Cluster Scraper — finds related reels/videos by keywords using Apify,
downloads audio, transcribes (Whisper), classifies (Claude), saves to
Inspiration Library with shared Topic Cluster ID.

WHAT IT DOES:
  1. Takes keywords (extracted from a transcript, or manual input) + niche
  2. Runs Apify actor to scrape related Instagram/TikTok reels
  3. For each reel: download audio → transcribe → classify → save to sheet
  4. All reels share a Topic Cluster ID (column T in Inspiration Library)
  5. Creates calendar task summarising findings

TRIGGER:
  GitHub App → oak-park-ai-hub → Actions → Topic Cluster Scraper → Run workflow
  OR called automatically by capture_pipeline.py (future: --topic-cluster flag)

REQUIRED ENV VARS (GitHub Secrets):
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  GOOGLE_SA_KEY
  APIFY_API_KEY   ← ADD THIS: https://console.apify.com/account/integrations

APEFY ACTORS USED:
  Instagram: apify/instagram-reel-scraper
  TikTok:    clockworks/free-tiktok-scraper
  YouTube:   streamers/youtube-scraper
"""

import os
import sys
import json
import re
import time
import argparse
import tempfile
import base64
import subprocess
import requests
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── CONFIG ──────────────────────────────────────────────────────────────────

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
APEFY_API_KEY     = os.getenv("APIFY_API_KEY", "")

IDEAS_INBOX_ID = os.getenv("IDEAS_INBOX_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")

TRANSCRIPTS_DIR = Path("transcripts")
TRANSCRIPTS_DIR.mkdir(exist_ok=True)

# Apify actor IDs — update if Apify changes these
APIFY_ACTORS = {
    "instagram": "apify/instagram-reel-scraper",
    "tiktok":    "clockworks/free-tiktok-scraper",
    "youtube":   "streamers/youtube-scraper",
}

APIFY_BASE = "https://api.apify.com/v2"


# ─── GOOGLE AUTH ──────────────────────────────────────────────────────────────

def _get_creds(scopes: list):
    from google.oauth2.service_account import Credentials
    sa_b64 = os.getenv("GOOGLE_SA_KEY")
    if sa_b64:
        sa_info = json.loads(base64.b64decode(sa_b64))
        return Credentials.from_service_account_info(sa_info, scopes=scopes)
    creds_path = Path("credentials/service_account.json")
    if creds_path.exists():
        return Credentials.from_service_account_file(str(creds_path), scopes=scopes)
    raise RuntimeError("No Google credentials. Set GOOGLE_SA_KEY secret.")


def get_sheets_client():
    try:
        import gspread
        creds = _get_creds(["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)
    except Exception as e:
        print(f"  SKIP Sheets: {e}")
        return None


def get_calendar_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds(["https://www.googleapis.com/auth/calendar"])
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"  SKIP Calendar: {e}")
        return None


# ─── APIFY SCRAPER ───────────────────────────────────────────────────────────

def scrape_reels(keywords: list, platform: str = "instagram", max_results: int = 10) -> list:
    """Use Apify to find reels by hashtags/keywords. Returns list of reel items."""
    if not APIFY_API_KEY:
        print("  ERROR: APIFY_API_KEY not set — add it to GitHub Secrets")
        print("  Get key at: https://console.apify.com/account/integrations")
        return []

    actor_id = APIFY_ACTORS.get(platform, APIFY_ACTORS["instagram"])
    print(f"\n[APIFY] Platform: {platform} | Actor: {actor_id}")
    print(f"[APIFY] Keywords: {keywords}")

    # Build actor input based on platform
    hashtags = [kw.strip().replace(" ", "").replace("#", "") for kw in keywords if kw.strip()]
    if platform == "instagram":
        input_data = {
            "hashtags": hashtags[:5],
            "resultsLimit": max_results,
            "scrapePostsUntilDate": (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"),
        }
    elif platform == "tiktok":
        input_data = {
            "hashtags": hashtags[:5],
            "maxItems": max_results,
        }
    else:  # youtube
        input_data = {
            "searchKeywords": keywords[:3],
            "maxResults": max_results,
            "type": "video",
        }

    try:
        # Start Apify actor run
        run_resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            params={"token": APIFY_API_KEY},
            json={"runInput": input_data},
            timeout=30,
        )
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        print(f"  Apify run started: {run_id}")

        # Poll until finished (max ~3 minutes)
        for attempt in range(18):
            time.sleep(10)
            status_resp = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": APIFY_API_KEY},
                timeout=15,
            )
            status = status_resp.json()["data"]["status"]
            print(f"  Status: {status} ({attempt+1}/18)")
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if status != "SUCCEEDED":
            print(f"  WARNING: Apify run ended with status: {status}")
            return []

        # Fetch results from dataset
        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "limit": max_results, "format": "json"},
            timeout=30,
        )
        items = items_resp.json()
        print(f"  Found {len(items)} items from Apify")
        return items

    except Exception as e:
        print(f"  ERROR Apify: {e}")
        return []


def extract_reel_urls(items: list, platform: str = "instagram") -> list:
    """Extract playable reel URLs from Apify actor output."""
    urls = []
    for item in items:
        url = (
            item.get("url")
            or (item.get("shortCode") and f"https://www.instagram.com/reel/{item['shortCode']}/")
            or item.get("webVideoUrl")
            or item.get("videoUrl")
            or item.get("shareUrl")
        )
        if url and isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    return list(dict.fromkeys(urls))  # deduplicate, preserve order


# ─── DOWNLOAD + TRANSCRIBE ────────────────────────────────────────────────────

def download_audio(url: str, tmp_dir: str) -> str:
    """Download audio from reel URL using yt-dlp."""
    output = os.path.join(tmp_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "0", "--output", output,
        "--no-playlist", "--quiet", url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:200]}")
    mp3 = os.path.join(tmp_dir, "audio.mp3")
    if not os.path.exists(mp3):
        for ext in ["m4a", "webm", "ogg", "wav"]:
            alt = os.path.join(tmp_dir, f"audio.{ext}")
            if os.path.exists(alt):
                mp3 = alt
                break
    size_kb = os.path.getsize(mp3) / 1024 if os.path.exists(mp3) else 0
    print(f"  Downloaded ({size_kb:.0f} KB)")
    return mp3


def transcribe_audio(audio_path: str) -> str:
    """Transcribe audio file using OpenAI Whisper."""
    if not OPENAI_API_KEY:
        return "[TRANSCRIPTION SKIPPED — OPENAI_API_KEY not set]"
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="text"
        )
    print(f"  Transcribed ({len(result)} chars)")
    return result


def classify_content(transcript: str, url: str, niche: str, cluster_id: str) -> dict:
    """Classify transcript using Claude Sonnet."""
    if not ANTHROPIC_API_KEY:
        return {"niche": niche, "classification": "NEEDS_REVIEW", "summary": transcript[:150]}
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""Classify this video transcript for Oak Park Construction content pipeline.
Niche hint: {niche}
URL: {url}
Topic Cluster: {cluster_id}
TRANSCRIPT: {transcript}

Fake news / misinformation detection: Does this content contain or spread a specific false or misleading claim (viral myth, fabricated statistic, doctored quote, out-of-context clip)? If yes, set fake_news_route "A" (source clip of spreader available) or "B" (expert/outlet already debunked). Brazil/bilingual niche = series_override "Verificamos". USA niche = series_override "Fact-Checked".

Respond with JSON only:
{{"niche": "Oak Park" or "Brazil" or "UGC" or "News", "content_type": "Talking Head/Expert" or "Project Progress/Before-After" or "Product Tips" or "Other", "classification": "READY" or "NEEDS_REVIEW" or "NOT_RELEVANT", "summary": "one sentence", "hook": "suggested hook for repost or inspiration", "notes": "why classified this way", "series_override": "Verificamos" or "Fact-Checked" or "", "fake_news_route": "A" or "B" or "", "fake_news_confidence": "high" or "medium" or "low" or ""}}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {"niche": niche, "classification": "NEEDS_REVIEW", "summary": text[:150]}


# ─── SAVE TO SHEETS ───────────────────────────────────────────────────────────

def save_to_inspiration_library(url: str, transcript: str, cl: dict,
                                 topic_cluster_id: str, original_url: str = ""):
    """Append row to Inspiration Library using header-name lookup (resilient to reorder)."""
    gc = get_sheets_client()
    if not gc:
        return
    try:
        sh = gc.open_by_key(IDEAS_INBOX_ID)
        lib = sh.worksheet("\U0001f4e5 Inspiration Library")

        # Resolve all columns by header name — never use positional index
        headers = lib.row_values(1)
        col_pos = {h.strip().lower(): i for i, h in enumerate(headers)}

        def _set_col(row, col_name, value):
            idx = col_pos.get(col_name.lower())
            if idx is not None:
                while len(row) <= idx:
                    row.append("")
                row[idx] = str(value) if value is not None else ""

        base_row = []
        _set_col(base_row, "date added",        datetime.now().strftime("%Y-%m-%d"))
        _set_col(base_row, "content hub link",  "")  # topic scraper has no hub path
        _set_col(base_row, "platform",          "Instagram")
        _set_col(base_row, "url",               url)
        _set_col(base_row, "creator / account", "")
        _set_col(base_row, "content type",      cl.get("content_type", ""))
        _set_col(base_row, "description",       cl.get("summary", ""))
        _set_col(base_row, "transcription",     transcript[:300])
        _set_col(base_row, "original caption",  "")
        _set_col(base_row, "visual hook",       cl.get("hook", ""))
        _set_col(base_row, "hook type",         "")
        _set_col(base_row, "what's working",    cl.get("notes", ""))
        _set_col(base_row, "brief / angle",     f"Topic cluster from: {original_url}" if original_url else "")
        _set_col(base_row, "status",            cl.get("classification", "NEEDS_REVIEW"))
        _set_col(base_row, "topic / title",     topic_cluster_id)

        lib.append_row(base_row, value_input_option="USER_ENTERED")
        print(f"  Saved → Inspiration Library (cluster: {topic_cluster_id})")
    except Exception as e:
        print(f"  WARNING Sheets: {e}")


# ─── CALENDAR TASK ────────────────────────────────────────────────────────────

def create_calendar_task(cluster_id: str, keywords: list, urls: list,
                          niche: str, original_url: str):
    cal = get_calendar_service()
    if not cal:
        return
    tomorrow = (datetime.now() + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    urls_list = "\n".join([f"  - {u}" for u in urls[:10]])
    try:
        cal.events().insert(calendarId="primary", body={
            "summary": f"TOPIC CLUSTER — {cluster_id} — Review {len(urls)} reels [{niche}]",
            "description": (
                f"Cluster ID: {cluster_id}\nNiche: {niche}\n"
                f"Keywords: {', '.join(keywords)}\n"
                f"Original URL: {original_url or 'manual trigger'}\n\n"
                f"REELS SCRAPED ({len(urls)}):\n{urls_list}\n\n"
                f"NEXT STEPS:\n"
                f"1. Open Inspiration Library → filter col T = {cluster_id}\n"
                f"   https://docs.google.com/spreadsheets/d/1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU\n"
                f"2. Mark best reels READY\n"
                f"3. Add to Clip Collections if 8-10 on same topic\n"
                f"4. Brief Mike/Matt on top findings"
            ),
            "start": {"dateTime": tomorrow.isoformat(), "timeZone": "America/New_York"},
            "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat(),
                    "timeZone": "America/New_York"},
        }).execute()
        print(f"  Calendar task created: tomorrow 9am ET")
    except Exception as e:
        print(f"  WARNING Calendar: {e}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Topic Cluster Scraper")
    parser.add_argument("--keywords", required=True,
                        help="Comma-separated keywords from transcript")
    parser.add_argument("--niche", default="Brazil",
                        choices=["Oak Park", "Brazil", "UGC", "News"])
    parser.add_argument("--original-url", default="",
                        help="Source reel that triggered this scrape")
    parser.add_argument("--topic-cluster-id", default=None,
                        help="ID to group related reels (auto-generated if blank)")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--platform", default="instagram",
                        choices=["instagram", "tiktok", "youtube"])
    args = parser.parse_args()

    if not args.topic_cluster_id:
        args.topic_cluster_id = f"TC-{datetime.now().strftime('%Y%m%d%H%M')}"

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    print(f"\n{'='*55}")
    print(f"TOPIC CLUSTER SCRAPER")
    print(f"Cluster ID : {args.topic_cluster_id}")
    print(f"Keywords   : {keywords}")
    print(f"Niche      : {args.niche}")
    print(f"Platform   : {args.platform}")
    print(f"Max results: {args.max_results}")
    print(f"{'='*55}")

    # 1. Scrape
    items = scrape_reels(keywords, args.platform, args.max_results)
    urls  = extract_reel_urls(items, args.platform)

    if not urls:
        print(f"\n  No reels found. Check APIFY_API_KEY and keyword spelling.")
        sys.exit(0)

    print(f"\n  {len(urls)} unique reel URLs to process")

    # 2. Process each reel
    processed = []
    for i, url in enumerate(urls[:args.max_results]):
        print(f"\n--- [{i+1}/{len(urls)}] {url[:70]} ---")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                audio      = download_audio(url, tmp)
                transcript = transcribe_audio(audio)

            slug = url.split("/reel/")[-1].split("/")[0].split("?")[0] \
                   if "/reel/" in url else f"reel_{i}"
            fp = TRANSCRIPTS_DIR / f"{args.topic_cluster_id}_{slug}_transcript.txt"
            fp.write_text(
                f"TOPIC CLUSTER: {args.topic_cluster_id}\n"
                f"URL: {url}\nKEYWORDS: {keywords}\n\n{transcript}",
                encoding="utf-8"
            )

            cl = classify_content(transcript, url, args.niche, args.topic_cluster_id)
            save_to_inspiration_library(url, transcript, cl, args.topic_cluster_id, args.original_url)
            processed.append(url)

        except Exception as e:
            print(f"  SKIP: {e}")
            continue

    # 3. Calendar task
    create_calendar_task(args.topic_cluster_id, keywords, processed,
                         args.niche, args.original_url)

    print(f"\n{'='*55}")
    print(f"DONE — {len(processed)}/{len(urls)} reels processed")
    print(f"Cluster ID: {args.topic_cluster_id}")
    print(f"Filter Inspiration Library col T = {args.topic_cluster_id}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
