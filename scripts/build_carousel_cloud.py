#!/usr/bin/env python3
"""
build_carousel_cloud.py — Oak Park Construction Carousel Builder (GitHub Actions / Cloud)
Same logic as build_carousel_v2.py but runs without local files.
Credentials from env vars. Fonts downloaded at runtime. Output uploaded to Drive.

Env vars required:
  SHEETS_TOKEN        — contents of sheets_token.json (from GitHub Secret)
  CONTENT_SHEET_ID    — Google Sheet ID (from GitHub Secret)

Optional enhancement route env vars:
  GEMINI_API_KEY      — enables Route 1A (Nano Banana 2 / Gemini enhancement)
  OPENAI_API_KEY      — enables Route 1B (OpenAI DALL-E enhancement)
  ENHANCEMENT_ROUTE   — "gemini" | "openai" | "pillow" (default: "gemini" if key present, else "pillow")

Design routes (2A/2B/2C) — stubs wired, implementations pending:
  Route 2A: Canva MCP   — TODO: wire Canva MCP tool calls
  Route 2B: Nano Banana 2 layout — TODO: wire Gemini layout generation
  Route 2C: OpenAI layout — TODO: wire OpenAI image generation for full slides

Analytics tab columns updated after each build:
  Col Z  — Enhancement Route used
  Col AA — Design Route used
  Col AB — Drive Folder Link
  Status col set to "Built" after successful upload
"""

# ═══════════════════════════════════════════════════════════════════════
# PIPELINE RULES — READ BEFORE MODIFYING
# ═══════════════════════════════════════════════════════════════════════
#
# TWO PIPELINES EXIST. NEVER MIX THEM.
#
# CHAT PIPELINE (Claude chat session):
#   Copy is written in chat → HTML file built in chat → user screenshots → posts
#   NEVER run this script for copy written in chat.
#   Use: carousel_cheapest_contractor.html or similar HTML output.
#
# AUTOMATED PIPELINE (this script):
#   4AM agent queues ideas → Content Queue (status=Approved) → this script builds
#   Triggers: cron 6PM daily OR workflow_dispatch with source=chat (grabs latest row)
#   Output: Drive → Reels & TikTok folder → slides saved as JPG
#
# PHOTO RULE:
#   Photo must match the copy TOPIC and EMOTIONAL TONE — not just service category.
#   Tips carousels → Pexels API (PEXELS_API_KEY secret) auto-sourced from hook keywords
#   Project carousels → Drive Oak Park Construction album → Photos and Videos
#
# TRIGGER LOGIC:
#   BUILD_SOURCE=chat → grab latest row regardless of status (chat-triggered)
#   BUILD_SOURCE=''   → only process rows with status=Approved (scheduler/manual)
#
# ═══════════════════════════════════════════════════════════════════════


import os, io, json, re, urllib.request, urllib.parse, sys, time, tempfile, base64
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent))
from routing import reels_folder as _reels_folder

from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID      = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB     = "📋 Content Queue"
CATALOG_TAB   = "📸 Photo Catalog"
CONTENT_FOLDER_ID = "1Y2ymfzpE4mZOFrIwWrFQHEfFeYK5sDmG"  # Drive: Content - Reels & TikTok

TMPDIR = Path(tempfile.gettempdir()) / "oak_park_carousel"
FONTS_DIR = TMPDIR / "fonts"
OUT_BASE  = TMPDIR / "ready_to_post"
TMPDIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)
OUT_BASE.mkdir(parents=True, exist_ok=True)

# ── Brand colors ──────────────────────────────────────────────────────────────
W, H        = 1080, 1440   # 3:4 portrait — taller, better for Instagram + thumbnail safety
SAFE_MARGIN = 120          # no text within 120px of left/right edges

# Primary palette
BG_DARK     = (10, 10, 10)       # #000000 — primary background (most posts)
BG_CREAM    = (240, 237, 231)    # #f0ede7 — light background option
YELLOW      = (203, 204, 16)     # #CBCC10 — primary brand accent
YELLOW_ALT  = (224, 232, 77)     # #e0e84d — secondary yellow / mustard (flexible)
WARM_BROWN  = (91, 60, 31)       # #5b3c1f — warm brown accent
WHITE       = (255, 255, 255)    # text on dark backgrounds
BLACK       = (0, 0, 0)          # #000000 — text on light backgrounds
NEAR_BLACK  = (4, 6, 6)          # #040606
GRAY_LIGHT  = (180, 180, 180)
GRAY_MID    = (100, 100, 100)
GOLD        = YELLOW             # alias — always resolves to brand yellow

# ── Font download (Google Fonts CDN) ─────────────────────────────────────────
# Note: Google Fonts repo migrated to variable fonts — static files no longer exist.
# We download the variable font files and use set_variation_by_axes() for bold weight.
FONT_URLS = {
    "Anton-Regular.ttf":          "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
    "RobotoCondensed-Bold.ttf":   "https://github.com/google/fonts/raw/main/ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf",
    "RobotoCondensed-Regular.ttf":"https://github.com/google/fonts/raw/main/ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf",
    "RobotoMono-Regular.ttf":     "https://github.com/google/fonts/raw/main/ofl/robotomono/RobotoMono%5Bwght%5D.ttf",
    "Roboto-Regular.ttf":         "https://github.com/google/fonts/raw/main/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
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
        font = ImageFont.truetype(_fp(name), size)
        # Variable fonts: apply bold weight for *-Bold.ttf filenames
        if "Bold" in name:
            try:
                font.set_variation_by_axes([700])
            except Exception:
                pass
        return font
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
def col_letter(n: int) -> str:
    """Convert 0-based column index to A1-style letter. 0→A, 25→Z, 26→AA, 27→AB"""
    result = ""
    n += 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result

def sheet_get(token, range_str):
    enc = urllib.parse.quote(range_str, safe="!:")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read())

def sheet_update_cells(token, tab_name, updates: list):
    """Batch-update individual cells. updates = list of (a1_cell_str, value)"""
    data = [{"range": f"'{tab_name}'!{cell}", "values": [[val]]} for cell, val in updates]
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": data}).encode()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req).read()
    except Exception as e:
        print(f"  ⚠️  Sheet update error: {e}")

def update_row_after_build(token, row_num: int, status_col: str,
                            enhancement_route: str, design_route: str, drive_folder_url: str):
    """After a successful build: set status → Built, log routes to Z/AA, Drive link to V."""
    updates = [
        (f"{status_col}{row_num}", "Built"),  # Status col (dynamic, found by header)
        (f"Z{row_num}", enhancement_route),   # AI Route col
        (f"AA{row_num}", design_route),        # Design Route col
        (f"V{row_num}", drive_folder_url),     # Drive Folder Path col
    ]
    sheet_update_cells(token, QUEUE_TAB, updates)
    print(f"  📊 Sheet updated (row {row_num}): Status=Built, routes logged, Drive folder → V")

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
        # col K = "after processed" — if "Edited", art already exists, skip build
        if v("after processed").lower() == "edited":
            print(f"  ⏭️  Row {idx} — K=Edited, skipping art build")
            continue
        status_idx = ci("status")
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
            "status_col":   col_letter(status_idx) if status_idx is not None else "J",
        })
    return result

def get_latest_post(token) -> list[dict]:
    """Chat-triggered: grab the single newest row regardless of status."""
    rows = sheet_get(token, f"'{QUEUE_TAB}'").get("values", [])
    if len(rows) < 2:
        return []
    header = [h.strip() for h in rows[0]]
    def ci(name): return next((i for i,h in enumerate(header) if name.lower() in h.lower()), None)
    # Walk from bottom to find the latest non-empty row
    for idx in range(len(rows)-1, 0, -1):
        row = rows[idx]
        real_idx = idx + 1  # 1-based sheet row
        def v(col): i=ci(col); return row[i].strip() if i is not None and len(row)>i else ""
        ct = v("content type").lower()
        if "static" in ct:
            continue
        if not v("hook") and not v("caption body"):
            continue
        status_idx = ci("status")
        print(f"  🤖 Chat mode — using row {real_idx}: {v('project name')}")
        return [{
            "row": real_idx,
            "project":      v("project name"),
            "service":      v("service type"),
            "content_type": v("content type"),
            "hook":         v("hook"),
            "caption":      v("caption body"),
            "cta":          v("cta"),
            "photos_raw":   v("photo(s) used"),
            "platform":     v("platform"),
            "status_col":   col_letter(status_idx) if status_idx is not None else "J",
        }]
    return []



def search_pexels(query: str, api_key: str) -> str:
    if not api_key:
        return ""
    try:
        url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&per_page=5&orientation=portrait"
        req = urllib.request.Request(url, headers={"Authorization": api_key})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        photos = data.get("photos", [])
        if photos:
            return photos[0]["src"]["large2x"]
    except Exception as e:
        print(f"  \u26a0\ufe0f  Pexels search failed: {e}")
    return ""

def sheet_update(token, range_: str, values: list):
    try:
        body = json.dumps({"values": values, "range": range_, "majorDimension": "ROWS"}).encode()
        enc_range = urllib.parse.quote(range_, safe="")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc_range}?valueInputOption=RAW"
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"
        }, method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            pass
    except Exception as e:
        print(f"  \u26a0\ufe0f  sheet_update failed: {e}")

def get_reels_folder_id(token) -> str:
    return _reels_folder("opc")

def create_content_subfolder(token, parent_id: str, folder_name: str) -> str:
    try:
        meta = json.dumps({"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}).encode()
        req = urllib.request.Request("https://www.googleapis.com/drive/v3/files", data=meta,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        return resp.get("id", "")
    except Exception as e:
        print(f"  \u26a0\ufe0f  Could not create subfolder: {e}")
    return ""

def download_photo_to_drive(token, photo_url: str, folder_id: str, filename: str) -> str:
    try:
        with urllib.request.urlopen(photo_url, timeout=15) as r:
            img_bytes = r.read()
        import base64 as _b64
        encoded = _b64.b64encode(img_bytes).decode()
        boundary = "oak_park_multipart"
        meta = json.dumps({"name": filename, "parents": [folder_id]}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n").encode() + meta + (
            f"\r\n--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Transfer-Encoding: base64\r\n\r\n"
        ).encode() + encoded.encode() + f"\r\n--{boundary}--".encode()
        req = urllib.request.Request("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            data=body, headers={"Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        return resp.get("id", "")
    except Exception as e:
        print(f"  \u26a0\ufe0f  Photo upload failed: {e}")
    return ""

def source_photo_for_post(token, post: dict) -> str:
    import datetime as _dt
    existing = (post.get("photos_raw") or "").strip()
    if existing and "TBD" not in existing.upper() and "UNSPLASH" not in existing.upper():
        print(f"  \U0001f4f8 Photo already set: {existing}")
        return existing
    pexels_key = os.environ.get("PEXELS_API_KEY", "")
    stop_words = {"the","a","an","is","are","was","were","be","been","have","has","had","do","does","did",
        "will","would","could","should","may","might","your","their","our","you","they","we","it","this",
        "that","and","or","but","not","with","from","by","to","of","on","in","at","don't","won't",
        "isn't","most","some","any","than","when","here","there","just","more","hire","hiring","stop"}
    raw = f"{post.get('service','')} {post.get('hook','')}"
    words = [w.lower().strip(".,!?\"'") for w in raw.split() if len(w) > 3]
    query_words = [w for w in words if w not in stop_words][:5]
    query = " ".join(query_words) if query_words else "construction renovation home"
    print(f"  \U0001f50d No valid photo — searching Pexels: '{query}'")
    photo_url = search_pexels(query, pexels_key)
    if not photo_url:
        print("  \u26a0\ufe0f  Pexels returned nothing — using brand background")
        return ""
    today = _dt.date.today().strftime("%Y-%m-%d")
    folder_name = f"{today} \u2014 {post.get('project','post')[:45]}"
    reels_folder_id = get_reels_folder_id(token)
    subfolder_id = create_content_subfolder(token, reels_folder_id, folder_name) if reels_folder_id else ""
    safe_q = query.replace(" ", "_")[:30]
    filename = f"pexels_{safe_q}.jpg"
    file_id = download_photo_to_drive(token, photo_url, subfolder_id, filename) if subfolder_id else ""
    if file_id:
        drive_url = f"https://drive.google.com/file/d/{file_id}/view"
        print(f"  \u2705 Photo saved: {filename}")
        row = post.get("row")
        if row:
            sheet_update(token, f"'\U0001f4cb Content Queue'!D{row}", [[filename]])
            sheet_update(token, f"'\U0001f4cb Content Queue'!E{row}", [[drive_url]])
        post["photos_raw"] = filename
        return filename
    print("  \u26a0\ufe0f  Photo save failed — using brand background")
    return ""

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

def download_photo(file_id: str, creds):
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
        return Image.open(buf).convert("RGB")
    except Exception as e:
        print(f"  ⚠️  Download error (ID {file_id}): {e}")
        return None

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
        folder_url = f"https://drive.google.com/drive/folders/{proj_id}"
        print(f"📁 Drive → Content - Reels & TikTok → Ready to Post")
        print(f"   🔗 {folder_url}")
        return folder_url
    except Exception as e:
        print(f"  ⚠️  Drive upload error: {e}")
        return ""

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

# ── Enhancement Route 1A — Nano Banana 2 (Gemini) ────────────────────────────
def enhance_with_gemini(img: "Image.Image") -> "Image.Image":
    """Route 1A: AI photo enhancement via Gemini image generation model.
    Sends the raw photo to Gemini with an enhancement prompt → returns improved image.
    Falls back to original if API unavailable or fails.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("  ⚠️  GEMINI_API_KEY not set — Route 1A skipped")
        return img
    try:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        payload = json.dumps({
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                    {"text": (
                        "Enhance this construction/renovation photo to look professional. "
                        "Improve lighting, exposure, contrast, and color accuracy to match "
                        "professional real estate or architecture photography quality. "
                        "DO NOT add or remove any objects, people, or elements. "
                        "Keep the exact same composition and framing as the original. "
                        "Output only the enhanced image."
                    )}
                ]
            }],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}
        }).encode()

        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-2.0-flash-preview-image-generation:generateContent?key={api_key}")
        req = urllib.request.Request(url, data=payload,
                                      headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=90).read())

        for part in resp.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if "inlineData" in part:
                img_data = base64.b64decode(part["inlineData"]["data"])
                enhanced = Image.open(io.BytesIO(img_data)).convert("RGB")
                # Resize back to original dimensions if Gemini changed them
                if enhanced.size != (img.width, img.height):
                    enhanced = enhanced.resize((img.width, img.height), Image.LANCZOS)
                print("  ✨ Route 1A (Gemini) enhancement applied")
                return enhanced

        print("  ⚠️  Gemini returned no image — falling back to original")
        return img
    except Exception as e:
        print(f"  ⚠️  Route 1A (Gemini) failed: {e}")
        return img


# ── Enhancement Route 1B — OpenAI Image Edit ─────────────────────────────────
def enhance_with_openai(img: "Image.Image") -> "Image.Image":
    """Route 1B: AI photo enhancement via OpenAI DALL-E image edit endpoint.
    Note: DALL-E 2 edit works best as an enhancement pass when no mask is provided.
    Falls back to original if API unavailable or fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  ⚠️  OPENAI_API_KEY not set — Route 1B skipped")
        return img
    try:
        # DALL-E 2 requires square PNG, max 4MB
        side = min(img.width, img.height, 1024)
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        img_sq = img.copy()
        img_sq.thumbnail((side, side), Image.LANCZOS)
        canvas.paste(img_sq, ((side - img_sq.width) // 2, (side - img_sq.height) // 2))

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        boundary = b"----OakParkBoundary7MA4YWxk"
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="model"\r\n\r\ndall-e-2\r\n'
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="n"\r\n\r\n1\r\n'
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="size"\r\n\r\n' + f"{side}x{side}".encode() + b"\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="response_format"\r\n\r\nb64_json\r\n'
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="prompt"\r\n\r\n'
            b"Enhance this construction/renovation photo: improve lighting, contrast, and "
            b"color accuracy to professional real estate photography quality. "
            b"Keep all elements and composition identical.\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="image"; filename="photo.png"\r\n'
            b"Content-Type: image/png\r\n\r\n" + img_bytes + b"\r\n"
            b"--" + boundary + b"--\r\n"
        )

        req = urllib.request.Request(
            "https://api.openai.com/v1/images/edits",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            }
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        if resp.get("data"):
            img_b64 = resp["data"][0].get("b64_json", "")
            if img_b64:
                enhanced = Image.open(io.BytesIO(base64.b64decode(img_b64))).convert("RGB")
                enhanced = enhanced.resize((img.width, img.height), Image.LANCZOS)
                print("  ✨ Route 1B (OpenAI) enhancement applied")
                return enhanced

        print("  ⚠️  OpenAI returned no image — falling back to original")
        return img
    except Exception as e:
        print(f"  ⚠️  Route 1B (OpenAI) failed: {e}")
        return img


def select_enhancement_route(img: "Image.Image") -> tuple:
    """Choose and apply the best available enhancement route.
    Returns (enhanced_image, route_name_str).
    Priority: ENHANCEMENT_ROUTE env var → gemini (if key present) → openai → pillow.
    """
    route_pref = os.environ.get("ENHANCEMENT_ROUTE", "auto").lower()

    if route_pref == "gemini" or (route_pref == "auto" and os.environ.get("GEMINI_API_KEY")):
        enhanced = enhance_with_gemini(img)
        if enhanced is not img:
            return enhanced, "Route 1A — Gemini (Nano Banana 2)"
        if route_pref == "gemini":
            return img, "Pillow (Gemini fallback)"

    if route_pref == "openai" or (route_pref == "auto" and os.environ.get("OPENAI_API_KEY")):
        enhanced = enhance_with_openai(img)
        if enhanced is not img:
            return enhanced, "Route 1B — OpenAI DALL-E"
        if route_pref == "openai":
            return img, "Pillow (OpenAI fallback)"

    # Default: Pillow-only enhancement (always-available, fast)
    return img, "Pillow (built-in)"


# ── Design Route Counter (alternating 2A ↔ 2B) ───────────────────────────────
# Persisted in /tmp so it survives across posts in the same run.
# Resets each new GitHub Actions run (that's fine — alternates per-session).
_ROUTE_COUNTER_FILE = TMPDIR / "design_route_counter.txt"

def get_next_design_route() -> str:
    """Returns '2A' or '2B' alternating. Increments counter each call."""
    try:
        count = int(_ROUTE_COUNTER_FILE.read_text().strip()) if _ROUTE_COUNTER_FILE.exists() else 0
    except Exception:
        count = 0
    _ROUTE_COUNTER_FILE.write_text(str(count + 1))
    return "2A" if count % 2 == 0 else "2B"


# ── Design Route 2A — Nano Banana 2 (Gemini full layout) ─────────────────────
def build_with_nano_banana_layout(post: dict, photos: list, out_dir: "Path") -> list:
    """Route 2A: Generate full carousel slides via Gemini image generation.
    Sends each photo + brand context to Gemini → returns a fully composed
    1080x1440 slide with text overlay baked in.
    Falls back to Pillow (build_carousel) if Gemini unavailable or fails.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("  ⚠️  GEMINI_API_KEY not set — Route 2A falling back to Pillow")
        return []

    saved = []
    total_slides = 1 + len(photos) + 1

    slide_prompts = [
        {
            "label": "cover",
            "fname": "slide_01_cover_nb2.jpg",
            "prompt": (
                f"Create a professional Instagram carousel cover slide, 1080x1440 pixels, portrait orientation. "
                f"Use this construction/renovation photo as the background. "
                f"Apply a dark gradient overlay at the bottom (60% of slide height). "
                f"Add this bold hook text in large white Anton font at the bottom left: '{post['hook']}'. "
                f"Add a small yellow (#CBCC10) horizontal accent bar above the hook text. "
                f"Add '@oakparkconstruction' in small white text at bottom right. "
                f"Service label '{post['service'].upper()}' in small white Roboto Bold at top left. "
                f"Brand colors: black background (#0a0a0a), yellow accent (#CBCC10), white text. "
                f"Style: high-end construction portfolio, dark and bold, no clutter."
            )
        },
    ]

    # Add content slides
    content_labels = ["The process.", "In progress.", "Taking shape.", "Finished."]
    for i, photo in enumerate(photos[1:] if len(photos) > 1 else photos):
        label = content_labels[min(i, len(content_labels)-1)]
        slide_prompts.append({
            "label": f"slide_{i+2}",
            "fname": f"slide_{i+2:02d}_nb2.jpg",
            "prompt": (
                f"Create a professional Instagram carousel slide, 1080x1440 pixels, portrait orientation. "
                f"Use this construction/renovation photo as the background. "
                f"Apply a dark gradient overlay at the bottom (30% of slide height). "
                f"Add this label text in white Roboto Bold at bottom left: '{label}'. "
                f"Add a small yellow (#CBCC10) accent bar above the label. "
                f"Add '@oakparkconstruction' in small white text at bottom right. "
                f"Brand colors: black (#0a0a0a), yellow (#CBCC10), white. "
                f"Style: clean, modern construction portfolio."
            )
        })

    # CTA slide (no photo — dark background)
    slide_prompts.append({
        "label": "cta",
        "fname": f"slide_{total_slides:02d}_cta_nb2.jpg",
        "prompt": (
            f"Create a professional Instagram carousel CTA slide, 1080x1440 pixels, portrait orientation. "
            f"Dark background (#0a0a0a) with subtle horizontal grid lines. "
            f"Left side: vertical yellow (#CBCC10) bar. "
            f"Bold text block: 'OAK PARK' in large Anton font on yellow background, "
            f"'CONSTRUCTION' in large white Roboto Condensed below it. "
            f"Service: '{post['service'].upper()}' in small yellow text. "
            f"Location: 'South Florida · Pompano Beach' in small gray text. "
            f"CTA text in white Roboto Mono: '{post['cta'] or 'DM us to see the full project'}'. "
            f"Contact info: '@oakparkconstruction', 'www.oakpark-construction.com', '+1 954-258-6769'. "
            f"Style: bold, high-contrast, luxury contractor brand."
        )
    })

    photo_cycle = photos + [None]  # None = CTA (no photo needed)

    for idx, slide_def in enumerate(slide_prompts):
        photo_img = photo_cycle[min(idx, len(photo_cycle)-1)]
        try:
            parts = []
            if photo_img is not None:
                buf = io.BytesIO()
                photo_img.save(buf, format="JPEG", quality=95)
                parts.append({"inline_data": {"mime_type": "image/jpeg",
                                               "data": base64.b64encode(buf.getvalue()).decode()}})
            parts.append({"text": slide_def["prompt"]})

            payload = json.dumps({
                "contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}
            }).encode()

            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.0-flash-preview-image-generation:generateContent?key={api_key}")
            req = urllib.request.Request(url, data=payload,
                                          headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=120).read())

            img_data = None
            for part in resp.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                if "inlineData" in part:
                    img_data = base64.b64decode(part["inlineData"]["data"])
                    break

            if img_data:
                slide_img = Image.open(io.BytesIO(img_data)).convert("RGB")
                if slide_img.size != (W, H):
                    slide_img = slide_img.resize((W, H), Image.LANCZOS)
                path = str(out_dir / slide_def["fname"])
                slide_img.save(path, "JPEG", quality=97)
                saved.append(path)
                print(f"    ✅ {slide_def['label']} (Gemini)")
            else:
                print(f"    ⚠️  Gemini returned no image for {slide_def['label']} — skipping slide")
        except Exception as e:
            print(f"    ⚠️  Route 2A failed on {slide_def['label']}: {e}")

    if len(saved) < 2:
        print("  ⚠️  Route 2A produced too few slides — falling back to Pillow")
        return []

    return saved


# ── Design Route 2B — OpenAI Layout ──────────────────────────────────────────
def build_with_openai_layout(post: dict, photos: list, out_dir: "Path") -> list:
    """Route 2B: Generate full carousel slides via OpenAI gpt-image-1.
    Sends each photo + brand context to OpenAI → returns composed slide.
    Falls back to Pillow if OpenAI unavailable or fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  ⚠️  OPENAI_API_KEY not set — Route 2B falling back to Pillow")
        return []

    saved = []
    total_slides = 1 + len(photos) + 1

    def generate_slide_openai(prompt: str, photo_img=None, fname: str = "slide.jpg") -> str | None:
        try:
            # Use gpt-image-1 edit if we have a photo, generate if CTA only
            if photo_img is not None:
                side = min(photo_img.width, photo_img.height, 1024)
                canvas = Image.new("RGB", (side, side), (0, 0, 0))
                thumb = photo_img.copy()
                thumb.thumbnail((side, side), Image.LANCZOS)
                canvas.paste(thumb, ((side - thumb.width) // 2, (side - thumb.height) // 2))
                buf = io.BytesIO()
                canvas.save(buf, format="PNG")
                img_bytes = buf.getvalue()

                boundary = b"----OakParkBoundary"
                body = (
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="model"\r\n\r\ngpt-image-1\r\n'
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="n"\r\n\r\n1\r\n'
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="size"\r\n\r\n1024x1024\r\n'
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="response_format"\r\n\r\nb64_json\r\n'
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="prompt"\r\n\r\n' +
                    prompt.encode() + b"\r\n"
                    b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="image"; filename="photo.png"\r\n'
                    b"Content-Type: image/png\r\n\r\n" + img_bytes + b"\r\n"
                    b"--" + boundary + b"--\r\n"
                )
                req = urllib.request.Request(
                    "https://api.openai.com/v1/images/edits",
                    data=body,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": f"multipart/form-data; boundary={boundary.decode()}"}
                )
            else:
                # CTA slide — pure generation, no input photo
                payload = json.dumps({
                    "model": "gpt-image-1",
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                    "response_format": "b64_json"
                }).encode()
                req = urllib.request.Request(
                    "https://api.openai.com/v1/images/generations",
                    data=payload,
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"}
                )

            resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            b64 = resp.get("data", [{}])[0].get("b64_json", "")
            if not b64:
                return None
            slide_img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            slide_img = slide_img.resize((W, H), Image.LANCZOS)
            path = str(out_dir / fname)
            slide_img.save(path, "JPEG", quality=97)
            return path
        except Exception as e:
            print(f"    ⚠️  OpenAI slide failed ({fname}): {e}")
            return None

    # Cover
    cover_prompt = (
        f"Professional Instagram carousel cover slide for Oak Park Construction. "
        f"Use this renovation photo as background with dark gradient at bottom. "
        f"Large bold hook text at bottom left: '{post['hook']}'. "
        f"Yellow (#CBCC10) accent bar above hook. Service: '{post['service'].upper()}' top left. "
        f"'@oakparkconstruction' bottom right. Dark, bold, luxury contractor style."
    )
    path = generate_slide_openai(cover_prompt, photos[0], "slide_01_cover_oai.jpg")
    if path:
        saved.append(path)
        print(f"    ✅ cover (OpenAI)")

    # Content slides
    content_labels = ["The process.", "In progress.", "Taking shape.", "Finished."]
    for i, photo in enumerate(photos[1:] if len(photos) > 1 else photos):
        label = content_labels[min(i, len(content_labels)-1)]
        p = (
            f"Professional Instagram carousel slide for Oak Park Construction. "
            f"Renovation photo as background, dark gradient bottom 30%. "
            f"Label '{label}' in white bold text bottom left. Yellow (#CBCC10) accent bar above label. "
            f"'@oakparkconstruction' bottom right. Clean, modern construction portfolio style."
        )
        path = generate_slide_openai(p, photo, f"slide_{i+2:02d}_oai.jpg")
        if path:
            saved.append(path)
            print(f"    ✅ slide {i+2} (OpenAI)")

    # CTA
    cta_prompt = (
        f"Professional Instagram CTA slide for Oak Park Construction. "
        f"Dark background (#0a0a0a). Left vertical yellow (#CBCC10) bar. "
        f"'OAK PARK' in large Anton font on yellow block. 'CONSTRUCTION' in white below. "
        f"Service '{post['service'].upper()}' in yellow. Location 'South Florida · Pompano Beach' in gray. "
        f"CTA: '{post['cta'] or 'DM us to see the full project'}' in white monospace. "
        f"Contact: '@oakparkconstruction', 'www.oakpark-construction.com', '+1 954-258-6769'. "
        f"Bold, high-contrast, luxury contractor brand."
    )
    path = generate_slide_openai(cta_prompt, None, f"slide_{total_slides:02d}_cta_oai.jpg")
    if path:
        saved.append(path)
        print(f"    ✅ CTA (OpenAI)")

    if len(saved) < 2:
        print("  ⚠️  Route 2B produced too few slides — falling back to Pillow")
        return []

    return saved


# ── Canva route — manual only (Claude Code session) ──────────────────────────
# Route 2C: Canva MCP — only available when running inside a Claude Code session.
# NOT wired here. To generate a Canva carousel, ask Claude directly:
#   "Generate a carousel for [project] using Canva"
# Claude will use the Oak Park Construction brand kit (ID: kAGs41wlfKg)
# and the templates in the Social Media Templates / TEMPLATE folders.


# ── CapCut Export (Future Phase) ──────────────────────────────────────────────
# TODO: After Drive upload, queue slides for CapCut auto-assembly
# Flow when available:
#   1. Get Drive folder link from upload_to_drive()
#   2. CapCut API call: create project → import slides in order → apply music template
#   3. Export as Reel-ready video → save back to Drive
# For now: slides are ready in Drive → Ready to Post → manual CapCut assembly if needed
# Estimated CapCut API availability: TBD (no public API as of 2026-04)


def build_cover_slide(photo, hook, service, slide_num, total):
    img = smart_crop(enhance_photo(photo), W, H)
    img = add_gradient(img, 0, 200, max_alpha=140)
    img = add_gradient(img, H - 700, 700, max_alpha=245)
    draw = ImageDraw.Draw(img)

    # Service label — inside safe margin
    font_label = load_font("RobotoCondensed-Bold.ttf", 28)
    draw.text((SAFE_MARGIN, 60), (service or "RENOVATION").upper(), font=font_label, fill=WHITE)

    # SWIPE pill — right side inside safe margin
    swipe_text = "SWIPE  →"
    bbox = draw.textbbox((0,0), swipe_text, font=font_label)
    tw = bbox[2]-bbox[0]; th_b = bbox[3]-bbox[1]
    px = W - SAFE_MARGIN - tw
    py = 50
    draw.rounded_rectangle([px-16, py-8, px+tw+16, py+th_b+8], radius=20, outline=WHITE, width=2)
    draw.text((px, py), swipe_text, font=font_label, fill=WHITE)

    # Gold accent bar
    draw.rectangle([(SAFE_MARGIN, H-530),(SAFE_MARGIN+60, H-524)], fill=GOLD)

    # Hook text — inside safe margins, max width = W - 2*SAFE_MARGIN
    font_hook = load_font("Anton-Regular.ttf", 76)
    safe_width = W - (SAFE_MARGIN * 2)
    lines = wrap_text(hook, font_hook, safe_width, draw)
    y = H - 510
    for line in lines[:4]:
        draw.text((SAFE_MARGIN, y), line, font=font_hook, fill=WHITE,
                  stroke_width=2, stroke_fill=(0,0,0))
        bbox = draw.textbbox((SAFE_MARGIN, y), line, font=font_hook)
        y += (bbox[3]-bbox[1]) + 12

    font_small = load_font("Roboto-Regular.ttf", 24)
    draw_brand_tag(draw, font_small)
    draw_progress_dots(draw, slide_num, total)
    return img

def build_content_slide(photo, label, sublabel, slide_num, total):
    img = smart_crop(enhance_photo(photo), W, H)
    img = add_gradient(img, H - 420, 420, max_alpha=220)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(SAFE_MARGIN, H-330),(SAFE_MARGIN+40, H-325)], fill=GOLD)

    font_label = load_font("RobotoCondensed-Bold.ttf", 48)
    font_sub   = load_font("Roboto-Regular.ttf", 28)
    safe_width = W - (SAFE_MARGIN * 2)

    if label:
        lines = wrap_text(label, font_label, safe_width, draw)
        y = H - 310
        for line in lines[:2]:
            draw.text((SAFE_MARGIN, y), line, font=font_label, fill=WHITE,
                      stroke_width=1, stroke_fill=(0,0,0))
            bbox = draw.textbbox((SAFE_MARGIN, y), line, font=font_label)
            y += (bbox[3]-bbox[1]) + 8
    if sublabel:
        draw.text((SAFE_MARGIN, H-175), sublabel, font=font_sub, fill=GRAY_LIGHT)

    font_small = load_font("Roboto-Regular.ttf", 24)
    draw_brand_tag(draw, font_small)
    draw_progress_dots(draw, slide_num, total)
    return img

def build_cta_slide(project, service, cta, slide_num, total):
    img = Image.new("RGB", (W, H), BG_DARK)
    draw = ImageDraw.Draw(img)
    for y in range(0, H, 60):
        draw.line([(0,y),(W,y)], fill=(20,20,20))

    # Yellow vertical bar — left accent
    draw.rectangle([(SAFE_MARGIN, 200),(SAFE_MARGIN+8, H-200)], fill=YELLOW)

    TX = SAFE_MARGIN + 24   # text x — safely inside margin + past the bar

    font_brand        = load_font("Anton-Regular.ttf", 88)
    font_construction = load_font("RobotoCondensed-Bold.ttf", 72)
    font_body         = load_font("RobotoMono-Regular.ttf", 32)
    font_small        = load_font("RobotoCondensed-Regular.ttf", 26)
    safe_width        = W - TX - SAFE_MARGIN

    # OAK PARK on yellow block
    draw.rectangle([(TX, 210),(W - SAFE_MARGIN, 315)], fill=YELLOW)
    draw.text((TX + 4, 215), "OAK PARK", font=font_brand, fill=BLACK)

    # CONSTRUCTION below
    draw.text((TX + 4, 325), "CONSTRUCTION", font=font_construction, fill=WHITE)

    # Service + location
    draw.text((TX + 4, 440), (service or "RENOVATION").upper(), font=font_small, fill=YELLOW)
    draw.text((TX + 4, 478), "South Florida  ·  Pompano Beach", font=font_small, fill=GRAY_LIGHT)

    # Separator
    draw.rectangle([(TX + 4, 525),(TX + 320, 528)], fill=YELLOW)

    # CTA text
    cta_clean = cta or "DM us to see the full project"
    lines = wrap_text(cta_clean, font_body, safe_width, draw)
    y = 542
    for line in lines[:3]:
        draw.text((TX + 4, y), line, font=font_body, fill=WHITE)
        bbox = draw.textbbox((TX+4, y), line, font=font_body)
        y += (bbox[3]-bbox[1]) + 10

    # Contact info
    draw.text((TX + 4, H-320), "📱  @oakparkconstruction",         font=font_small, fill=GRAY_LIGHT)
    draw.text((TX + 4, H-275), "🌐  www.oakpark-construction.com", font=font_small, fill=GRAY_LIGHT)
    draw.text((TX + 4, H-230), "📞  +1 954-258-6769",              font=font_small, fill=GRAY_LIGHT)

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

def build_carousel(post, photos, out_dir, enhancement_route="Pillow (built-in)"):
    """Build carousel slides using Flow A (Python/Pillow). Design route = 'Flow A — Python/Pillow'."""
    total_slides = 1 + len(photos) + 1
    saved = []
    cover = build_cover_slide(photos[0], post["hook"], post["service"], 0, total_slides)
    path = str(out_dir / "slide_01_cover.jpg")
    cover.save(path, "JPEG", quality=97)
    saved.append(path)
    print(f"    ✅ Slide 1 (cover)")

    photo_slides = photos if len(photos) > 1 else [photos[0]] * 2
    for i, photo in enumerate(photo_slides[1:] if len(photos) > 1 else photo_slides):
        slide_num = i + 1
        label = _slide_label(post, slide_num, len(photo_slides) - 1)
        sublabel = post["service"] if i == len(photo_slides) - 2 else ""
        slide = build_content_slide(photo, label, sublabel, slide_num, total_slides)
        fname = f"slide_{slide_num+1:02d}.jpg"
        path = str(out_dir / fname)
        slide.save(path, "JPEG", quality=97)
        saved.append(path)
        print(f"    ✅ Slide {slide_num+1}")

    cta_slide = build_cta_slide(post["project"], post["service"], post["cta"],
                                 total_slides - 1, total_slides)
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

    build_source = os.environ.get("BUILD_SOURCE", "")
    if build_source == "chat":
        print("🤖 Chat-triggered build — grabbing latest row regardless of status...")
        approved = get_latest_post(token)
        if not approved:
            print("✅ No buildable rows found.")
            return
    else:
        print(f"📋 Reading approved posts...")
        approved = get_approved_posts(token)
        if not approved:
            print("✅ No approved carousel/reel posts found — nothing to build.")
            return
    print(f"   Found {len(approved)} post(s) to build")

    print(f"📸 Loading photo catalog...")
    catalog = get_photo_catalog(token)
    print(f"   {len(catalog)} photos in catalog")

    for post in approved:
        print(f"\n{'='*50}")
        print(f"📌 {post['project']} — {post['content_type']}")

        source_photo_for_post(token, post)

        filenames = [f.strip() for f in post["photos_raw"].split(",") if f.strip()]
        raw_photos = []
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
                raw_photos.append(img)
                print(f"     ✅ {fn} ({img.width}x{img.height})")

        if not raw_photos:
            print(f"  ❌ No photos for '{post['project']}' — skipping")
            continue

        # ── Enhancement Route (1A Gemini / 1B OpenAI / Pillow fallback) ─────
        print(f"  🎨 Applying enhancement route...")
        photos = []
        enhancement_route = "Pillow (built-in)"
        for i, raw_img in enumerate(raw_photos):
            enhanced, route_name = select_enhancement_route(raw_img)
            photos.append(enhanced)
            if i == 0:
                enhancement_route = route_name  # log route from first photo

        safe = re.sub(r'[^\w\s-]', '', post["project"])[:35].strip()
        out_dir = OUT_BASE / f"{date.today()} — {safe}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Design Route — alternating 2A (Nano Banana 2) ↔ 2B (OpenAI) ─────
        route_key = get_next_design_route()
        print(f"  🎨 Design Route {route_key} selected")

        slide_paths = []
        if route_key == "2A":
            slide_paths = build_with_nano_banana_layout(post, photos, out_dir)
            design_route = "Route 2A — Nano Banana 2 (Gemini)"
        else:
            slide_paths = build_with_openai_layout(post, photos, out_dir)
            design_route = "Route 2B — OpenAI gpt-image-1"

        # Pillow fallback if chosen route produced nothing
        if not slide_paths:
            print(f"  ↩️  Falling back to Pillow layout")
            design_route += " → Pillow fallback"
            slide_paths = build_carousel(post, photos, out_dir, enhancement_route)
        drive_folder_url = upload_to_drive(slide_paths, post["project"], creds)

        # ── Update Analytics tab + set status = Built ─────────────────────────
        if drive_folder_url:
            update_row_after_build(
                token, post["row"], post["status_col"],
                enhancement_route, design_route, drive_folder_url
            )

    print(f"\n✅ Done. All carousels built and uploaded to Drive.")
    print(f"   Drive → Content - Reels & TikTok → Ready to Post")

if __name__ == "__main__":
    main()
