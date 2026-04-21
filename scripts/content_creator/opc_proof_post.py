#!/usr/bin/env python3
"""
opc_proof_post.py — Isolated OPC proof-post flow.

Reads real cataloged Oak Park photos from 📸 Photo Catalog,
groups them by project + service + room, scores each group for
the best proof-post type, and writes candidates to a separate
📸 Proof Post Candidates tab.

ISOLATION GUARANTEE:
  - Reads from Photo Catalog only. Never touches Content Queue.
  - Never writes to OPC educational carousel folders (16P2JN7...).
  - Never calls carousel_builder.py or main.py.
  - Phase 1 (tonight): grouping + candidate selection + metadata confidence.
  - Phase 2 (future): enhancement + rendering + email preview.

ENHANCEMENT PROMPT (locked, Phase 2):
  Do not apply until rendering is wired. Stored here as constant only.

Post types:
  before_after        — 2+ matched before/after photos, confidence >= 0.60
  progress_carousel   — 4+ chronological photos, confidence >= 0.55
  single_progress_post — 2-3 usable photos, confidence >= 0.40
  skip                — insufficient photos or low confidence

Drive destination for Phase 2 (NOT used tonight):
  OPC Proof Posts — separate from carousel_folder_id — TBD with Priscila.
  Originals: never touched. Enhanced copies saved alongside with _enhanced suffix.
"""

import argparse, base64, io, json, mimetypes, os, re, shutil, smtplib, ssl, subprocess, sys, time
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import urllib.request, urllib.parse

# ── Constants ─────────────────────────────────────────────────────────────────

SHEET_ID       = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CATALOG_TAB    = "📸 Photo Catalog"
CANDIDATES_TAB = "📸 Proof Post Candidates"

TOKEN_FILE_PATH = os.environ.get("SHEETS_TOKEN_PATH", "")

# ── Drive path constants — locked 2026-04-21 ──────────────────────────────────
# CONTENT-ATTACHED: enhanced images live inside the run folder (WITH the post assets)
PROOF_POSTS_FOLDER_ID  = "1R4p51rUyGSfgf5VMgFKjQVXl5A399_QI"  # Marketing > Content > Proof Posts
MARKETING_DRIVE_ID     = "0AIPzwsJD_qqzUk9PVA"
# STANDALONE: enhanced images from manual/chat enhancement go here (photo_edit.yml)
STANDALONE_ENHANCED_ID = "1WdxoKIOFIa0E9eREe-uKLDgD4tLVegTw"  # Marketing > Image Creation > Enhanced Photos

# Script paths
_DIR            = os.path.dirname(os.path.abspath(__file__))
EXPORT_PROOF_JS = os.path.join(_DIR, "export_proof.js")

# Runtime credentials — read from env at startup
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")          # unused in enhance; kept for future
REPLICATE_API_KEY  = os.environ.get("PRI_OP_REPLICATE_API_KEY", "")
GMAIL_APP_PASSWORD = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")
PREVIEW_EMAIL_TO   = "priscila@oakpark-construction.com"
PIXEL_DIFF_REJECT_THRESHOLD = 0.15   # fallback gate for legacy enhance_one_photo (not used by core)

# Confidence gates — below these, the group is skipped (do not invent a weak story)
CONFIDENCE_GATE = {
    "before_after":         0.60,
    "progress_carousel":    0.55,
    "single_progress_post": 0.40,
}

# Locked enhancement prompt — stored here for Phase 2, NOT applied in Phase 1.
# Never modify this block without explicit instruction from Priscila.
LOCKED_ENHANCEMENT_PROMPT = """
Enhance this REAL construction/remodel photo to look like professional architectural /
real-estate photography while preserving the exact original jobsite and composition.

STRICT RULES:
- Do NOT recreate, redesign, restyle, or invent anything.
- Do NOT add or remove objects, furniture, decor, tools, materials, landscaping,
  fixtures, cabinets, tile, lighting, windows, doors, walls, people, or shadows
  caused by real objects.
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
- for AFTER photos only: very subtle grass / exterior tidying if it already exists
  in frame, with no new elements added

Phase-aware rule:
- If BEFORE / DURING / PROGRESS: preserve demolition, dust, tools, unfinished work,
  and all visible construction reality.
- If AFTER: keep it polished but still natural and truthful.

Style target:
Professional real-estate / architectural photography, natural light, realistic color,
clean but honest, South Florida residential feel.

NEGATIVE:
no extra objects, no staging, no fake furniture, no new plants, no new windows,
no new doors, no changed cabinets, no changed counters, no changed tile, no fake sky
replacement, no face/body changes, no text/logo/watermark artifacts, no AI hallucinations.
""".strip()

# ── Phase 2 constants ──────────────────────────────────────────────────────────
# Content Creation workspace — proof-post run folders land here
DRIVE_CONTENT_CREATION_FOLDER = "1um7y2Yt8zi9KGxev6kfFJYgrkMYwrCNh"
PROOF_POSTS_PARENT_NAME       = "Proof Posts"
OPENAI_API_KEY                = os.environ.get("OPENAI_API_KEY", "")
GMAIL_APP_PASSWORD            = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")
PREVIEW_EMAIL_TO              = "priscila@oakpark-construction.com"
# 15% mean absolute pixel diff → reject OpenAI output as hallucinated
PIXEL_DIFF_REJECT_THRESHOLD   = 0.15


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_token() -> str:
    td = json.loads(Path(TOKEN_FILE_PATH).read_text())
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    ).read())
    return resp["access_token"]


def _sheets_get(token, range_str):
    enc = urllib.parse.quote(range_str, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read()).get("values", [])


def _sheets_update(token, range_str, values):
    enc = urllib.parse.quote(range_str, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}?valueInputOption=USER_ENTERED"
    body = json.dumps({"values": values}).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def _sheets_append(token, tab, rows):
    enc = urllib.parse.quote(f"'{tab}'!A:Z", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    body = json.dumps({"values": rows}).encode()
    req = urllib.request.Request(url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def _sheets_clear(token, range_str):
    """Clear all cell values in range (POST .../values/{range}:clear)."""
    enc = urllib.parse.quote(range_str, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}:clear"
    req = urllib.request.Request(url, data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def _ensure_candidates_tab(token):
    """Create the candidates tab if it doesn't exist. Idempotent."""
    meta_url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
    req = urllib.request.Request(meta_url, headers={"Authorization": f"Bearer {token}"})
    meta = json.loads(urllib.request.urlopen(req).read())
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if CANDIDATES_TAB in tabs:
        return
    # Create tab
    body = json.dumps({"requests": [{"addSheet": {"properties": {"title": CANDIDATES_TAB}}}]}).encode()
    req = urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate",
        data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req).read()
    # Write header
    header = [[
        "Last Run", "Group Key", "Project", "Service Type", "Room",
        "Post Type", "Confidence", "Above Gate", "Reason",
        "Photo Count", "Before Count", "During/Progress Count", "After Count",
        "Best Before URL", "Best After URL", "Status",
    ]]
    _sheets_append(token, CANDIDATES_TAB, header)
    print(f"[proof-post] Created tab: {CANDIDATES_TAB}")


# ── Phase 1: Grouping ─────────────────────────────────────────────────────────

def read_catalog(token):
    """Read Photo Catalog A:T. Returns (header, data_rows)."""
    rows = _sheets_get(token, f"'{CATALOG_TAB}'!A:T")
    if len(rows) < 2:
        return [], []
    return rows[0], rows[1:]


def group_photos(header, data_rows):
    """
    Filter to client-visible, non-flagged, quality >= 3 photos.
    Group by (project_name, service_type, room).
    Returns dict: {group_key: [photo_dict, ...]}
    """
    col = {h: i for i, h in enumerate(header)}
    groups = {}

    for row in data_rows:
        padded = row + [""] * (len(header) - len(row))

        def get(name, default_idx=None):
            idx = col.get(name, default_idx)
            return padded[idx].strip() if idx is not None and idx < len(padded) else ""

        quality_raw = get("Quality ⭐", 7)
        try:
            quality_int = int(float(quality_raw))
        except (ValueError, TypeError):
            quality_int = 0

        if quality_int < 3:
            continue
        if get("Quality Flag", 18).upper() == "YES":
            continue
        if get("Client Visible", 19).upper() != "YES":
            continue

        project = get("Project Name", 1) or "Unknown"
        service = get("Service Type", 2) or "General"
        room    = get("Room", 15) or "other"
        phase   = get("Phase", 6).lower() or "unknown"

        key = f"{project}|{service}|{room}"
        if key not in groups:
            groups[key] = []
        groups[key].append({
            "project":     project,
            "service":     service,
            "room":        room,
            "phase":       phase,
            "quality":     quality_int,
            "filename":    get("Filename", 3),
            "drive_url":   get("Drive URL", 4),
            "description": get("AI Description", 5),
            "trade":       get("Trade", 16),
            "materials":   get("Materials", 17),
            "date_taken":  get("Date Taken", 10),
        })

    return groups


# ── Phase 1: Candidate Scoring ────────────────────────────────────────────────

def assess_post_type(photos):
    """
    Score a photo group for best post type.
    Returns (post_type, confidence, reason, stats_dict).

    Confidence rules:
    - before_after: base 0.50 + quality bonus + arc bonus
      arc bonus = +0.10 if during/progress also present (shows full journey)
      quality bonus = (avg quality of best before + best after - 6) * 0.10
    - progress_carousel: base 0.40 + count bonus + quality bonus
      count bonus = (usable_count - 4) * 0.04 capped at +0.20
      quality bonus = (avg_quality - 3) * 0.08
    - single_progress_post: base 0.30 + quality bonus
    - skip: 0.0 if < 2 usable photos
    """
    phases = [p["phase"] for p in photos]
    counts = {
        "before":   phases.count("before"),
        "during":   phases.count("during"),
        "after":    phases.count("after"),
        "progress": phases.count("progress"),
    }
    has_before  = counts["before"] >= 1
    has_after   = counts["after"] >= 1
    has_mid     = counts["during"] + counts["progress"] >= 1
    usable      = [p for p in photos if p["phase"] in ("before", "during", "progress", "after")]

    stats = {
        "before_count": counts["before"],
        "mid_count": counts["during"] + counts["progress"],
        "after_count": counts["after"],
    }

    # ── before_after ──
    if has_before and has_after:
        before_q = max(p["quality"] for p in photos if p["phase"] == "before")
        after_q  = max(p["quality"] for p in photos if p["phase"] == "after")
        quality_bonus = ((before_q + after_q) / 2 - 3) * 0.10
        arc_bonus = 0.10 if has_mid else 0.0
        confidence = min(0.50 + quality_bonus + arc_bonus, 0.95)
        best_before = max((p for p in photos if p["phase"] == "before"), key=lambda p: p["quality"])
        best_after  = max((p for p in photos if p["phase"] == "after"),  key=lambda p: p["quality"])
        stats["best_before_url"] = best_before["drive_url"]
        stats["best_after_url"]  = best_after["drive_url"]
        arc_desc = " (full arc: before+mid+after)" if has_mid else ""
        reason = (f"{counts['before']} before + {counts['after']} after"
                  f" | best Q{before_q}+Q{after_q}{arc_desc}")
        return "before_after", round(confidence, 2), reason, stats

    # ── progress_carousel ──
    if len(usable) >= 4:
        avg_q        = sum(p["quality"] for p in usable) / len(usable)
        count_bonus  = min((len(usable) - 4) * 0.04, 0.20)
        quality_bonus = (avg_q - 3) * 0.08
        confidence   = min(0.40 + count_bonus + quality_bonus, 0.85)
        stats["best_before_url"] = next((p["drive_url"] for p in usable if p["phase"] == "before"), "")
        stats["best_after_url"]  = next((p["drive_url"] for p in usable if p["phase"] == "after"), "")
        reason = f"{len(usable)} usable photos | avg Q{avg_q:.1f}"
        return "progress_carousel", round(confidence, 2), reason, stats

    # ── single_progress_post ──
    if len(usable) >= 2:
        avg_q        = sum(p["quality"] for p in usable) / len(usable)
        confidence   = min(0.30 + (avg_q - 3) * 0.05, 0.50)
        stats["best_before_url"] = next((p["drive_url"] for p in usable if p["phase"] == "before"), "")
        stats["best_after_url"]  = next((p["drive_url"] for p in usable if p["phase"] == "after"), "")
        reason = f"{len(usable)} photos — not enough for carousel, single post only"
        return "single_progress_post", round(confidence, 2), reason, stats

    stats["best_before_url"] = ""
    stats["best_after_url"]  = ""
    return "skip", 0.0, f"Only {len(photos)} eligible photo(s) — insufficient", stats


def select_candidates(groups):
    """Score all groups. Return sorted list of candidates (highest confidence first)."""
    candidates = []
    for key, photos in groups.items():
        project, service, room = key.split("|", 2)
        post_type, confidence, reason, stats = assess_post_type(photos)
        if post_type == "skip":
            continue
        gate = CONFIDENCE_GATE.get(post_type, 0.5)
        candidates.append({
            "group_key":    key,
            "project":      project,
            "service":      service,
            "room":         room,
            "post_type":    post_type,
            "confidence":   confidence,
            "above_gate":   confidence >= gate,
            "reason":       reason,
            "photo_count":  len(photos),
            "stats":        stats,
        })
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates


# ── Phase 1: Write Candidates Tab ─────────────────────────────────────────────

def write_candidates(token, candidates):
    """
    Overwrite the candidates tab with today's run results.
    Clears previous run rows, re-writes header + new results.
    """
    today = date.today().isoformat()

    # Clear existing data (keep header in row 1, clear from row 2 down)
    _sheets_clear(token, f"'{CANDIDATES_TAB}'!A2:P1000")

    if not candidates:
        print("[proof-post] No candidates found.")
        return

    rows = []
    for c in candidates:
        s = c["stats"]
        rows.append([
            today,
            c["group_key"],
            c["project"],
            c["service"],
            c["room"],
            c["post_type"],
            c["confidence"],
            "Yes" if c["above_gate"] else "No",
            c["reason"],
            c["photo_count"],
            s.get("before_count", 0),
            s.get("mid_count", 0),
            s.get("after_count", 0),
            s.get("best_before_url", ""),
            s.get("best_after_url", ""),
            "Pending" if c["above_gate"] else "Below Gate",
        ])

    _sheets_update(token, f"'{CANDIDATES_TAB}'!A2", rows)
    above = sum(1 for c in candidates if c["above_gate"])
    print(f"[proof-post] {len(candidates)} groups scored | {above} above confidence gate | written to {CANDIDATES_TAB}")


# ── Phase 2: Drive helpers ─────────────────────────────────────────────────────

def _drive_api_post(token, endpoint, body):
    """POST to Drive API with supportsAllDrives=true. Returns parsed JSON."""
    url = f"https://www.googleapis.com/drive/v3/{endpoint}?supportsAllDrives=true"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read())


def _drive_find_or_create_folder(token, name, parent_id):
    """Find existing folder by name under parent, or create it. Returns folder_id."""
    safe_name = name.replace("'", "\\'")
    q = (f"name='{safe_name}' and mimeType='application/vnd.google-apps.folder'"
         f" and '{parent_id}' in parents and trashed=false")
    url = (f"https://www.googleapis.com/drive/v3/files"
           f"?q={urllib.parse.quote(q)}&supportsAllDrives=true"
           f"&includeItemsFromAllDrives=true&fields=files(id)")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    files = json.loads(urllib.request.urlopen(req).read()).get("files", [])
    if files:
        return files[0]["id"]
    return _drive_api_post(token, "files", {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    })["id"]


def _mime_for(filename):
    ext = Path(filename).suffix.lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".txt": "text/plain"}.get(ext, "application/octet-stream")


def _drive_upload_file_multipart(token, local_path, filename, parent_folder_id):
    """Multipart upload to Drive. Returns (file_id, web_view_link)."""
    import requests as _req  # requests is in requirements_content.txt
    meta = json.dumps({"name": filename, "parents": [parent_folder_id]})
    with open(local_path, "rb") as fh:
        resp = _req.post(
            "https://www.googleapis.com/upload/drive/v3/files"
            "?uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "data": ("metadata", meta, "application/json; charset=UTF-8"),
                "file": (filename, fh, _mime_for(filename)),
            },
        )
    resp.raise_for_status()
    data = resp.json()
    fid = data["id"]
    return fid, data.get("webViewLink", f"https://drive.google.com/file/d/{fid}/view")


def _extract_file_id(url):
    """Extract Drive file ID from webViewLink or other Drive URL formats."""
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", url.strip()):
        return url.strip()
    return None


def _download_photo_from_drive(token, file_id, dest_dir, stem):
    """Download Drive file by ID to dest_dir/{stem}_original.jpg. Returns Path."""
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    data = urllib.request.urlopen(req).read()
    out = Path(dest_dir) / f"{stem}_original.jpg"
    out.write_bytes(data)
    return out



# ── Phase 2: Enhancement ───────────────────────────────────────────────────────
# Shared core: photo_enhance_core.py (PIL → Real-ESRGAN → original fallback).
# This script calls enhance_one_photo() which delegates to that core.
# Standalone enhancement (manual/chat) uses photo_edit.yml → saves to Enhanced Photos.
# Content-attached enhancement (here) saves inside run_folder/enhanced/ only.

def enhance_one_photo(local_path, file_id, _unused=None, phase="after"):
    """
    Enhance a single photo via shared photo_enhance_core (PIL → Real-ESRGAN → original).
    Content-attached mode: result saved alongside original in same directory.
    Returns (result_path, fallback_used, reason_str).

    Routing:
      content-attached (here): enhanced copy lives in run_folder/enhanced/ — NOT in standalone path.
      standalone (photo_edit.yml): caller saves to Marketing > Image Creation > Enhanced Photos.
    """
    from photo_enhance_core import enhance as _core

    local_path    = Path(local_path)
    enhanced_path = local_path.parent / local_path.name.replace("_original.jpg", "_enhanced.jpg")
    sidecar_path  = local_path.parent / local_path.name.replace("_original.jpg", "_enhanced.source.txt")

    raw    = local_path.read_bytes()
    result = _core(raw, phase=phase, replicate_key=REPLICATE_API_KEY)

    if result["enhanced"]:
        enhanced_path.write_bytes(result["enhanced_bytes"])
        sidecar_path.write_text(
            f"tier={result['provider']}\nssim={result['ssim']}\n"
            f"file_id={file_id}\nfetched_at={date.today().isoformat()}\n"
        )
        return enhanced_path, False, f"ok(ssim={result['ssim']})"

    # Route C: original fallback
    shutil.copy(str(local_path), str(enhanced_path))
    sidecar_path.write_text(
        f"tier=original_fallback\nssim={result['ssim']}\n"
        f"file_id={file_id}\nfetched_at={date.today().isoformat()}\n"
    )
    return enhanced_path, True, f"original_fallback(ssim={result['ssim']})"


# ── Phase 2: Preview collage ───────────────────────────────────────────────────

def build_proof_collage(photo_entries, output_path, post_type):
    """
    Stitch a preview collage from enhanced photo paths. PIL only.
    photo_entries: [{"local_path": str, "phase": str, "quality": int, "fallback": bool}]

    PREVIEW ONLY — this is not the final proof-post carousel format.
    Final format (HTML + Playwright export) comes in a later phase.
    """
    from PIL import Image as _PIL, ImageDraw

    CARD_W, CARD_H, LABEL_H, BORDER = 540, 540, 36, 4

    if post_type == "before_after":
        befores = [e for e in photo_entries if e.get("phase") == "before"]
        afters  = [e for e in photo_entries if e.get("phase") == "after"]
        sel = []
        if befores:
            sel.append(max(befores, key=lambda x: x.get("quality", 0)))
        if afters:
            sel.append(max(afters,  key=lambda x: x.get("quality", 0)))
    else:
        sel = sorted(photo_entries, key=lambda x: x.get("quality", 0), reverse=True)[:4]

    if not sel:
        return None

    cols   = min(len(sel), 2)
    rows_n = (len(sel) + cols - 1) // cols
    canvas = _PIL.new("RGB", (cols * CARD_W, rows_n * (CARD_H + LABEL_H)), (20, 20, 20))
    draw   = ImageDraw.Draw(canvas)

    for i, entry in enumerate(sel):
        col_i = i % cols
        row_i = i // cols
        x = col_i * CARD_W
        y = row_i * (CARD_H + LABEL_H)
        try:
            img = _PIL.open(entry["local_path"]).convert("RGB")
            img.thumbnail((CARD_W - BORDER * 2, CARD_H - BORDER * 2), _PIL.LANCZOS)
            px = x + BORDER + (CARD_W - BORDER * 2 - img.width) // 2
            py = y + BORDER + (CARD_H - BORDER * 2 - img.height) // 2
            canvas.paste(img, (px, py))
        except Exception:
            pass
        lbl_y = y + CARD_H
        draw.rectangle([x, lbl_y, x + CARD_W, lbl_y + LABEL_H], fill=(40, 40, 40))
        fallback_mark = " [orig]" if entry.get("fallback") else ""
        try:
            draw.text(
                (x + 8, lbl_y + 8),
                f"{entry.get('phase','?')} Q{entry.get('quality','?')}{fallback_mark}",
                fill=(200, 200, 200),
            )
        except Exception:
            pass  # no system font on runner — skip label text

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out), "JPEG", quality=90)
    return out


# ── Phase 2: Email preview ─────────────────────────────────────────────────────

def send_proof_preview_email(project, post_type, confidence, group_key,
                             run_folder_link, subfolder_links,
                             enhancement_summary, smtp_password):
    """Send HTML preview email via smtplib SMTP_SSL (PRI_OP_GMAIL_APP_PASSWORD)."""
    enh   = enhancement_summary.get("enhanced", 0)
    fall  = enhancement_summary.get("fallback", 0)
    total = enh + fall
    if total == 0:
        flag = "⚠️ NO PHOTOS — "
    elif fall == total:
        flag = "⚠️ NO ENHANCEMENT — "
    elif fall > 0:
        flag = "⚠️ PARTIAL — "
    else:
        flag = ""

    subject      = f"{flag}OPC Proof Post Preview — {project} | {post_type} | conf={confidence}"
    collage_link = subfolder_links.get("collage", subfolder_links.get("png", "#"))

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:20px;">
<h2 style="color:#0a0a0a;">OPC Proof Post Preview</h2>
<table style="border-collapse:collapse;width:100%;margin-bottom:16px;">
  <tr><td style="padding:4px 8px;font-weight:bold;">Project</td><td>{project}</td></tr>
  <tr><td style="padding:4px 8px;font-weight:bold;">Post type</td><td>{post_type}</td></tr>
  <tr><td style="padding:4px 8px;font-weight:bold;">Confidence</td><td>{confidence}</td></tr>
  <tr><td style="padding:4px 8px;font-weight:bold;">Group key</td><td><code>{group_key}</code></td></tr>
  <tr><td style="padding:4px 8px;font-weight:bold;">Enhancement</td>
      <td>{enh}/{total} enhanced &nbsp;|&nbsp; {fall}/{total} fallback (original used)</td></tr>
</table>
<p>
  <a href="{collage_link}" style="background:#cbcc10;color:#0a0a0a;padding:10px 18px;
     text-decoration:none;font-weight:bold;border-radius:4px;">View Preview Collage</a>
</p>
<p><a href="{run_folder_link}">Open full run folder in Drive</a></p>
<ul style="line-height:1.8;">
  <li><a href="{subfolder_links.get('originals_used','#')}">originals_used/</a></li>
  <li><a href="{subfolder_links.get('enhanced','#')}">enhanced/</a></li>
  <li><a href="{subfolder_links.get('png','#')}">png/ (preview collage)</a></li>
</ul>
<hr style="margin:20px 0;">
<h3>Reply to approve or reject</h3>
<p>
  <strong>APPROVE</strong> → proceed to final carousel build<br>
  <strong>REJECT</strong> → discard this candidate<br>
  <strong>SKIP</strong> → keep as candidate for later
</p>
<p style="font-size:11px;color:#999;margin-top:32px;">
  OPC Proof-Post Pipeline — Phase 2 preview asset only, not final carousel format
</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = PREVIEW_EMAIL_TO
    msg["To"]      = PREVIEW_EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
        srv.login(PREVIEW_EMAIL_TO, smtp_password)
        srv.sendmail(PREVIEW_EMAIL_TO, [PREVIEW_EMAIL_TO], msg.as_string())
    print(f"[proof-post] Preview email sent → {PREVIEW_EMAIL_TO}")


# ── Phase 2: Orchestration ─────────────────────────────────────────────────────

def _pick_top_candidate(token):
    """Read candidates tab, return group_key of the first above-gate row."""
    rows = _sheets_get(token, f"'{CANDIDATES_TAB}'!A:P")
    if len(rows) < 2:
        return None
    header    = rows[0]
    col_above = next((i for i, h in enumerate(header) if "Above Gate" in h), None)
    col_key   = next((i for i, h in enumerate(header) if "Group Key"  in h), None)
    if col_above is None or col_key is None:
        return None
    for row in rows[1:]:
        padded = row + [""] * (max(col_above, col_key) + 1 - len(row))
        if padded[col_above].strip() == "Yes":
            return padded[col_key].strip()
    return None


def _update_candidate_status(token, group_key, new_status):
    """Write new_status to the Status column for the matching group_key row."""
    rows = _sheets_get(token, f"'{CANDIDATES_TAB}'!A:P")
    if len(rows) < 2:
        return
    header     = rows[0]
    col_key    = next((i for i, h in enumerate(header) if "Group Key" in h), None)
    col_status = next((i for i, h in enumerate(header) if h.strip() == "Status"), None)
    if col_key is None or col_status is None:
        return
    for row_idx, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (max(col_key, col_status) + 1 - len(row))
        if padded[col_key].strip() == group_key:
            col_letter = chr(ord("A") + col_status)
            _sheets_update(token, f"'{CANDIDATES_TAB}'!{col_letter}{row_idx}", [[new_status]])
            print(f"[proof-post] Status → '{new_status}' for {group_key}")
            return


def _get_catalog_photos_for_group(token, group_key):
    """Re-read Photo Catalog and return the photos matching group_key."""
    header, data_rows = read_catalog(token)
    if not header:
        return []
    return group_photos(header, data_rows).get(group_key, [])


def _next_version_number(token, slug):
    """Return next unused vN number under PROOF_POSTS_FOLDER_ID for this slug."""
    q = f"'{PROOF_POSTS_FOLDER_ID}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder'"
    url = (f"https://www.googleapis.com/drive/v3/files"
           f"?q={urllib.parse.quote(q)}&supportsAllDrives=true"
           f"&includeItemsFromAllDrives=true&fields=files(name)")
    req   = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    names = [f["name"] for f in json.loads(urllib.request.urlopen(req).read()).get("files", [])]
    n = 1
    while f"v{n}_proof-{slug}" in names:
        n += 1
    return n


def run_phase2(token, group_key):
    """
    Phase 2 orchestrator: download originals → enhance (PIL core) → build collage →
    upload all to Drive run folder → send preview email.

    Storage rule (content-attached):
      Enhanced images go INSIDE the run folder at: Proof Posts/v<N>_proof-<slug>/enhanced/
      NOT in the standalone Marketing > Image Creation > Enhanced Photos folder.

    Fully isolated: zero contact with Brazil/news flows, main.py, or
    carousel_builder.py. Only reads Photo Catalog and writes to Proof Posts/.
    """
    if not GMAIL_APP_PASSWORD:
        print("❌ PRI_OP_GMAIL_APP_PASSWORD not set — cannot send preview email")
        sys.exit(1)

    print(f"\n[proof-post Phase 2] Group: {group_key}")
    photos = _get_catalog_photos_for_group(token, group_key)
    if not photos:
        print(f"❌ No photos found for group: {group_key}")
        sys.exit(1)

    post_type, confidence, reason, _ = assess_post_type(photos)
    parts   = (group_key + "||").split("|", 2)
    project = parts[0]
    print(f"[proof-post] {len(photos)} photos | {post_type} | conf={confidence}")

    slug    = re.sub(r"[^a-z0-9]+", "-", group_key.lower()).strip("-")[:40]
    ver_n   = _next_version_number(token, slug)
    run_name = f"v{ver_n}_proof-{slug}"   # follows CAROUSEL_FOLDER_STANDARD

    # ── Local temp structure (mirrors Drive structure) ─────────────────────────
    run_dir = Path(f"/tmp/proof_post_{slug}")
    for sub in ("originals_used", "enhanced", "png", "motion", "html", "review"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # ── Drive structure (content-attached routing) ─────────────────────────────
    # Marketing > Content > Proof Posts > v<N>_proof-<slug>/
    #   originals_used/  enhanced/  png/  motion/  html/  review/
    # Enhanced images live INSIDE this run folder — NOT in standalone Enhanced Photos.
    run_fid = _drive_find_or_create_folder(token, run_name, PROOF_POSTS_FOLDER_ID)
    sub_ids, sub_links = {}, {}
    for sub in ("originals_used", "enhanced", "png", "motion", "html", "review"):
        fid = _drive_find_or_create_folder(token, sub, run_fid)
        sub_ids[sub]   = fid
        sub_links[sub] = f"https://drive.google.com/drive/folders/{fid}"
    run_link = f"https://drive.google.com/drive/folders/{run_fid}"
    print(f"[proof-post] Drive run folder: {run_link}")

    # ── Download + enhance (cap at 6 photos to control OpenAI cost per test) ──
    photos_to_use  = sorted(photos, key=lambda p: p["quality"], reverse=True)[:6]
    enhanced_entries = []
    summary = {"enhanced": 0, "fallback": 0}

    for p in photos_to_use:
        fid = _extract_file_id(p.get("drive_url", ""))
        if not fid:
            print(f"  ⚠️  No file_id in URL: {p.get('drive_url','')[:60]}")
            continue

        print(f"  ↓ {p['filename']} ...")
        try:
            orig = _download_photo_from_drive(
                token, fid, run_dir / "originals_used", fid)
        except Exception as e:
            print(f"  ❌ Download failed: {e}")
            continue

        try:
            _drive_upload_file_multipart(
                token, str(orig), orig.name, sub_ids["originals_used"])
        except Exception as e:
            print(f"  ⚠️  Upload original failed (non-fatal): {e}")

        print(f"  ✨ Enhancing (PIL core, phase={p.get('phase','after')}) ...")
        enh_path, is_fallback, enh_reason = enhance_one_photo(
            str(orig), fid, phase=p.get("phase", "after"))
        if is_fallback:
            summary["fallback"] += 1
            print(f"  ⚠️  Fallback: {enh_reason}")
        else:
            summary["enhanced"] += 1
            print(f"  ✅ Enhanced: {enh_reason}")

        try:
            _drive_upload_file_multipart(
                token, str(enh_path), enh_path.name, sub_ids["enhanced"])
        except Exception as e:
            print(f"  ⚠️  Upload enhanced failed (non-fatal): {e}")

        enhanced_entries.append({
            "local_path": str(enh_path),
            "phase":      p.get("phase", "unknown"),
            "quality":    p.get("quality", 0),
            "fallback":   is_fallback,
        })

    if not enhanced_entries:
        print("❌ No photos processed successfully — aborting Phase 2")
        sys.exit(1)

    # ── Preview collage ────────────────────────────────────────────────────────
    collage_name  = f"collage_{slug}_{today_s.replace('-','')}.jpg"
    collage_local = run_dir / "png" / collage_name
    print("[proof-post] Building preview collage ...")
    result = build_proof_collage(enhanced_entries, str(collage_local), post_type)
    if result and collage_local.exists():
        try:
            _, clnk = _drive_upload_file_multipart(
                token, str(collage_local), collage_name, sub_ids["png"])
            sub_links["collage"] = clnk
            print(f"  ✅ Collage uploaded: {clnk}")
        except Exception as e:
            print(f"  ⚠️  Collage upload failed: {e}")
            sub_links["collage"] = sub_links["png"]

    # ── Preview email ──────────────────────────────────────────────────────────
    print("[proof-post] Sending preview email ...")
    send_proof_preview_email(
        project=project, post_type=post_type, confidence=confidence,
        group_key=group_key, run_folder_link=run_link,
        subfolder_links=sub_links, enhancement_summary=summary,
        smtp_password=GMAIL_APP_PASSWORD,
    )

    _update_candidate_status(token, group_key, "Preview Sent")

    print(f"\n✅ Phase 2 complete")
    print(f"   Run folder : {run_link}")
    print(f"   Enhanced   : {summary['enhanced']} | Fallback: {summary['fallback']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OPC Proof-Post Flow")
    parser.add_argument("--phase", type=int, default=1, choices=[1, 2],
                        help="Pipeline phase: 1=candidate scan, 2=enhance+preview")
    parser.add_argument("--group-key", default=None,
                        help="Phase 2: group key to process (default: top above-gate candidate)")
    args = parser.parse_args()

    if not TOKEN_FILE_PATH or not Path(TOKEN_FILE_PATH).exists():
        print("❌ SHEETS_TOKEN_PATH not set or missing")
        sys.exit(1)

    print(f"\n🏗️  OPC Proof-Post — Phase {args.phase} — {date.today()}")
    token = _get_token()
    _ensure_candidates_tab(token)

    if args.phase == 1:
        header, data_rows = read_catalog(token)
        if not header:
            print("❌ Photo Catalog is empty — run photo-catalog.yml first")
            sys.exit(0)
        print(f"[proof-post] Catalog rows: {len(data_rows)}")

        groups = group_photos(header, data_rows)
        print(f"[proof-post] Groups (project+service+room): {len(groups)}")
        for key, photos in sorted(groups.items(), key=lambda x: -len(x[1]))[:10]:
            phases = [p["phase"] for p in photos]
            print(f"  {key} — {len(photos)} photos — phases: {phases}")

        candidates = select_candidates(groups)
        print(f"[proof-post] Candidates scored: {len(candidates)}")
        for c in candidates[:5]:
            gate_marker = "✅" if c["above_gate"] else "⚠️"
            print(f"  {gate_marker} {c['post_type']} | conf={c['confidence']} | {c['project']} {c['room']} | {c['reason']}")

        write_candidates(token, candidates)
        print(f"\n✅ Proof-post scan complete — see tab: {CANDIDATES_TAB}")

    elif args.phase == 2:
        # Accept group key from: --group-key arg OR PROOF_GROUP_KEY env var OR auto-pick top candidate
        group_key = args.group_key or os.environ.get("PROOF_GROUP_KEY", "").strip() or _pick_top_candidate(token)
        if not group_key:
            print("❌ No above-gate candidates found — run Phase 1 first")
            sys.exit(0)
        print(f"[proof-post] Phase 2 target: {group_key}")
        run_phase2(token, group_key)


if __name__ == "__main__":
    main()
