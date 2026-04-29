"""
vision_validator.py — shared Claude Haiku Vision check for image-vs-content match.

Single-source-of-truth helper used by every pipeline that places an image into
a slide:
    - carousel_builder.py     (initial generation)
    - fix_existing_images.py  (retroactive fix)
    - carousel_reviewer.py    (post-build review)

Usage:
    from vision_validator import validate_image
    ok, reason = validate_image(local_path, query)
    if not ok:
        # discard image, fall through to next provider
        ...

Returns (is_relevant, reason). When the API key is missing or the call fails,
returns (True, "skipped") so the pipeline never blocks on a transient Vision
outage — the image still ships, but the failure reason is logged.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path
from typing import Tuple

ANTHROPIC_KEY = os.environ.get("CLAUDE_KEY_4_CONTENT") or os.environ.get("ANTHROPIC_API_KEY", "")


def validate_image_bytes(image_bytes: bytes, filename: str, query: str) -> Tuple[bool, str]:
    """Send (image, query) to Claude Haiku Vision. Return (is_relevant, reason)."""
    if not ANTHROPIC_KEY:
        return True, "skipped (no ANTHROPIC_KEY)"
    if len(image_bytes) < 5000:
        return True, "skipped (image < 5kb)"
    if not query or len(query.strip()) < 3:
        return True, "skipped (no query to match against)"

    mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
    b64 = base64.b64encode(image_bytes).decode()
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 80,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": (
                    f"Does this image visually represent: '{query}'?\n"
                    f"YES if it clearly shows the correct subject, action, or setting.\n"
                    f"NO if it shows something completely unrelated.\n"
                    f"Start with YES or NO, then one short sentence."
                )},
            ],
        }],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        verdict = resp["content"][0]["text"].strip()
    except Exception as e:
        return True, f"skipped (vision error: {e})"

    is_relevant = not verdict.upper().startswith("NO")
    return is_relevant, verdict[:200]


def validate_image(local_path: str, query: str) -> Tuple[bool, str]:
    """Read a local image file and validate it against the query. Convenience wrapper."""
    p = Path(local_path)
    if not p.exists():
        return True, "skipped (file not found)"
    try:
        return validate_image_bytes(p.read_bytes(), p.name, query)
    except Exception as e:
        return True, f"skipped (read error: {e})"
