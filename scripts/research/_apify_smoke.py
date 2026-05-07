"""_apify_smoke.py — SH-104 Apify route smoke test.

Tests one authorized Apify route at a time without running the full
person-evidence pipeline. No Sheet writes, Drive writes, LLM calls, or media
retention. It exists to prove actor/input shape and provider reachability
before a full SH-104 retry.

Modes:
  direct_url — apify~instagram-scraper with directUrls=[public Reel URL]
  hashtag    — apify~instagram-hashtag-scraper with hashtags=[tag]
  username   — apify~instagram-reel-scraper with username=[<handle>]

Exit code is 0 when the selected route is accepted and returns usable route
evidence. direct_url additionally requires a detected media URL.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

os.environ.setdefault("FALLBACK_MODE", "auto")

from transcription import (  # noqa: E402
    _apify_request,
    _extract_ig_media_url,
    _is_soft_fail_item,
    APIFY_API_KEY,
)


TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


def _safe_url(url: str) -> str:
    return (url or "").split("?")[0]


def _classify_start_error(err: str) -> str:
    low = (err or "").lower()
    if any(x in low for x in ("402", "403", "credit", "billing", "quota", "provider-access")):
        return "quota_or_provider_access"
    if any(x in low for x in ("400", "422", "schema", "input", "validation")):
        return "schema_or_input"
    if any(x in low for x in ("401", "auth", "unauthorized")):
        return "auth"
    return "unknown_start_error"


def _route_config(args: argparse.Namespace) -> tuple[str, dict, str]:
    if args.mode == "direct_url":
        reel_url = (args.reel_url or "").split("?")[0]
        payload = {
            "directUrls": [reel_url],
            "resultsType": "posts",
            "resultsLimit": 1,
            "addParentData": False,
            "proxy": {"useApifyProxy": True},
        }
        return "apify~instagram-scraper", payload, reel_url

    if args.mode == "hashtag":
        tag = (args.hashtag or "").strip().lstrip("#")
        payload = {"hashtags": [tag], "resultsLimit": max(1, args.results_limit)}
        return "apify~instagram-hashtag-scraper", payload, tag

    handle = (args.username or "").strip().lstrip("@")
    payload = {"username": [handle], "resultsLimit": max(1, args.results_limit)}
    return "apify~instagram-reel-scraper", payload, handle


def _poll_run(run_id: str, attempts: int = 20) -> str:
    status = ""
    for i in range(attempts):
        time.sleep(8)
        body, err = _apify_request(
            "GET",
            f"/actor-runs/{run_id}",
            params={"token": APIFY_API_KEY},
            timeout=15,
        )
        if err or not isinstance(body, dict):
            print(f"  poll {i + 1}: status=? err={(err or '')[:180]}")
            continue
        status = body.get("data", {}).get("status", "")
        print(f"  poll {i + 1}: status={status}")
        if status in TERMINAL_STATUSES:
            break
    return status


def _fetch_items(run_id: str, limit: int) -> list[dict]:
    body, err = _apify_request(
        "GET",
        f"/actor-runs/{run_id}/dataset/items",
        params={"token": APIFY_API_KEY, "limit": limit, "format": "json"},
        timeout=30,
    )
    if err:
        print(f"  dataset fetch error: {err[:300]}")
        return []
    return body if isinstance(body, list) else []


def _summarize_item(item: dict) -> dict:
    keys = sorted(item.keys())[:40]
    is_soft, soft_msg = _is_soft_fail_item(item)
    media_url, media_field = _extract_ig_media_url(item)
    url = item.get("url") or item.get("postUrl") or item.get("inputUrl") or ""
    shortcode = item.get("shortCode") or item.get("shortcode") or ""
    if not url and shortcode:
        url = f"https://www.instagram.com/reel/{shortcode}/"
    return {
        "keys": keys,
        "soft_fail": is_soft,
        "soft_message": soft_msg[:300],
        "url_detected": bool(url),
        "url": _safe_url(url),
        "media_detected": bool(media_url),
        "media_field": media_field,
        "media_url": media_url,
    }


def _head_media(media_url: str) -> tuple[int | None, str]:
    if not media_url:
        return None, ""
    try:
        import requests
        resp = requests.head(media_url, allow_redirects=True, timeout=20)
        return resp.status_code, resp.headers.get("Content-Type", "")
    except Exception as e:
        print(f"  media HEAD raised: {e}")
        return None, ""


def smoke_test(args: argparse.Namespace) -> int:
    actor, payload, target = _route_config(args)
    print("=== SH-104 Apify smoke test ===")
    print(f"  mode: {args.mode}")
    print(f"  actor: {actor}")
    print(f"  target: {target}")
    print(f"  APIFY_API_KEY present: {'yes' if APIFY_API_KEY else 'no'}")

    if not APIFY_API_KEY:
        print("  accepted: no")
        print("  safe_error: missing APIFY_API_KEY")
        return 1
    if not target:
        print("  accepted: no")
        print("  safe_error: missing required mode input")
        return 2

    body, err = _apify_request(
        "POST",
        f"/acts/{actor}/runs",
        params={"token": APIFY_API_KEY},
        json_body=payload,
        timeout=30,
    )
    if err:
        print("  accepted: no")
        print(f"  safe_error_category: {_classify_start_error(err)}")
        print(f"  safe_error: {err[:500]}")
        return 1

    run_id = body.get("data", {}).get("id", "") if isinstance(body, dict) else ""
    print("  accepted: yes")
    print(f"  run_id: {run_id}")
    if not run_id:
        print("  safe_error: accepted response missing run id")
        return 1

    status = _poll_run(run_id)
    print(f"  final_status: {status}")
    if status != "SUCCEEDED":
        print(f"  safe_error: run did not succeed ({status})")
        return 1

    items = _fetch_items(run_id, max(1, args.results_limit))
    print(f"  dataset_count: {len(items)}")
    if not items:
        print("  safe_error: dataset empty")
        return 1

    summary = _summarize_item(items[0])
    print(f"  safe_item_keys: {summary['keys']}")
    print(f"  soft_fail: {'yes' if summary['soft_fail'] else 'no'}")
    if summary["soft_fail"]:
        print(f"  safe_soft_error: {summary['soft_message']}")
        return 1
    print(f"  url_detected: {'yes' if summary['url_detected'] else 'no'}")
    if summary["url_detected"]:
        print(f"  sample_url: {summary['url'][:120]}")
    print(f"  media_url_detected: {'yes' if summary['media_detected'] else 'no'}")
    if summary["media_detected"]:
        print(f"  media_field: {summary['media_field']}")
        print(f"  safe_media_url: {_safe_url(summary['media_url'])[:120]}")

    if args.mode == "direct_url":
        if not summary["media_detected"]:
            print("  safe_error: direct_url route returned item but no media URL")
            return 1
        code, content_type = _head_media(summary["media_url"])
        print(f"  media_head_status: {code}")
        print(f"  media_content_type: {content_type or '?'}")
        if code and 200 <= code < 400:
            print("=== SMOKE PASSED ===")
            return 0
        print("  safe_error: media URL was detected but HEAD did not confirm downloadability")
        return 1

    if not summary["url_detected"] and not summary["media_detected"]:
        print("  safe_error: route returned item but no URL or media fields")
        return 1
    print("=== SMOKE PASSED ===")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("direct_url", "hashtag", "username"),
                   default=os.environ.get("INPUT_MODE", "direct_url"))
    p.add_argument("--reel-url", default=os.environ.get("INPUT_REEL_URL", ""))
    p.add_argument("--hashtag", default=os.environ.get("INPUT_HASHTAG", ""))
    p.add_argument("--username", default=os.environ.get("INPUT_USERNAME", ""))
    p.add_argument("--results-limit", type=int,
                   default=int(os.environ.get("INPUT_RESULTS_LIMIT", "3") or "3"))
    # Backward compatibility with the original positional direct Reel URL.
    p.add_argument("legacy_reel_url", nargs="?")
    args = p.parse_args(argv)
    if args.legacy_reel_url and not args.reel_url:
        args.reel_url = args.legacy_reel_url
    return args


if __name__ == "__main__":
    sys.exit(smoke_test(parse_args(sys.argv[1:])))
