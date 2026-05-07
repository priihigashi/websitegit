"""_anthropic_diag.py — SH-104 safe Anthropic key diagnostic.

Reports only:
  - whether CLAUDE_KEY_4_CONTENT is present
  - response category: ok / low_credit / auth / rate_limit / unknown

Never prints the key or raw headers.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _category(status: int, body_text: str) -> str:
    low = (body_text or "").lower()
    if 200 <= status < 300:
        return "ok"
    if "credit balance is too low" in low or "purchase credits" in low or "billing" in low:
        return "low_credit"
    if status in (401, 403) or "authentication" in low or "api key" in low:
        return "auth"
    if status == 429 or "rate limit" in low or "rate_limit" in low:
        return "rate_limit"
    return "unknown"


def main() -> int:
    key = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    print("=== SH-104 Anthropic diagnostic ===")
    print(f"  CLAUDE_KEY_4_CONTENT present: {'yes' if bool(key) else 'no'}")
    if not key:
        print("  result_category: auth")
        return 1

    payload = {
        "model": "claude-3-5-haiku-latest",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply OK."}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    status = 0
    body_text = ""
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body_text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        body_text = e.read().decode("utf-8", "replace")
    except Exception as e:
        body_text = str(e)

    cat = _category(status, body_text)
    print(f"  result_category: {cat}")
    if cat == "unknown":
        print(f"  safe_status: {status or 'no_http_status'}")
    return 0 if cat == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
