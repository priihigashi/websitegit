#!/usr/bin/env python3
"""
generate_thumbnail.py — Oak Park Construction Reel Thumbnail Generator

Usage:
  python3 generate_thumbnail.py --title "IF YOU DONT WANT THIS" --cta "HIRE US" --reel-id 18105110056913809
  python3 generate_thumbnail.py --title "BEFORE VS AFTER" --cta "SWIPE RIGHT"
  python3 generate_thumbnail.py --topic "bathroom remodel" --auto   # auto-generates title from topic

How it works:
  1. Generates a photorealistic background image (DALL-E 3)
  2. Composites bold neon-lime text overlay using Pillow (perfect text, no DALL-E spelling errors)
  3. Saves to Drive (Content - Reels & TikTok folder)
  4. Returns local path + Drive URL

Style: Black bg, neon lime (#CCFF00), chunky block caps — matches Oak Park brand.
"""

import os, sys, argparse, base64, json, requests, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import io

# ── Config ────────────────────────────────────────────────────────────────────
OPENAI_KEY = os.environ.get("OPENAI_API_KEY") or open(Path.home() / "ClaudeWorkspace/.env").read().split("OPENAI_API_KEY=")[1].split("\n")[0]
OUTPUT_DIR = Path.home() / "ClaudeWorkspace/_Scripts/thumbnails"
OUTPUT_DIR.mkdir(exist_ok=True)

NEON_LIME   = (204, 255, 0)    # #CCFF00
BLACK       = (0, 0, 0)
WHITE       = (255, 255, 255)
THUMB_W     = 1080
THUMB_H     = 1920

# ── Font loader ───────────────────────────────────────────────────────────────
def get_font(size):
    """Try Impact → Arial Bold → fallback default."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ── Background generation ─────────────────────────────────────────────────────
def generate_background(topic: str) -> bytes:
    """Generate a photorealistic background image using DALL-E 3."""
    prompt_map = {
        "damage": "Dramatic wide-angle photo of a small South Florida stucco bungalow with severe structural damage. Cracked stucco walls, exposed cinder blocks, damaged roof, storm water stains. Slightly desaturated, high contrast, moody overcast sky. Photorealistic. No people. No text.",
        "before_after": "Split view of a South Florida home: left side shows a run-down neglected exterior, right side shows a stunning luxury renovation with fresh stucco, modern windows, lush landscaping. Photorealistic. No text. No people.",
        "progress": "Aerial drone view of an active South Florida residential construction site. Concrete slab, rebar structure, workers in hard hats. Golden hour lighting, dramatic. Photorealistic. No text.",
        "bathroom": "Stunning modern bathroom remodel in South Florida luxury home. Large format marble tiles, frameless glass shower, floating vanity with LED lighting. High-end architectural photography. No text. No people.",
        "outdoor": "Beautiful South Florida outdoor living space: custom pergola, outdoor kitchen with stainless appliances, tropical landscaping, pool in background. Luxury real estate photography style. No text. No people.",
        "default": "Dramatic wide-angle photo of a luxury South Florida home construction project. Clean modern design, professional workmanship visible. Golden hour lighting, high contrast. Photorealistic. No text. No people.",
    }

    prompt = prompt_map.get(topic, prompt_map["default"])

    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json={"model": "dall-e-3", "prompt": prompt, "size": "1024x1024", "quality": "hd", "n": 1, "response_format": "b64_json"},
        timeout=120
    )
    r.raise_for_status()
    return base64.b64decode(r.json()["data"][0]["b64_json"])


# ── Text compositor ───────────────────────────────────────────────────────────
def composite_thumbnail(bg_bytes: bytes, top_text: str, bottom_text: str, brand: str = "OAK PARK CONSTRUCTION") -> Image.Image:
    """Composite bold neon text over a black canvas with rounded photo frame."""
    canvas = Image.new("RGB", (THUMB_W, THUMB_H), BLACK)

    # -- Photo frame (center, rounded corners)
    bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    frame_w, frame_h = 940, 800
    frame_x = (THUMB_W - frame_w) // 2
    frame_y = 520
    bg_img = bg_img.resize((frame_w, frame_h), Image.LANCZOS)

    # Rounded corners mask
    mask = Image.new("L", (frame_w, frame_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, frame_w, frame_h], radius=60, fill=255)
    canvas.paste(bg_img, (frame_x, frame_y), mask)

    draw = ImageDraw.Draw(canvas)

    # -- Top text (top 40% of canvas)
    top_font_size = 160
    top_font = get_font(top_font_size)
    words = top_text.upper().split()
    # Break into lines of ~3 words
    lines = []
    chunk = []
    for word in words:
        chunk.append(word)
        if len(chunk) >= 3:
            lines.append(" ".join(chunk))
            chunk = []
    if chunk:
        lines.append(" ".join(chunk))

    y_pos = 60
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=top_font)
        tw = bbox[2] - bbox[0]
        x = (THUMB_W - tw) // 2
        # Shadow
        draw.text((x + 4, y_pos + 4), line, font=top_font, fill=(0, 80, 0))
        draw.text((x, y_pos), line, font=top_font, fill=NEON_LIME)
        y_pos += top_font_size + 10

    # -- Brand text inside frame bottom
    brand_font = get_font(36)
    bbox = draw.textbbox((0, 0), brand, font=brand_font)
    bw = bbox[2] - bbox[0]
    draw.text(((THUMB_W - bw) // 2, frame_y + frame_h - 55), brand, font=brand_font, fill=NEON_LIME)

    # -- Bottom CTA text
    bottom_font_size = 220
    bottom_font = get_font(bottom_font_size)
    cta = bottom_text.upper()
    bbox = draw.textbbox((0, 0), cta, font=bottom_font)
    cw = bbox[2] - bbox[0]
    cx = (THUMB_W - cw) // 2
    cy = frame_y + frame_h + 50
    # Shadow
    draw.text((cx + 5, cy + 5), cta, font=bottom_font, fill=(0, 80, 0))
    draw.text((cx, cy), cta, font=bottom_font, fill=NEON_LIME)

    return canvas


# ── Drive upload ──────────────────────────────────────────────────────────────
def upload_to_drive(img_path: Path) -> str:
    """Upload thumbnail to Drive Content - Reels & TikTok folder. Returns view URL."""
    try:
        env_path = Path.home() / "ClaudeWorkspace/.env"
        env = dict(line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line and not line.startswith("#"))

        # Read OAuth token
        token_file = Path.home() / "ClaudeWorkspace/Credentials/oauth_token.json"
        if not token_file.exists():
            return f"local:{img_path}"

        token_data = json.loads(token_file.read_text())
        access_token = token_data.get("access_token", "")

        folder_id = env.get("REELS_FOLDER_ID", "")
        if not folder_id:
            return f"local:{img_path}"

        img_bytes = img_path.read_bytes()
        metadata = json.dumps({"name": img_path.name, "parents": [folder_id]})

        r = requests.post(
            f"https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
            headers={"Authorization": f"Bearer {access_token}"},
            files={"metadata": ("metadata", metadata, "application/json"), "file": (img_path.name, img_bytes, "image/jpeg")},
            timeout=60
        )
        if r.ok:
            file_id = r.json().get("id", "")
            return f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as e:
        print(f"  Drive upload skipped: {e}")
    return f"local:{img_path}"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate Oak Park reel thumbnail")
    parser.add_argument("--title", default="IF YOU DONT WANT THIS", help="Top text (2-4 words work best)")
    parser.add_argument("--cta", default="HIRE US", help="Bottom CTA text")
    parser.add_argument("--topic", default="damage", help="Background scene: damage|before_after|progress|bathroom|outdoor|default")
    parser.add_argument("--brand", default="OAK PARK CONSTRUCTION", help="Brand text inside frame")
    parser.add_argument("--output", help="Output filename (optional)")
    parser.add_argument("--no-upload", action="store_true", help="Skip Drive upload")
    args = parser.parse_args()

    print(f"🎨 Generating thumbnail: '{args.title}' / '{args.cta}'")

    print("  📸 Generating background photo...")
    bg_bytes = generate_background(args.topic)

    print("  ✏️  Compositing text overlay...")
    thumbnail = composite_thumbnail(bg_bytes, args.title, args.cta, args.brand)

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = args.output or f"thumbnail_{ts}.jpg"
    out_path = OUTPUT_DIR / filename
    thumbnail.save(out_path, "JPEG", quality=95)
    print(f"  ✅ Saved: {out_path}")

    if not args.no_upload:
        print("  ☁️  Uploading to Drive...")
        drive_url = upload_to_drive(out_path)
        print(f"  🔗 Drive: {drive_url}")
    else:
        drive_url = f"local:{out_path}"

    print(f"\n✅ Done → {out_path}")
    return str(out_path), drive_url


if __name__ == "__main__":
    main()
