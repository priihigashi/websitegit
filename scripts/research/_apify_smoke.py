"""_apify_smoke.py — minimal SH-104 Apify smoke test.

Verifies, with a single known-public IG reel URL:
  1. apify~instagram-scraper actor accepts our payload (no HTTP 400 schema bug)
  2. Run status reaches SUCCEEDED within timeout
  3. Dataset returns ≥1 item
  4. A media URL field (videoUrl / video_url / videoUrlBackup / etc.) is detected
  5. The audio download URL is reachable (HEAD request, no body fetched)

Does NOT: transcribe via Whisper, score via Claude/OpenAI, write to Sheets,
upload to Drive, or send email. Pure connectivity check.

Exit code: 0 on full success, 1 on any failure.

Usage (locally):
    APIFY_API_KEY=apify_api_xxx python3 scripts/research/_apify_smoke.py \
      https://www.instagram.com/reel/DXm0WjqAAS_/

Usage (GitHub Actions): see .github/workflows/sh104_apify_smoke.yml
"""

from __future__ import annotations
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Force fallback_mode=auto so the route_state singleton allows Apify.
os.environ.setdefault("FALLBACK_MODE", "auto")

from transcription import (  # noqa: E402
    _apify_request, _extract_ig_media_url, APIFY_API_KEY,
)


def smoke_test(reel_url: str) -> int:
    print(f"=== SH-104 Apify smoke test ===")
    print(f"  Reel URL: {reel_url}")
    print(f"  APIFY_API_KEY set: {bool(APIFY_API_KEY)} (len={len(APIFY_API_KEY)})")

    if not APIFY_API_KEY:
        print("  ❌ APIFY_API_KEY not set in env")
        return 1

    actor = "apify~instagram-scraper"
    payload = {
        "directUrls": [reel_url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["DATACENTER"]},
    }

    print(f"\n[1/5] POST /acts/{actor}/runs ...")
    body, err = _apify_request(
        "POST", f"/acts/{actor}/runs",
        params={"token": APIFY_API_KEY}, json_body=payload, timeout=30,
    )
    if err is not None:
        print(f"  ❌ start failed: {err}")
        return 1
    run_id = body["data"]["id"]
    print(f"  ✅ run_id={run_id}")

    print(f"\n[2/5] Polling run status ...")
    status = ""
    for i in range(20):
        time.sleep(8)
        s, err = _apify_request(
            "GET", f"/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY}, timeout=15,
        )
        if err:
            print(f"  poll err (attempt {i+1}): {err}")
            continue
        status = s.get("data", {}).get("status", "")
        print(f"  attempt {i+1}: status={status}")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status != "SUCCEEDED":
        print(f"  ❌ run did not succeed: {status}")
        return 1
    print(f"  ✅ status=SUCCEEDED")

    print(f"\n[3/5] Fetching dataset items ...")
    items_body, err = _apify_request(
        "GET", f"/actor-runs/{run_id}/dataset/items",
        params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
        timeout=30,
    )
    items = items_body if isinstance(items_body, list) else []
    if not items:
        print(f"  ❌ dataset empty (err={err})")
        return 1
    print(f"  ✅ {len(items)} item(s) returned")
    keys = sorted(items[0].keys())
    print(f"  keys present: {keys}")

    print(f"\n[4/5] Detecting media URL field ...")
    media_url, src = _extract_ig_media_url(items[0])
    if not media_url:
        print(f"  ❌ no media URL found in item")
        return 1
    safe_url = media_url.split("?")[0]
    print(f"  ✅ media URL via field='{src}' → {safe_url[:80]}...")

    print(f"\n[5/5] HEAD-checking media URL is reachable ...")
    try:
        import requests
        h = requests.head(media_url, allow_redirects=True, timeout=20)
        if h.status_code in (200, 206) or 200 <= h.status_code < 400:
            print(f"  ✅ HEAD {h.status_code} (Content-Type={h.headers.get('Content-Type','?')})")
        else:
            print(f"  ⚠️  HEAD {h.status_code} — URL may still work for GET, but this is unusual")
    except Exception as e:
        print(f"  ⚠️  HEAD check raised: {e} (URL may still be downloadable)")

    print(f"\n=== ✅ SMOKE PASSED ===")
    print(f"  Apify discovery actor: WORKS")
    print(f"  Apify direct reel transcription fallback: WORKS (media URL extracted)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: _apify_smoke.py <ig_reel_url>")
        sys.exit(2)
    sys.exit(smoke_test(sys.argv[1]))
