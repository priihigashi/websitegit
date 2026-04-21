#!/usr/bin/env python3
"""
photo_enhance_core.py — Shared OPC photo enhancement core.

Used by TWO entry points — caller decides where to save results:
  standalone (photo_edit.yml)    → Marketing > Image Creation > Enhanced Photos
  content-attached (opc_proof_post.py) → run_folder/enhanced/

This module is pure bytes-in / bytes-out. No Drive logic.

3-route cascade (NONNEGOTIABLES: 3-ROUTE MINIMUM):
  Route A: PIL deterministic adjustments (zero API cost, always available)
  Route B: Replicate Real-ESRGAN scale=1 (noise cleanup, no upscaling)
  Route C: original bytes — automatic fallback when SSIM < FIDELITY_FLOOR

Phase-aware scaling:
  before / during / progress → 0.55 (preserve construction reality)
  after                      → 1.0  (polished but natural)
"""

import base64, io, json, os, time, urllib.request
from typing import Optional

# ── Locked enhancement prompt ──────────────────────────────────────────────────
# Locked by Priscila 2026-04-20. DO NOT EDIT without explicit instruction.
# PIL parameters below are derived from this specification.
LOCKED_ENHANCEMENT_PROMPT = """Enhance this REAL construction/remodel photo to look like professional architectural / real-estate photography while preserving the exact original jobsite and composition.

STRICT RULES:
- Do NOT recreate, redesign, restyle, or invent anything.
- Do NOT add or remove objects, furniture, decor, tools, materials, landscaping, fixtures, cabinets, tile, lighting, windows, doors, walls, people, or shadows caused by real objects.
- Do NOT change geometry, layout, finishes, room proportions, or construction details.
- Do NOT make it look CGI, overly glossy, fake, or staged.
- Keep the image truthful to the real Oak Park project.

Allowed edits only:
- fix exposure and white balance
- improve contrast and color accuracy
- recover highlights and gently lift shadows
- mild dehaze / clarity / noise cleanup
- straighten verticals and minor perspective correction
- slight sharpening
- subtle crop only if needed for leveling/straightening
- realistic light cleanup only
- for AFTER photos only: very subtle grass / exterior tidying if it already exists in frame, with no new elements added

Phase-aware rule:
- If BEFORE / DURING / PROGRESS: preserve demolition, dust, tools, unfinished work, and all visible construction reality.
- If AFTER: keep it polished but still natural and truthful.

Style target:
Professional real-estate / architectural photography, natural light, realistic color, clean but honest, South Florida residential feel.

NEGATIVE:
no extra objects, no staging, no fake furniture, no new plants, no new windows, no new doors, no changed cabinets, no changed counters, no changed tile, no fake sky replacement, no face/body changes, no text/logo/watermark artifacts, no AI hallucinations."""

FIDELITY_FLOOR = 0.82

# PIL parameters derived from prompt above
_BRIGHTNESS  = 1.08
_CONTRAST    = 1.12
_SATURATION  = 1.05
_SHARPNESS   = 1.12
_UNSHARP_R   = 1
_UNSHARP_PCT = 55
_UNSHARP_THR = 3


def _auto_levels(img):
    """Per-channel auto-levels: stretch each channel to 2nd-98th percentile."""
    from PIL import Image
    r, g, b = img.split()
    channels = []
    for ch in (r, g, b):
        hist  = ch.histogram()
        total = sum(hist)
        lo = hi = 0
        cumsum = 0
        for i, count in enumerate(hist):
            cumsum += count
            if cumsum < total * 0.02 and lo == 0:
                lo = i
            if cumsum < total * 0.98:
                hi = i
        if hi - lo > 10:
            scale = 255.0 / (hi - lo)
            ch = ch.point(lambda p, lo=lo, s=scale: max(0, min(255, int((p - lo) * s))))
        channels.append(ch)
    return Image.merge("RGB", channels)


def _fidelity_score(original_bytes: bytes, enhanced_bytes: bytes) -> float:
    """Mean-pixel-difference proxy. 0.0 = completely different, 1.0 = identical.
    Returns 1.0 on any error (trust the result)."""
    try:
        from PIL import Image, ImageChops, ImageStat
        orig = Image.open(io.BytesIO(original_bytes)).convert("RGB").resize((256, 256))
        enh  = Image.open(io.BytesIO(enhanced_bytes)).convert("RGB").resize((256, 256))
        diff = ImageChops.difference(orig, enh)
        mean_diff = sum(ImageStat.Stat(diff).mean) / 3.0
        return round(max(0.0, 1.0 - mean_diff / 128.0), 4)
    except Exception:
        return 1.0


def _pil_enhance(raw: bytes, phase: str) -> bytes:
    """Route A — PIL deterministic photographic adjustments."""
    from PIL import Image, ImageEnhance, ImageFilter

    img   = Image.open(io.BytesIO(raw)).convert("RGB")
    scale = 0.55 if phase.lower() in ("before", "during", "progress") else 1.0

    img = _auto_levels(img)
    img = ImageEnhance.Brightness(img).enhance(1.0 + (_BRIGHTNESS - 1.0) * scale)
    img = ImageEnhance.Contrast(img).enhance(1.0 + (_CONTRAST - 1.0) * scale)
    img = ImageEnhance.Color(img).enhance(1.0 + (_SATURATION - 1.0) * scale * 0.6)
    img = img.filter(ImageFilter.UnsharpMask(
        radius=_UNSHARP_R, percent=int(_UNSHARP_PCT * scale), threshold=_UNSHARP_THR))
    img = ImageEnhance.Sharpness(img).enhance(1.0 + (_SHARPNESS - 1.0) * scale)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95, optimize=True)
    return out.getvalue()


def _replicate_denoise(pil_bytes: bytes, api_key: str) -> Optional[bytes]:
    """Route B — Replicate Real-ESRGAN scale=1 (noise cleanup only, no upscaling)."""
    if not api_key:
        return None
    try:
        b64     = base64.b64encode(pil_bytes).decode()
        payload = json.dumps({"input": {
            "image":        f"data:image/jpeg;base64,{b64}",
            "scale":        1,
            "face_enhance": False,
        }}).encode()
        req  = urllib.request.Request(
            "https://api.replicate.com/v1/models/nightmareai/real-esrgan/predictions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
                "Prefer":        "wait=30",
            },
        )
        resp    = json.loads(urllib.request.urlopen(req, timeout=45).read())
        pred_id = resp.get("id")
        status  = resp.get("status", "")
        output  = resp.get("output")

        for _ in range(12):
            if status in ("succeeded", "failed", "canceled"):
                break
            time.sleep(5)
            poll = urllib.request.Request(
                f"https://api.replicate.com/v1/predictions/{pred_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r2     = json.loads(urllib.request.urlopen(poll, timeout=15).read())
            status = r2.get("status", "")
            output = r2.get("output")

        if status != "succeeded" or not output:
            return None
        url = output if isinstance(output, str) else (output[0] if output else None)
        if not url:
            return None
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except Exception as e:
        print(f"  [enhance_core] Replicate skipped (non-fatal): {e}")
        return None


def enhance(raw_bytes: bytes, phase: str = "after", replicate_key: str = "") -> dict:
    """
    Run 3-route enhancement cascade on raw image bytes.
    Caller decides where to save the result.

    Returns:
        provider      (str):   "PIL" | "Replicate+PIL" | "original"
        enhanced_bytes (bytes): the best safe output (or raw_bytes if fallback)
        ssim          (float): fidelity score vs original (1.0 = identical)
        enhanced      (bool):  True if an enhanced copy was produced
    """
    enhanced_bytes: Optional[bytes] = None
    provider = "original"
    ssim     = 1.0

    # Route A — PIL
    try:
        pil_out = _pil_enhance(raw_bytes, phase)
        score   = _fidelity_score(raw_bytes, pil_out)
        if score >= FIDELITY_FLOOR:
            enhanced_bytes = pil_out
            provider       = "PIL"
            ssim           = score
            print(f"  [enhance_core] PIL ✅ ssim={score} phase={phase}")
        else:
            print(f"  [enhance_core] PIL ssim={score} < floor={FIDELITY_FLOOR} — skipping")
    except Exception as e:
        print(f"  [enhance_core] PIL failed (non-fatal): {e}")

    # Route B — Replicate (only if PIL succeeded, second pass on top)
    if enhanced_bytes is not None and replicate_key:
        try:
            rep = _replicate_denoise(enhanced_bytes, replicate_key)
            if rep:
                rep_score = _fidelity_score(raw_bytes, rep)
                if rep_score >= FIDELITY_FLOOR:
                    enhanced_bytes = rep
                    ssim           = rep_score
                    provider       = "Replicate+PIL"
                    print(f"  [enhance_core] Replicate ✅ ssim={rep_score}")
                else:
                    print(f"  [enhance_core] Replicate ssim={rep_score} < floor — keeping PIL")
        except Exception as e:
            print(f"  [enhance_core] Replicate pass skipped (non-fatal): {e}")

    # Route C — original fallback
    if enhanced_bytes is None:
        return {
            "provider":       "original",
            "enhanced_bytes": raw_bytes,
            "ssim":           1.0,
            "enhanced":       False,
        }

    return {
        "provider":       provider,
        "enhanced_bytes": enhanced_bytes,
        "ssim":           ssim,
        "enhanced":       True,
    }
