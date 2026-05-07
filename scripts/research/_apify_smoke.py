"""_apify_smoke.py — minimal SH-104 Apify smoke test.

Probes apify~instagram-scraper against a known public reel and reports
whether the actor schema is correct AND whether the proxy can actually
reach Instagram. Two-tier probe:

  1. Default Apify proxy (cheap, sometimes blocked by IG)
  2. RESIDENTIAL proxy   (4-8× cost, usually bypasses IG block)

Distinguishes:
  - Schema/code bug      (HTTP 400 / 422 → "fix the code")
  - Quota/billing issue  (HTTP 402 / 403 / "credit balance too low")
  - IG soft-fail         (actor SUCCEEDED but item has `error` field)
  - Real success         (post fields + media URL detected)

Exit code: 0 only if ANY proxy tier succeeded.

Usage (locally):
    APIFY_API_KEY=apify_api_xxx python3 scripts/research/_apify_smoke.py \
      https://www.instagram.com/reel/DXm0WjqAAS_/
"""

from __future__ import annotations
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

os.environ.setdefault("FALLBACK_MODE", "auto")

from transcription import (  # noqa: E402
    _apify_request, _extract_ig_media_url, _is_soft_fail_item, APIFY_API_KEY,
)


def _probe(reel_url: str, proxy_groups: list[str] | None, label: str) -> str:
    """Returns one of: 'ok' | 'soft_fail' | 'schema_error' | 'quota' |
    'run_failed' | 'no_items' | 'no_media'. Prints a structured report."""
    print(f"\n--- Probe: {label} ---")
    actor = "apify~instagram-scraper"
    proxy: dict = {"useApifyProxy": True}
    if proxy_groups:
        proxy["apifyProxyGroups"] = proxy_groups
    payload = {
        "directUrls": [reel_url.split("?")[0]],
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
        "proxy": proxy,
    }
    body, err = _apify_request(
        "POST", f"/acts/{actor}/runs",
        params={"token": APIFY_API_KEY}, json_body=payload, timeout=30,
    )
    if err is not None:
        low = err.lower()
        if "402" in err or "403" in err or "credit" in low or "billing" in low or "quota" in low:
            print(f"  ❌ QUOTA / BILLING: {err[:300]}")
            return "quota"
        if "400" in err or "422" in err or "schema" in low or "input" in low:
            print(f"  ❌ SCHEMA / INPUT BUG: {err[:300]}")
            return "schema_error"
        print(f"  ❌ start failed: {err[:300]}")
        return "run_failed"
    run_id = body["data"]["id"]
    print(f"  ✅ run_id={run_id}")

    status = ""
    for i in range(20):
        time.sleep(8)
        s, perr = _apify_request(
            "GET", f"/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY}, timeout=15,
        )
        if perr or not isinstance(s, dict):
            continue
        status = s.get("data", {}).get("status", "")
        print(f"  attempt {i+1}: status={status}")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status != "SUCCEEDED":
        print(f"  ❌ run did not succeed: {status}")
        return "run_failed"

    items_body, _ = _apify_request(
        "GET", f"/actor-runs/{run_id}/dataset/items",
        params={"token": APIFY_API_KEY, "limit": 1, "format": "json"},
        timeout=30,
    )
    items = items_body if isinstance(items_body, list) else []
    if not items:
        print(f"  ❌ dataset empty")
        return "no_items"

    item = items[0]
    keys = sorted(item.keys())[:30]
    print(f"  keys: {keys}")

    is_soft, soft_msg = _is_soft_fail_item(item)
    if is_soft:
        print(f"  ⚠️  IG soft-fail: {soft_msg[:300]}")
        return "soft_fail"

    media_url, src = _extract_ig_media_url(item)
    if not media_url:
        print(f"  ❌ no media URL field detected")
        return "no_media"

    safe = media_url.split("?")[0]
    print(f"  ✅ media URL via field='{src}' → {safe[:80]}...")
    try:
        import requests
        h = requests.head(media_url, allow_redirects=True, timeout=20)
        print(f"  HEAD {h.status_code} (Content-Type={h.headers.get('Content-Type','?')})")
    except Exception as e:
        print(f"  HEAD raised: {e} (URL may still be downloadable)")
    return "ok"


def smoke_test(reel_url: str) -> int:
    print(f"=== SH-104 Apify smoke test ===")
    print(f"  Reel URL: {reel_url}")
    print(f"  APIFY_API_KEY set: {bool(APIFY_API_KEY)} (len={len(APIFY_API_KEY)})")

    if not APIFY_API_KEY:
        print("  ❌ APIFY_API_KEY not set in env")
        return 1

    # Tier 1: default proxy
    r1 = _probe(reel_url, proxy_groups=None, label="default Apify proxy")
    if r1 == "ok":
        print("\n=== ✅ SMOKE PASSED (default proxy) ===")
        return 0
    if r1 == "schema_error":
        print("\n=== ❌ SCHEMA / CODE BUG — fix actor input before retrying ===")
        return 1
    if r1 == "quota":
        print("\n=== ❌ QUOTA / BILLING — Apify credit needed ===")
        return 1

    # Tier 2: RESIDENTIAL retry only on soft_fail (proxy-fixable)
    if r1 == "soft_fail":
        print("\n  → soft-fail justifies RESIDENTIAL retry")
        r2 = _probe(reel_url, proxy_groups=["RESIDENTIAL"], label="RESIDENTIAL proxy")
        if r2 == "ok":
            print("\n=== ✅ SMOKE PASSED (RESIDENTIAL proxy) ===")
            print("  Recommendation: pipeline will auto-retry with RESIDENTIAL on soft-fail.")
            return 0
        if r2 == "soft_fail":
            print("\n=== ⚠️  RESIDENTIAL ALSO BLOCKED — IG actor cannot reach this reel ===")
            print("  Likely causes: (a) reel is private/deleted, (b) actor needs IG cookies,")
            print("  (c) IG flagged the proxy pool. Try a different reel URL.")
            return 1
        print(f"\n=== ❌ RESIDENTIAL probe: {r2} ===")
        return 1

    print(f"\n=== ❌ Default-proxy probe: {r1} (not retryable via proxy) ===")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: _apify_smoke.py <ig_reel_url>")
        sys.exit(2)
    sys.exit(smoke_test(sys.argv[1]))
