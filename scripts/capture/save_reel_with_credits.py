#!/usr/bin/env python3
"""
save_reel_with_credits.py
=========================
Saves an Instagram reel URL to the Inspiration Library with credits info.

FYI: This script uses the Apify API (apify/instagram-scraper) to fetch
the reel's creator handle, name, caption, and stats — so we can give
proper credit in captions when reposting.

USAGE:
  python scripts/capture/save_reel_with_credits.py \
    "https://www.instagram.com/reel/XXXX/" \
    --notes "Verify claims about XYZ"

  With manual credits (if Apify is unavailable):
  python scripts/capture/save_reel_with_credits.py \
    "https://www.instagram.com/reel/XXXX/" \
    --creator-handle "getbetterwithbooks" \
    --creator-name "Get Better With Books" \
    --notes "Book reco for content later"

REQUIRED ENV VARS:
  GOOGLE_SA_KEY   — base64-encoded service account JSON
  APIFY_API_KEY   — for auto-fetching creator info (optional if --creator-handle set)
"""

import os
import sys
import json
import time
import argparse
import base64
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")
APIFY_BASE = "https://api.apify.com/v2"
IDEAS_INBOX_ID = os.getenv("IDEAS_INBOX_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")


def _get_creds():
    from google.oauth2.service_account import Credentials
    sa_b64 = os.getenv("GOOGLE_SA_KEY")
    if sa_b64:
        sa_info = json.loads(base64.b64decode(sa_b64))
        return Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    raise RuntimeError("GOOGLE_SA_KEY not set")


def fetch_reel_metadata(url):
    """Fetch reel metadata via Apify. Returns dict with creator info."""
    if not APIFY_API_KEY or not requests:
        return {}
    if "instagram.com" not in url:
        return {}

    print("[APIFY] Fetching reel metadata...")
    actor_id = "apify/instagram-scraper"
    input_data = {
        "directUrls": [url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }

    try:
        run_resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/runs",
            params={"token": APIFY_API_KEY},
            json=input_data,
            timeout=30,
        )
        run_resp.raise_for_status()
        run_id = run_resp.json()["data"]["id"]
        print(f"  Apify run: {run_id}")

        for attempt in range(12):
            time.sleep(10)
            status_resp = requests.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": APIFY_API_KEY},
                timeout=15,
            )
            status = status_resp.json()["data"]["status"]
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break

        if status != "SUCCEEDED":
            print(f"  WARNING: Apify ended with {status}")
            return {}

        items_resp = requests.get(
            f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items",
            params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
            timeout=30,
        )
        items = items_resp.json()
        if not items:
            return {}

        item = items[0]
        return {
            "creator_handle": item.get("ownerUsername", ""),
            "creator_name": item.get("ownerFullName", ""),
            "caption": item.get("caption", ""),
            "likes": item.get("likesCount", 0),
            "comments": item.get("commentsCount", 0),
            "views": item.get("videoViewCount", 0),
        }
    except Exception as e:
        print(f"  WARNING Apify: {e}")
        return {}


def save_to_inspiration_library(url, metadata, notes):
    # DISABLED: this function used the old 9-column schema and will corrupt the sheet.
    # Use capture_pipeline.py:update_inspiration_library() instead.
    print("WARNING: legacy write to Inspiration Library blocked — use capture_pipeline.py")
    return
    import gspread
    creds = _get_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(IDEAS_INBOX_ID)
    lib = sh.worksheet("📥 Inspiration Library")

    creator = metadata.get("creator_handle", "unknown")
    caption = metadata.get("caption", "")
    credits_line = f"Credit: @{creator}"
    if metadata.get("creator_name"):
        credits_line += f" ({metadata['creator_name']})"

    lib.append_row([
        datetime.now().strftime("%Y-%m-%d"),           # Date
        url,                                            # URL
        caption[:200] if caption else notes,            # Summary
        "Oak Park",                                     # Niche
        "Talking Head/Expert",                          # Content Type
        "SAVED",                                        # Status
        notes or "",                                    # Notes
        "",                                             # Hook
        credits_line,                                   # Credits
    ])
    print(f"  Saved to Inspiration Library with credits: {credits_line}")


def main():
    parser = argparse.ArgumentParser(description="Save reel with credits")
    parser.add_argument("url", help="Instagram reel URL")
    parser.add_argument("--creator-handle", default="", help="Manual creator @handle")
    parser.add_argument("--creator-name", default="", help="Manual creator name")
    parser.add_argument("--notes", default="", help="Notes about this reel")
    args = parser.parse_args()

    # Try Apify first, fall back to manual args
    metadata = fetch_reel_metadata(args.url)
    if not metadata and args.creator_handle:
        metadata = {
            "creator_handle": args.creator_handle,
            "creator_name": args.creator_name,
        }

    if not metadata:
        print("WARNING: No creator info available. Set APIFY_API_KEY or use --creator-handle")
        metadata = {"creator_handle": "unknown", "creator_name": ""}

    print(f"\nReel: {args.url}")
    print(f"Creator: @{metadata.get('creator_handle', 'unknown')}")
    print(f"Notes: {args.notes}")

    try:
        save_to_inspiration_library(args.url, metadata, args.notes)
        print("\nDone! Reel saved to Inspiration Library.")
    except Exception as e:
        print(f"\nERROR saving to sheet: {e}")
        print("Reel info for manual entry:")
        print(f"  URL: {args.url}")
        print(f"  Creator: @{metadata.get('creator_handle', 'unknown')}")
        print(f"  Notes: {args.notes}")
        sys.exit(1)


if __name__ == "__main__":
    main()
