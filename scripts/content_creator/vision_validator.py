"""
vision_validator.py — shared Claude Vision check for image-vs-content match.

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

Phase 10 (SH-OPC-SMART-SLIDE-PICKER):
- Sonnet 4.6 primary (Haiku missed kitchen-on-concrete in prior runs).
- Auto-downscale images >4.5MB so OPC catalog DSLR JPEGs stop 400-ing.
- HTTPError surfaces real response body for diagnosability.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Tuple

ANTHROPIC_KEY = os.environ.get("CLAUDE_KEY_4_CONTENT") or os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", "")

# Anthropic Vision API limits:
#   - Max raw image size: 5 MB (base64 payload <= ~6.7 MB)
#   - Max dimensions: 8000 × 8000 px (recommended ≤1568 on long edge)
# OPC catalog JPEGs are 4-8 MB DSLR shots — every Vision call 400'd in
# workflow runs 25463127290 + 25464355273 because of this.
_MAX_IMG_BYTES = 4_500_000  # 10% headroom under 5 MB hard limit


def _downscale_to_under_limit(image_bytes: bytes, mime: str) -> Tuple[bytes, str]:
    """If image_bytes exceeds the limit, try to downscale via Pillow. If
    Pillow is missing OR downscale fails, return b'' to signal skip."""
    if len(image_bytes) <= _MAX_IMG_BYTES:
        return image_bytes, mime
    try:
        from PIL import Image
        from io import BytesIO
    except Exception:
        return b"", mime
    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.thumbnail((1568, 1568), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return b"", mime


def validate_image_bytes(image_bytes: bytes, filename: str, query: str) -> Tuple[bool, str]:
    """Send (image, query) to Claude Vision. Return (is_relevant, reason)."""
    # Phase 11/C2 — accept OpenAI-only deployments. If neither key is set,
    # we have no provider — skip. If only OPENAI_KEY, route directly there.
    if not ANTHROPIC_KEY and not OPENAI_KEY:
        return True, "skipped (no vision key — neither ANTHROPIC nor OPENAI)"
    if len(image_bytes) < 5000:
        return True, "skipped (image < 5kb)"
    if not query or len(query.strip()) < 3:
        return True, "skipped (no query to match against)"

    mime = "image/jpeg" if filename.lower().endswith((".jpg", ".jpeg")) else "image/png"
    image_bytes, mime = _downscale_to_under_limit(image_bytes, mime)
    if not image_bytes:
        # Phase 10/M5 — log instead of silent skip.
        print("  [vision] oversize image — Pillow missing or downscale failed; skipping")
        return True, "skipped (oversize, no Pillow to downscale)"
    b64 = base64.b64encode(image_bytes).decode()

    # OpenAI-only path: skip Anthropic entirely.
    if not ANTHROPIC_KEY and OPENAI_KEY:
        return _validate_via_openai(b64, mime, query)
    payload = json.dumps({
        # Phase 10 — Sonnet for image-text semantic match.
        "model": "claude-sonnet-4-6",
        "max_tokens": 80,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": (
                    f"Does this image visually represent: '{query}'?\n"
                    f"YES if it clearly shows the correct subject, action, or setting.\n"
                    f"NO if it shows something completely unrelated (e.g. kitchen photo "
                    f"on a concrete/structural topic).\n"
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
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = "(no body)"
        # Phase 10/11/C3 — fallback to OpenAI on credits/capacity (4xx auth/
        # billing) AND on 5xx server errors (500 internal, 502 bad gateway,
        # 503 unavailable, 504 timeout, 529 overload).
        if e.code in (400, 401, 402, 429, 500, 502, 503, 504, 529) and OPENAI_KEY:
            ok, reason = _validate_via_openai(b64, mime, query)
            return ok, reason
        return True, f"skipped (vision HTTP {e.code}: {body})"
    except Exception as e:
        if OPENAI_KEY:
            try:
                return _validate_via_openai(b64, mime, query)
            except Exception:
                pass
        return True, f"skipped (vision error: {e})"

    is_relevant = not verdict.upper().startswith("NO")
    return is_relevant, verdict[:200]


def _validate_via_openai(b64_data: str, mime: str, query: str) -> Tuple[bool, str]:
    """Fallback when Anthropic Vision 400/429/529. Uses OpenAI gpt-4o-mini
    vision, same yes/no contract."""
    payload = json.dumps({
        "model": "gpt-4o-mini",
        "max_tokens": 80,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64_data}"}},
                {"type": "text",
                 "text": (
                    f"Does this image visually represent: '{query}'?\n"
                    f"YES if it clearly shows the correct subject/action/setting.\n"
                    f"NO if it shows something completely unrelated (e.g. kitchen "
                    f"photo on a concrete/structural topic).\n"
                    f"Start with YES or NO, then one short sentence."
                 )},
            ],
        }],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        verdict = resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return True, f"skipped (openai vision error: {e})"
    is_relevant = not verdict.upper().startswith("NO")
    return is_relevant, f"[openai-fallback] {verdict[:180]}"


def validate_image(local_path: str, query: str) -> Tuple[bool, str]:
    """Read a local image file and validate it against the query. Convenience wrapper."""
    p = Path(local_path)
    if not p.exists():
        return True, "skipped (file not found)"
    try:
        return validate_image_bytes(p.read_bytes(), p.name, query)
    except Exception as e:
        return True, f"skipped (read error: {e})"
