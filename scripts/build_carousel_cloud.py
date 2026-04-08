#!/usr/bin/env python3
"""
build_carousel_cloud.py — Oak Park Construction Carousel Builder (GitHub Actions / Cloud)
Same logic as build_carousel_v2.py but runs without local files.
Credentials from env vars. Fonts downloaded at runtime. Output uploaded to Drive.

Env vars required:
  SHEETS_TOKEN      — contents of sheets_token.json (from GitHub Secret)
  CONTENT_SHEET_ID  — Google Sheet ID (from GitHub Secret)
"""

import os, io, json, re, urllib.request, urllib.parse, sys, time, tempfile
from pathlib import Path
from datetime import date

from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID      = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB     = "📋 Content Queue"
CATALOG_TAB   = "📸 Photo Catalog"
CONTENT_FOLDER_ID   = "1Y2ymfzpE4mZOFrIwWrFQHEfFeYK5sDmG"  # Drive: Content - Reels & TikTok
TEMPLATES_FOLDER_ID = "1564kppA5kuHgYXzj7ujjhSgyW3fc-jXR"  # Drive: ClaudeWorkspace/Content - Reels & TikTok/templates

TMPDIR = Path(tempfile.gettempdir()) / "oak_park_carousel"
FONTS_DIR = TMPDIR / "fonts"
OUT_BASE  = TMPDIR / "ready_to_post"
TMPDIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)
OUT_BASE.mkdir(parents=True, exist_ok=True)

# ── Brand colors ──────────────────────────────────────────────────────────────
W, H        = 1080, 1350
BG_DARK     = (10, 10, 10)
BG_CREAM    = (237, 232, 224)
YELLOW      = (203, 204, 16)
WARM_BROWN  = (107, 74, 26)
WHITE       = (255, 255, 255)
BLACK       = (0, 0, 0)
GRAY_LIGHT  = (180, 180, 180)
GRAY_MID    = (100, 100, 100)
GOLD        = YELLOW

# ── Font download (Google Fonts CDN) ─────────────────────────────────────────
FONT_URLS = {
    "Anton-Regular.ttf":          "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
    "RobotoCondensed-Bold.ttf":   "https://github.com/google/fonts/raw/main/ofl/robotocondensed/static/RobotoCondensed-Bold.ttf",
    "RobotoCondensed-Regular.ttf":"https://github.com/google/fonts/raw/main/ofl/robotocondensed/static/RobotoCondensed-Regular.ttf",
    "RobotoMono-Regular.ttf":     "https://github.com/google/fonts/raw/main/ofl/robotomono/static/RobotoMono-Regular.ttf",
    "Roboto-Regular.ttf":         "https://github.com/google/fonts/raw/main/ofl/roboto/static/Roboto-Regular.ttf",
}

def ensure_fonts():
    for fname, url in FONT_URLS.items():
        dest = FONTS_DIR / fname
        if not dest.exists():
            print(f"  ⬇️  Downloading font: {fname}")
            try:
                urllib.request.urlretrieve(url, str(dest))
            except Exception as e:
                print(f"  ⚠️  Font download failed ({fname}): {e}")

def _fp(name):
    return str(FONTS_DIR / name)

def load_font(name, size):
    try:
        return ImageFont.truetype(_fp(name), size)
    except Exception:
        try:
            return ImageFont.truetype(_fp("Roboto-Regular.ttf"), size)
        except Exception:
            return ImageFont.load_default()

# ── Google Auth ───────────────────────────────────────────────────────────────
_token_cache = {}

def get_token():
    if _token_cache.get("token") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["token"]

    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        # Try reading from file path (local fallback)
        path = os.environ.get("SHEETS_TOKEN_PATH", "")
        if path and Path(path).exists():
            raw = Path(path).read_text()
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN env var or SHEETS_TOKEN_PATH set")

    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["token"] = resp["access_token"]
    _token_cache["exp"]   = time.time() + resp.get("expires_in", 3500) - 60
    _token_cache["td"]    = td
    return resp["access_token"]

def get_creds():
    from google.oauth2.credentials import Credentials
    get_token()
    td = _token_cache["td"]
    return Credentials(
        token=_token_cache["token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=["https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets"],
    )

# ── Sheets helpers ────────────────────────────────────────────────────────────
def sheet_get(token, range_str):
    enc = urllib.parse.quote(range_str, safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read())

def get_approved_posts(token) -> list[dict]:
    rows = sheet_get(token, f"'{QUEUE_TAB}'").get("values", [])
    if len(rows) < 2:
        return []
    header = [h.strip() for h in rows[0]]
    def ci(name): return next((i for i,h in enumerate(header) if name.lower() in h.lower()), None)
    result = []
    for idx, row in enumerate(rows[1:], start=2):
        def v(col): i=ci(col); return row[i].strip() if i is not None and len(row)>i else ""
        if v("status").lower() != "approved":
            continue
        ct = v("content type").lower()
        if "static" in ct:
            continue
        result.append({
            "row": idx,
            "project":      v("project name"),
            "service":      v("service type"),
            "content_type": v("content type"),
            "hook":         v("hook"),
            "caption":      v("caption body"),
            "cta":          v("cta"),
            "photos_raw":   v("photo(s) used"),
            "platform":     v("platform"),
        })
    return result

def get_photo_catalog(token) -> dict:
    rows = sheet_get(token, f"'{CATALOG_TAB}'").get("values", [])
    if len(rows) < 2:
        return {}
    header = [h.strip().lower() for h in rows[0]]
    def ci(name): return next((i for i,h in enumerate(header) if name in h), None)
    fn_col  = ci("filename")
    url_col = ci("drive url")
    catalog = {}
    for row in rows[1:]:
        fn  = row[fn_col].strip()  if fn_col  is not None and len(row)>fn_col  else ""
        url = row[url_col].strip() if url_col is not None and len(row)>url_col else ""
        if fn and url:
            catalog[fn] = url
    return catalog

# ── Drive helpers ─────────────────────────────────────────────────────────────
def drive_url_to_id(url: str) -> str:
    m = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    return m.group(1) if m else ""

def download_drive_file(file_id: str, creds):
    """Download a Drive file as PIL Image, preserving original mode (incl. RGBA)."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    try:
        svc = build("drive", "v3", credentials=creds)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, svc.files().get_media(
            fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return Image.open(buf)
    except Exception as e:
        print(f"  ⚠️  Download error (ID {file_id}): {e}")
        return None

def download_photo(file_id: str, creds):
    img = download_drive_file(file_id, creds)
    return img.convert("RGB") if img else None

def upload_to_drive(file_paths: list, project_name: str, creds):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    try:
        svc = build("drive", "v3", credentials=creds)

        # Find or create "Ready to Post" subfolder inside Content - Reels & TikTok
        rtp_q = (f"name='Ready to Post' and '{CONTENT_FOLDER_ID}' in parents and "
                 f"mimeType='application/vnd.google-apps.folder' and trashed=false")
        rtp_res = svc.files().list(q=rtp_q, fields="files(id)", supportsAllDrives=True,
                                    includeItemsFromAllDrives=True).execute()
        rtp_id = rtp_res["files"][0]["id"] if rtp_res["files"] else CONTENT_FOLDER_ID

        # Create named project subfolder
        safe_name = re.sub(r'[^\w\s-]', '', project_name)[:40]
        folder_meta = {
            "name": f"{date.today()} — {safe_name}",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [rtp_id],
        }
        proj_folder = svc.files().create(body=folder_meta, fields="id",
                                          supportsAllDrives=True).execute()
        proj_id = proj_folder["id"]

        print(f"\n📤 Uploading {len(file_paths)} slides to Drive...")
        for fp in file_paths:
            meta  = {"name": Path(fp).name, "parents": [proj_id]}
            media = MediaFileUpload(fp, mimetype="image/jpeg")
            svc.files().create(body=meta, media_body=media,
                               fields="id", supportsAllDrives=True).execute()
            print(f"   ✅ {Path(fp).name}")
        print("📁 Drive → Content - Reels & TikTok → Ready to Post")
        return True
    except Exception as e:
        print(f"  ⚠️  Drive upload error: {e}")
        return False

# ── Template helpers ──────────────────────────────────────────────────────────
def load_templates(creds) -> dict:
    """List files in the Drive templates folder. Returns {filename: file_id}."""
    from googleapiclient.discovery import build
    try:
        svc = build("drive", "v3", credentials=creds)
        res = svc.files().list(
            q=f"'{TEMPLATES_FOLDER_ID}' in parents and trashed=false",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        return {f["name"]: f["id"] for f in res.get("files", [])}
    except Exception as e:
        print(f"  ⚠️  Could not load templates: {e}")
        return {}

def match_template(templates: dict, service: str, content_type: str) -> str | None:
    """Return Drive file_id of the best matching template, or None."""
    service_l = service.lower()
    ct_l      = content_type.lower()
    keywords  = []
    if "kitchen"                                    in service_l: keywords.append("kitchen")
    if "bath"                                       in service_l: keywords.append("bath")
    if any(x in service_l for x in ["pergola", "outdoor", "patio"]): keywords.append("outdoor")
    if "before" in ct_l or "after" in ct_l:                      keywords.append("before_after")
    if "reel"                                       in ct_l:      keywords.append("reel")
    if "carousel"                                   in ct_l:      keywords.append("carousel")
    for kw in keywords:
        for name, fid in templates.items():
            if kw in name.lower():
                return fid
    return next(iter(templates.values()), None)

def apply_template_overlay(slide: Image.Image, template_id: str, creds) -> Image.Image:
    """Composite a Drive template image over the slide.
    PNG with alpha → true overlay; JPEG → subtle 12% blend for style reference."""
    tmpl = download_drive_file(template_id, creds)
    if tmpl is None:
        return slide
    tmpl = tmpl.resize((W, H), Image.LANCZOS)
    if tmpl.mode == "RGBA":
        base = slide.convert("RGBA")
        return Image.alpha_composite(base, tmpl).convert("RGB")
    return Image.blend(slide.convert("RGB"), tmpl.convert("RGB"), alpha=0.12)

# ── Image helpers (same as local version) ─────────────────────────────────────
def enhance_photo(img):
    grayscale = img.convert("L")
    avg = sum(grayscale.getdata()) / len(grayscale.getdata())
    is_dark = avg < 110
    if is_dark:
        img = ImageEnhance.Brightness(img).enhance(1.25)
        img = ImageEnhance.Contrast(img).enhance(1.20)
        img = ImageEnhance.Color(img).enhance(1.15)
    else:
        img = ImageEnhance.Brightness(img).enhance(1.08)
        img = ImageEnhance.Contrast(img).enhance(1.18)
        img = ImageEnhance.Color(img).enhance(1.12)
    return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))

def smart_crop(img, tw, th):
    ir = img.width / img.height
    tr = tw / th
    if ir > tr:
        nh = th; nw = int(img.width * th / img.height)
    else:
        nw = tw; nh = int(img.height * tw / img.width)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top  = max(0, (nh - th) // 3)
    return img.crop((left, top, left + tw, top + th))

def add_gradient(canvas, start_y, height, max_alpha=230):
    grad = Image.new("RGBA", (W, height), (0,0,0,0))
    draw = ImageDraw.Draw(grad)
    for i in range(height):
        alpha = int((i / height) ** 1.4 * max_alpha)
        draw.line([(0,i),(W,i)], fill=(0,0,0,alpha))
    out = canvas.convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0,0,0,0))
    layer.paste(grad, (0, start_y))
    return Image.alpha_composite(out, layer).convert("RGB")

def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0,0), test, font=font)
        if bbox[2] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines

def draw_brand_tag(draw, font_small):
    text = "@oakparkconstruction"
    bbox = draw.textbbox((0,0), text, font=font_small)
    tw = bbox[2] - bbox[0]
    draw.text((W - tw - 30, H - 50), text, font=font_small, fill=WHITE)

def draw_progress_dots(draw, current, total):
    dot_r_a = 5; dot_r_i = 3; gap = 16
    total_w = total * (dot_r_a * 2) + (total - 1) * gap
    sx = (W - total_w) // 2
    y  = H - 55
    for i in range(total):
        x = sx + i * (dot_r_a * 2 + gap) + dot_r_a
        if i == current:
            draw.ellipse([(x-dot_r_a,y-dot_r_a),(x+dot_r_a,y+dot_r_a)], fill=GOLD)
        else:
            draw.ellipse([(x-dot_r_i,y-dot_r_i),(x+dot_r_i,y+dot_r_i)], fill=GRAY_MID)

def build_cover_slide(photo, hook, service, slide_num, total):
    img = smart_crop(enhance_photo(photo), W, H)
    img = add_gradient(img, 0, 180, max_alpha=140)
    img = add_gradient(img, H - 650, 650, max_alpha=240)
    draw = ImageDraw.Draw(img)
    font_label = load_font("RobotoCondensed-Bold.ttf", 28)
    draw.text((50, 55), (service or "RENOVATION").upper(), font=font_label, fill=WHITE)
    swipe_text = "SWIPE  →"
    bbox = draw.textbbox((0,0), swipe_text, font=font_label)
    tw = bbox[2]-bbox[0]; th_b = bbox[3]-bbox[1]
    px, py = W - tw - 60, 45
    draw.rounded_rectangle([px-16, py-8, px+tw+16, py+th_b+8], radius=20, outline=WHITE, width=2)
    draw.text((px, py), swipe_text, font=font_label, fill=WHITE)
    draw.rectangle([(50, H-490),(110, H-484)], fill=GOLD)
    font_hook = load_font("Anton-Regular.ttf", 72)
    lines = wrap_text(hook, font_hook, W - 100, draw)
    y = H - 470
    for line in lines[:4]:
        draw.text((50, y), line, font=font_hook, fill=WHITE, stroke_width=2, stroke_fill=(0,0,0))
        bbox = draw.textbbox((50,y), line, font=font_hook)
        y += (bbox[3]-bbox[1]) + 12
    font_small = load_font("Roboto-Regular.ttf", 24)
    draw_brand_tag(draw, font_small)
    draw_progress_dots(draw, slide_num, total)
    return img

def build_content_slide(photo, label, sublabel, slide_num, total):
    img = smart_crop(enhance_photo(photo), W, H)
    img = add_gradient(img, H - 380, 380, max_alpha=215)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(50, H-300),(90, H-295)], fill=GOLD)
    font_label = load_font("RobotoCondensed-Bold.ttf", 48)
    font_sub   = load_font("Roboto-Regular.ttf", 28)
    if label:
        lines = wrap_text(label, font_label, W - 100, draw)
        y = H - 280
        for line in lines[:2]:
            draw.text((50,y), line, font=font_label, fill=WHITE, stroke_width=1, stroke_fill=(0,0,0))
            bbox = draw.textbbox((50,y), line, font=font_label)
            y += (bbox[3]-bbox[1]) + 8
    if sublabel:
        draw.text((50, H-155), sublabel, font=font_sub, fill=GRAY_LIGHT)
    font_small = load_font("Roboto-Regular.ttf", 24)
    draw_brand_tag(draw, font_small)
    draw_progress_dots(draw, slide_num, total)
    return img

def build_cta_slide(project, service, cta, slide_num, total):
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    for y in range(0, H, 60):
        draw.line([(0,y),(W,y)], fill=(20,20,20))
    draw.rectangle([(50, 180),(58, H-180)], fill=YELLOW)
    font_brand = load_font("Anton-Regular.ttf", 88)
    font_body  = load_font("RobotoMono-Regular.ttf", 32)
    font_small = load_font("RobotoCondensed-Regular.ttf", 26)
    draw.rectangle([(75, 195),(W-75, 295)], fill=YELLOW)
    draw.text((80, 200), "OAK PARK", font=font_brand, fill=BLACK)
    font_construction = load_font("RobotoCondensed-Bold.ttf", 72)
    draw.text((80, 305), "CONSTRUCTION", font=font_construction, fill=WHITE)
    draw.text((82, 420), (service or "RENOVATION").upper(), font=font_small, fill=YELLOW)
    draw.text((82, 458), "South Florida  ·  Pompano Beach", font=font_small, fill=GRAY_LIGHT)
    draw.rectangle([(80, 505),(420, 508)], fill=YELLOW)
    cta_clean = cta or "DM us to see the full project"
    lines = wrap_text(cta_clean, font_body, W - 160, draw)
    y = 520
    for line in lines[:3]:
        draw.text((80, y), line, font=font_body, fill=WHITE)
        bbox = draw.textbbox((80,y), line, font=font_body)
        y += (bbox[3]-bbox[1]) + 10
    draw.text((80, H-300), "📱  @oakparkconstruction",        font=font_small, fill=GRAY_LIGHT)
    draw.text((80, H-255), "🌐  www.oakpark-construction.com",font=font_small, fill=GRAY_LIGHT)
    draw.text((80, H-210), "📞  +1 954-258-6769",             font=font_small, fill=GRAY_LIGHT)
    draw_progress_dots(draw, slide_num, total)
    return img

def _slide_label(post, slide_num, total_content):
    service = post["service"].lower()
    if "before" in post["hook"].lower() or "was" in post["hook"].lower():
        labels = ["The before.", "During the work.", "Coming together.", "The finish."]
    elif any(x in service for x in ["pergola","outdoor","patio"]):
        labels = ["Breaking ground.", "Structure going up.", "The build.", "Finished."]
    elif "kitchen" in service:
        labels = ["Tear-out done.", "New layout framed.", "Cabinets in.", "Final result."]
    elif "bath" in service:
        labels = ["Demo complete.", "Tile work begins.", "Coming together.", "The reveal."]
    else:
        labels = ["The process.", "In progress.", "Taking shape.", "Finished."]
    return labels[min(slide_num - 1, len(labels) - 1)]

def build_carousel(post, photos, out_dir, template_id=None, creds=None):
    total_slides = 1 + len(photos) + 1
    saved = []

    def _apply(slide):
        if template_id and creds:
            return apply_template_overlay(slide, template_id, creds)
        return slide

    cover = _apply(build_cover_slide(photos[0], post["hook"], post["service"], 0, total_slides))
    path = str(out_dir / "slide_01_cover.jpg")
    cover.save(path, "JPEG", quality=97)
    saved.append(path)
    print(f"    ✅ Slide 1 (cover)")

    photo_slides = photos if len(photos) > 1 else [photos[0]] * 2
    for i, photo in enumerate(photo_slides[1:] if len(photos) > 1 else photo_slides):
        slide_num = i + 1
        label = _slide_label(post, slide_num, len(photo_slides) - 1)
        sublabel = post["service"] if i == len(photo_slides) - 2 else ""
        slide = _apply(build_content_slide(photo, label, sublabel, slide_num, total_slides))
        fname = f"slide_{slide_num+1:02d}.jpg"
        path = str(out_dir / fname)
        slide.save(path, "JPEG", quality=97)
        saved.append(path)
        print(f"    ✅ Slide {slide_num+1}")

    cta_slide = _apply(build_cta_slide(post["project"], post["service"], post["cta"],
                                        total_slides - 1, total_slides))
    path = str(out_dir / f"slide_{total_slides:02d}_cta.jpg")
    cta_slide.save(path, "JPEG", quality=97)
    saved.append(path)
    print(f"    ✅ Slide {total_slides} (CTA)")
    return saved

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n🏗️  Carousel Builder (Cloud) — {date.today()}")
    print("=" * 50)

    print("⬇️  Ensuring fonts...")
    ensure_fonts()

    print("🔐 Authenticating...")
    token = get_token()
    creds = get_creds()

    print(f"📋 Reading approved posts...")
    approved = get_approved_posts(token)
    if not approved:
        print("✅ No approved carousel/reel posts found — nothing to build.")
        return
    print(f"   Found {len(approved)} post(s) to build")

    print(f"📸 Loading photo catalog...")
    catalog = get_photo_catalog(token)
    print(f"   {len(catalog)} photos in catalog")

    print(f"🎨 Loading design templates...")
    templates = load_templates(creds)
    print(f"   {len(templates)} template(s) found: {', '.join(templates.keys()) or 'none'}")

    for post in approved:
        print(f"\n{'='*50}")
        print(f"📌 {post['project']} — {post['content_type']}")

        filenames = [f.strip() for f in post["photos_raw"].split(",") if f.strip()]
        photos = []
        for fn in filenames:
            url = catalog.get(fn) or next((v for k,v in catalog.items() if k.lower()==fn.lower()), "")
            if not url:
                print(f"  ⚠️  '{fn}' not in catalog — skipping")
                continue
            file_id = drive_url_to_id(url)
            if not file_id:
                print(f"  ⚠️  Could not extract Drive ID from: {url}")
                continue
            print(f"  ⬇️  Downloading {fn}...")
            img = download_photo(file_id, creds)
            if img:
                photos.append(img)
                print(f"     ✅ {fn} ({img.width}x{img.height})")

        if not photos:
            print(f"  ❌ No photos for '{post['project']}' — skipping")
            continue

        safe = re.sub(r'[^\w\s-]', '', post["project"])[:35].strip()
        out_dir = OUT_BASE / f"{date.today()} — {safe}"
        out_dir.mkdir(parents=True, exist_ok=True)

        template_id = match_template(templates, post["service"], post["content_type"])
        if template_id:
            tname = next(k for k, v in templates.items() if v == template_id)
            print(f"  🎨 Template matched: {tname}")
        else:
            print(f"  🎨 No template matched — using default design")

        slide_paths = build_carousel(post, photos, out_dir, template_id=template_id, creds=creds)
        upload_to_drive(slide_paths, post["project"], creds)

    print(f"\n✅ Done. All carousels built and uploaded to Drive.")
    print(f"   Drive → Content - Reels & TikTok → Ready to Post")

if __name__ == "__main__":
    main()
