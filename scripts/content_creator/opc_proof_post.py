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
BUFFER_API_KEY     = os.environ.get("BUFFER_API_KEY", "")
BUFFER_GRAPHQL_URL = "https://api.buffer.com"
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


# ── Phase 2: Proof-post HTML slide builder ─────────────────────────────────────

def _img_to_b64(local_path: str) -> str:
    """Encode image file as base64 data URI for inline HTML embedding."""
    data = Path(local_path).read_bytes()
    mime = mimetypes.guess_type(local_path)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def build_proof_slides_html(enhanced_entries, post_type, project, today_s, group_key) -> str:
    """
    Build Instagram-format HTML proof-post slides (1080×1350 each).
    Photos embedded as base64 data URIs — no network requests needed at render time.
    Returns full HTML string; caller writes to disk and runs export_proof.js.
    """
    OBSIDIAN = "#0A0A0A"
    CREAM    = "#F0EBE3"
    LIME     = "#CBCC10"

    parts   = (group_key + "|||").split("|", 3)
    address = parts[0].strip().title()
    service = parts[1].strip()
    room    = parts[2].strip()

    entries = sorted(enhanced_entries, key=lambda e: (
        {"before": 0, "during": 1, "progress": 1, "after": 2}.get(e.get("phase", ""), 3),
        -e.get("quality", 0),
    ))

    slide_css = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Anton&family=Roboto+Condensed:wght@400;700&display=swap');
  *{{margin:0;padding:0;box-sizing:border-box;}}
  .slide{{width:1080px;height:1350px;background:{OBSIDIAN};position:relative;overflow:hidden;
         font-family:'Roboto Condensed',Arial,sans-serif;display:block;}}
  .slide-photo{{width:100%;height:100%;object-fit:cover;position:absolute;top:0;left:0;}}
  .ov-bot{{position:absolute;bottom:0;left:0;right:0;height:50%;
           background:linear-gradient(to top,rgba(10,10,10,.93) 0%,rgba(10,10,10,0) 100%);}}
  .ov-top{{position:absolute;top:0;left:0;right:0;height:22%;
           background:linear-gradient(to bottom,rgba(10,10,10,.78) 0%,rgba(10,10,10,0) 100%);}}
  .opc-mark{{position:absolute;top:40px;right:48px;color:{LIME};
             font-family:'Anton',Impact,sans-serif;font-size:30px;letter-spacing:5px;}}
  .badge{{display:inline-block;background:{LIME};color:{OBSIDIAN};
          font-family:'Anton',Impact,sans-serif;font-size:20px;letter-spacing:3px;
          padding:7px 18px;text-transform:uppercase;margin-bottom:18px;}}
  .slide-content{{position:absolute;bottom:56px;left:56px;right:56px;color:{CREAM};}}
  .proj-title{{font-family:'Anton',Impact,sans-serif;font-size:60px;line-height:1.0;
               color:{CREAM};text-transform:uppercase;margin-bottom:10px;}}
  .proj-sub{{font-size:26px;color:{LIME};font-weight:700;text-transform:uppercase;
             letter-spacing:2px;}}
  .date-line{{font-size:19px;color:rgba(240,235,227,.6);margin-top:14px;letter-spacing:1px;}}
  .phase-lbl{{position:absolute;top:40px;left:48px;background:rgba(10,10,10,.82);
              border-left:4px solid {LIME};color:{CREAM};font-size:20px;font-weight:700;
              padding:7px 18px;text-transform:uppercase;letter-spacing:2px;}}
  .photo-n{{position:absolute;bottom:36px;right:48px;color:rgba(240,235,227,.45);
            font-size:19px;font-family:'Roboto Condensed',Arial,sans-serif;}}
  .outro{{width:1080px;height:1350px;background:{OBSIDIAN};display:flex;flex-direction:column;
          align-items:center;justify-content:center;position:relative;overflow:hidden;}}
  .outro-opc{{font-family:'Anton',Impact,sans-serif;font-size:128px;color:{LIME};
              letter-spacing:10px;line-height:1;}}
  .outro-line{{width:100px;height:3px;background:{LIME};margin:28px auto;}}
  .outro-name{{font-size:34px;color:{CREAM};font-family:'Roboto Condensed',Arial,sans-serif;
               font-weight:700;text-transform:uppercase;letter-spacing:3px;margin-bottom:20px;}}
  .outro-cta{{font-size:24px;color:rgba(240,235,227,.60);letter-spacing:1px;
              text-transform:uppercase;text-align:center;max-width:800px;line-height:1.4;}}
  .outro-lic{{position:absolute;bottom:36px;font-size:17px;color:rgba(240,235,227,.28);
              letter-spacing:1px;}}
</style>"""

    slides = []
    total = len(entries) + 1  # photos + outro

    # ── Cover slide ──
    best_b64 = _img_to_b64(entries[0]["local_path"])
    cover_phase = entries[0].get("phase", "progress").upper()
    slides.append(f"""
<div class="slide">
  <img class="slide-photo" src="{best_b64}" />
  <div class="ov-top"></div><div class="ov-bot"></div>
  <div class="opc-mark">OPC</div>
  <div class="slide-content">
    <div class="badge">Progress Update</div>
    <div class="proj-title">{address}</div>
    <div class="proj-sub">{service}</div>
    <div class="date-line">{today_s}</div>
  </div>
</div>""")

    # ── Photo slides ──
    for i, entry in enumerate(entries[1:], start=2):
        b64   = _img_to_b64(entry["local_path"])
        phase = entry.get("phase", "progress").upper()
        fallback_note = " [orig]" if entry.get("fallback") else ""
        slides.append(f"""
<div class="slide">
  <img class="slide-photo" src="{b64}" />
  <div class="ov-top"></div><div class="ov-bot"></div>
  <div class="opc-mark">OPC</div>
  <div class="phase-lbl">{phase}{fallback_note}</div>
  <div class="photo-n">{i} / {total}</div>
</div>""")

    # ── Outro slide ──
    cta_text = (room or service or "Quality Craftsmanship")
    slides.append(f"""
<div class="slide outro">
  <div class="outro-opc">OPC</div>
  <div class="outro-line"></div>
  <div class="outro-name">Oak Park Construction</div>
  <div class="outro-cta">{cta_text}</div>
  <div class="outro-lic">© {today_s[:4]} Oak Park Construction — License CBC1263425</div>
</div>""")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>OPC Proof Post — {address}</title>
{slide_css}
</head><body style="background:#111;padding:0;margin:0;">
{"".join(slides)}
</body></html>"""


def render_proof_pngs(html_path: str, out_dir: str) -> list:
    """Run export_proof.js → list of rendered proof_NN.png paths (sorted)."""
    js = os.environ.get("EXPORT_PROOF_JS", EXPORT_PROOF_JS)
    if not os.path.exists(js):
        print(f"  ⚠️  export_proof.js not found at {js} — skipping PNG render")
        return []
    try:
        r = subprocess.run(["node", js, html_path, out_dir],
                           capture_output=True, text=True, timeout=120)
        for line in r.stdout.splitlines():
            print(f"  [playwright] {line}")
        if r.returncode != 0:
            print(f"  ⚠️  export_proof.js stderr: {r.stderr[:400]}")
            return []
        return sorted(Path(out_dir).glob("proof_*.png"))
    except Exception as e:
        print(f"  ⚠️  PNG render failed (non-fatal): {e}")
        return []


def render_proof_motion(cover_png: str, out_dir: str, duration_s: int = 5) -> dict:
    """Ken Burns zoompan on cover PNG → MP4 + GIF + preview_frame.jpg.
    Non-fatal: returns dict with None values on failure."""
    out        = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mp4_path   = out / "cover_motion.mp4"
    gif_path   = out / "cover_motion.gif"
    frame_path = out / "preview_frame.jpg"
    fps        = 30
    frames     = duration_s * fps

    # Slow zoom-in 1.0→1.05x with gentle pan — construction photo feels
    zp = (f"zoompan=z='min(zoom+0.0003,1.05)':"
          f"x='iw/2-(iw/zoom/2)+sin(on/{fps})*4':"
          f"y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1350:fps={fps}")
    try:
        r = subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", cover_png,
            "-vf", zp, "-t", str(duration_s),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(mp4_path),
        ], capture_output=True, timeout=90)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-400:].decode("utf-8", "replace"))
        print(f"  ✅ Motion MP4 → {mp4_path.name}")

        subprocess.run([
            "ffmpeg", "-y", "-i", str(mp4_path), "-t", "3",
            "-vf", "fps=5,scale=540:-1:flags=lanczos", str(gif_path),
        ], capture_output=True, timeout=60)
        if gif_path.exists():
            print(f"  ✅ Motion GIF → {gif_path.name}")

        subprocess.run([
            "ffmpeg", "-y", "-i", str(mp4_path),
            "-frames:v", "1", "-q:v", "2", str(frame_path),
        ], capture_output=True, timeout=15)

        return {"mp4": str(mp4_path), "gif": str(gif_path) if gif_path.exists() else None,
                "frame": str(frame_path) if frame_path.exists() else None}
    except Exception as e:
        print(f"  ⚠️  Motion render failed (non-fatal): {e}")
        try:
            shutil.copy(cover_png, str(frame_path))
        except Exception:
            pass
        return {"mp4": None, "gif": None,
                "frame": str(frame_path) if frame_path.exists() else None}


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
    slides_link  = subfolder_links.get("slides", subfolder_links.get("png", "#"))
    motion_link  = subfolder_links.get("motion_folder", "")

    slides_row = (f'<li><a href="{slides_link}">png/ — proof slides (Instagram format)</a></li>'
                  if slides_link else "")
    motion_row = (f'<li><a href="{motion_link}">motion/ — Ken Burns MP4 + GIF</a></li>'
                  if motion_link else "")

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
  &nbsp;
  <a href="{slides_link}" style="background:#0a0a0a;color:#cbcc10;padding:10px 18px;
     text-decoration:none;font-weight:bold;border-radius:4px;border:2px solid #cbcc10;">View Proof Slides (PNG)</a>
</p>
<p><a href="{run_folder_link}">Open full run folder in Drive</a></p>
<ul style="line-height:1.8;">
  <li><a href="{subfolder_links.get('originals_used','#')}">originals_used/ — downloaded originals</a></li>
  <li><a href="{subfolder_links.get('enhanced','#')}">enhanced/ — PIL-enhanced copies</a></li>
  {slides_row}
  {motion_row}
  <li><a href="{subfolder_links.get('html','#')}">html/ — proof_slides.html source</a></li>
</ul>
<hr style="margin:20px 0;">
<h3>Reply to approve or reject</h3>
<p>
  <strong>APPROVE</strong> → proceed to Buffer scheduling<br>
  <strong>REJECT</strong> → discard this candidate<br>
  <strong>SKIP</strong> → keep as candidate for later
</p>
<p style="font-size:11px;color:#999;margin-top:32px;">
  OPC Proof-Post Pipeline — opc-proof-post.yml Phase 2
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
    today_s = date.today().isoformat()
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

    # ── Proof-post HTML slides + PNG render ────────────────────────────────────
    print("[proof-post] Building proof-post HTML slides ...")
    proof_html = build_proof_slides_html(
        enhanced_entries, post_type, project, today_s, group_key)
    html_path  = run_dir / "html" / "proof_slides.html"
    html_path.write_text(proof_html, encoding="utf-8")
    try:
        _drive_upload_file_multipart(
            token, str(html_path), "proof_slides.html", sub_ids["html"])
        print(f"  ✅ HTML uploaded to html/")
    except Exception as e:
        print(f"  ⚠️  HTML upload failed (non-fatal): {e}")

    print("[proof-post] Rendering proof PNGs via Playwright ...")
    png_dir   = run_dir / "png"
    proof_pngs = render_proof_pngs(str(html_path), str(png_dir))
    for png in proof_pngs:
        try:
            _drive_upload_file_multipart(token, str(png), png.name, sub_ids["png"])
            print(f"  ✅ Uploaded {png.name}")
        except Exception as e:
            print(f"  ⚠️  PNG upload failed {png.name}: {e}")
    if proof_pngs:
        sub_links["slides"] = sub_links["png"]

    # ── Ken Burns motion (cover PNG) ───────────────────────────────────────────
    cover_png = str(proof_pngs[0]) if proof_pngs else None
    motion_links = {}
    if cover_png and Path(cover_png).exists():
        print("[proof-post] Rendering Ken Burns motion ...")
        motion_result = render_proof_motion(cover_png, str(run_dir / "motion"))
        for key, fpath in motion_result.items():
            if fpath and Path(fpath).exists():
                fname = Path(fpath).name
                try:
                    _, mlink = _drive_upload_file_multipart(
                        token, fpath, fname, sub_ids["motion"])
                    motion_links[key] = mlink
                    print(f"  ✅ Motion uploaded: {fname}")
                except Exception as e:
                    print(f"  ⚠️  Motion upload failed {fname}: {e}")
        if motion_links:
            sub_links["motion_folder"] = sub_links["motion"]
    else:
        print("  ⚠️  No cover PNG available — skipping motion render")

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
    print(f"   Slides     : {len(proof_pngs)} PNGs rendered")
    print(f"   Motion     : {'✅ MP4+GIF' if motion_links.get('mp4') else '⚠️ skipped'}")


# ── Phase 3: Approval handler ─────────────────────────────────────────────────

def run_phase3(token, group_key, decision):
    """
    Record APPROVE / REJECT / SKIP decision for a proof-post candidate.

    Decision effects:
      APPROVE → status = 'Approved' — downstream Phase 4 can schedule/post
      REJECT  → status = 'Rejected' — candidate removed from queue
      SKIP    → status = 'Skipped'  — candidate stays available for next cycle

    Reads PROOF_GROUP_KEY + PROOF_DECISION from env (or passed args).
    """
    decision = decision.strip().upper()
    if decision not in ("APPROVE", "REJECT", "SKIP"):
        print(f"❌ Invalid decision '{decision}' — must be APPROVE, REJECT, or SKIP")
        sys.exit(1)

    status_map = {
        "APPROVE": "Approved",
        "REJECT":  "Rejected",
        "SKIP":    "Skipped",
    }
    new_status = status_map[decision]

    print(f"\n[proof-post Phase 3] Decision: {decision} → {group_key}")

    # Update status in Candidates tab
    _update_candidate_status(token, group_key, new_status)

    # Write decided_at timestamp to column Q (index 16, zero-based)
    rows = _sheets_get(token, f"'{CANDIDATES_TAB}'!A:Q")
    if len(rows) >= 2:
        header  = rows[0]
        col_key = next((i for i, h in enumerate(header) if "Group Key" in h), None)
        if col_key is not None:
            for row_idx, row in enumerate(rows[1:], start=2):
                padded = row + [""] * (max(col_key, 16) + 1 - len(row))
                if padded[col_key].strip() == group_key:
                    _sheets_update(token, f"'{CANDIDATES_TAB}'!Q{row_idx}",
                                   [[date.today().isoformat()]])
                    break

    print(f"✅ Phase 3 complete — {group_key} → {new_status}")

    if decision == "APPROVE":
        print(f"[proof-post] APPROVED — Phase 4 (schedule/publish) can now run for: {group_key}")
        # Phase 4 trigger: the APPROVED row is the signal — opc-proof-post.yml Phase 4 reads it
        # No inline trigger here; separate workflow dispatch with run_phase=4


# ── Phase 4: Buffer scheduling (GraphQL API) ──────────────────────────────────

def _buffer_graphql(query, variables=None):
    """Execute a Buffer GraphQL query or mutation."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        BUFFER_GRAPHQL_URL, data=payload,
        headers={"Authorization": f"Bearer {BUFFER_API_KEY}",
                 "Content-Type": "application/json",
                 "User-Agent": "BufferClient/1.0 (automation)",
                 "Accept": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        raise Exception(f"HTTP {e.code}: {body}")
    if "errors" in resp:
        raise Exception(f"GraphQL errors: {resp['errors']}")
    return resp.get("data", {})


def _buffer_get_instagram_channel():
    """Return (channel_id, org_id) for the Instagram channel in the Buffer account."""
    data = _buffer_graphql("{ account { organizations { id channels { id service name } } } }")
    for org in data.get("account", {}).get("organizations", []):
        for ch in org.get("channels", []):
            if "instagram" in ch.get("service", "").lower():
                return ch["id"], org["id"]
    return None, None


def _buffer_find_slot(channel_id):
    """Return (ISO string, unix ts) for next open 9am/1pm/6pm ET slot (max 3/day)."""
    import pytz
    from collections import defaultdict
    from datetime import datetime, timedelta
    ET = pytz.timezone("America/New_York")

    day_counts = defaultdict(int)
    try:
        q = """query($id:String!){channel(id:$id){posts(filter:{status:[scheduled,pending]}){edges{node{dueAt}}}}}"""
        data = _buffer_graphql(q, {"id": channel_id})
        for edge in data.get("channel", {}).get("posts", {}).get("edges", []):
            due = edge.get("node", {}).get("dueAt")
            if due:
                try:
                    dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                    day_counts[dt.astimezone(ET).strftime("%Y-%m-%d")] += 1
                except Exception:
                    pass
    except Exception as e:
        print(f"  [buffer] slot query error: {e} — using default slot")

    slot_hours = [9, 13, 18]
    now   = datetime.now(ET)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.hour >= 18:
        start += timedelta(days=1)

    for _ in range(60):
        day_str = start.strftime("%Y-%m-%d")
        count   = day_counts.get(day_str, 0)
        if count < 3:
            h  = slot_hours[min(count, 2)]
            dt = start.replace(hour=h, minute=0, second=0, microsecond=0)
            if dt > now:
                iso = dt.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                return iso, int(dt.timestamp())
        start += timedelta(days=1)
    return None, None


def _make_public_url(token, file_id):
    """Give anyone-reader permission to a Drive file and return public URL."""
    try:
        perm_url  = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions?supportsAllDrives=true"
        perm_body = json.dumps({"type": "anyone", "role": "reader"}).encode()
        req = urllib.request.Request(perm_url, data=perm_body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        urllib.request.urlopen(req).read()
    except Exception:
        pass
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _find_run_folder_for_slug(token, slug):
    """Find the latest vN_proof-<slug> folder under PROOF_POSTS_FOLDER_ID."""
    q   = (f"'{PROOF_POSTS_FOLDER_ID}' in parents and trashed=false "
           f"and mimeType='application/vnd.google-apps.folder' "
           f"and name contains 'proof-{slug}'")
    url = (f"https://www.googleapis.com/drive/v3/files"
           f"?q={urllib.parse.quote(q)}&supportsAllDrives=true"
           f"&includeItemsFromAllDrives=true&fields=files(id,name)&orderBy=name%20desc")
    req   = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    files = json.loads(urllib.request.urlopen(req).read()).get("files", [])
    if not files:
        return None, None
    return files[0]["id"], files[0]["name"]   # highest version (desc sorted)


def run_phase4(token, group_key):
    """
    Phase 4: Schedule approved proof-post to Buffer.
    Reads proof_*.png files from the latest run folder's png/ subfolder,
    makes them publicly readable, and posts to Buffer (next available slot).
    Also schedules a 30-day repeat (same Buffer convention as approval_handler.py).
    """
    if not BUFFER_API_KEY:
        print("❌ BUFFER_API_KEY_EXP04092027 not set — cannot schedule to Buffer")
        sys.exit(1)

    print(f"\n[proof-post Phase 4] Scheduling: {group_key}")

    parts   = (group_key + "|||").split("|", 3)
    project = parts[0].strip().title()
    service = parts[1].strip()
    room    = parts[2].strip()
    slug    = re.sub(r"[^a-z0-9]+", "-", group_key.lower()).strip("-")[:40]

    # Find run folder
    run_fid, run_name = _find_run_folder_for_slug(token, slug)
    if not run_fid:
        print(f"❌ No run folder found for slug '{slug}' — run Phase 2 first")
        sys.exit(1)
    print(f"  Run folder: {run_name} ({run_fid})")

    # Find png/ subfolder
    q    = f"'{run_fid}' in parents and name='png' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    url  = (f"https://www.googleapis.com/drive/v3/files"
            f"?q={urllib.parse.quote(q)}&supportsAllDrives=true&includeItemsFromAllDrives=true&fields=files(id)")
    req  = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    png_folders = json.loads(urllib.request.urlopen(req).read()).get("files", [])
    if not png_folders:
        print(f"❌ No png/ subfolder in {run_name}")
        sys.exit(1)
    png_fid = png_folders[0]["id"]

    # List proof_*.png files (sorted by name = slide order)
    q2   = f"'{png_fid}' in parents and name contains 'proof_' and trashed=false"
    url2 = (f"https://www.googleapis.com/drive/v3/files"
            f"?q={urllib.parse.quote(q2)}&supportsAllDrives=true&includeItemsFromAllDrives=true"
            f"&fields=files(id,name)&orderBy=name")
    req2 = urllib.request.Request(url2, headers={"Authorization": f"Bearer {token}"})
    proof_files = json.loads(urllib.request.urlopen(req2).read()).get("files", [])
    if not proof_files:
        print(f"❌ No proof_*.png files in png/ subfolder of {run_name}")
        sys.exit(1)
    print(f"  Found {len(proof_files)} proof slides to schedule")

    # Make all files public + collect URLs
    image_urls = [_make_public_url(token, f["id"]) for f in proof_files]
    print(f"  Made {len(image_urls)} files public")

    # Get Buffer Instagram channel (GraphQL)
    try:
        channel_id, org_id = _buffer_get_instagram_channel()
    except Exception as e:
        print(f"❌ Buffer channel fetch failed: {e}")
        sys.exit(1)
    if not channel_id:
        print("❌ No Instagram channel in Buffer account")
        sys.exit(1)
    print(f"  Buffer Instagram channel: {channel_id}")

    slot_iso, slot_ts = _buffer_find_slot(channel_id)

    # Build caption (OPC-safe: no promises, no stats, construction reality)
    caption = f"Progress update — {project} | {service} | {room} | Oak Park Construction #oakparkconstruction #construction #remodel"

    # Schedule via GraphQL createPost
    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id dueAt } }
        ... on MutationError { message }
      }
    }
    """
    post_input = {
        "text": caption,
        "channelId": channel_id,
        "assets": {"images": [{"url": u} for u in image_urls[:10]]},
    }
    if slot_iso:
        post_input["schedulingType"] = "customScheduled"
        post_input["mode"]           = "customScheduled"
        post_input["dueAt"]          = slot_iso
    else:
        post_input["schedulingType"] = "automatic"
        post_input["mode"]           = "addToQueue"

    try:
        resp = _buffer_graphql(mutation, {"input": post_input})
    except Exception as e:
        print(f"❌ Buffer post failed: {e}")
        sys.exit(1)

    result = resp.get("createPost", {})
    if "message" in result:
        print(f"❌ Buffer rejected: {result['message']}")
        sys.exit(1)

    post_id = result.get("post", {}).get("id", "?")
    if slot_iso and slot_ts:
        from datetime import datetime
        import pytz
        ET = pytz.timezone("America/New_York")
        slot_str = datetime.fromtimestamp(slot_ts, ET).strftime("%Y-%m-%d %H:%M ET")
        print(f"  ✅ Buffer scheduled: {len(image_urls)} slides → {slot_str} (id={post_id})")

        # 30-day repeat
        from datetime import timedelta
        repeat_dt  = datetime.fromtimestamp(slot_ts, pytz.UTC) + timedelta(days=30)
        repeat_iso = repeat_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        repeat_input = dict(post_input)
        repeat_input["dueAt"] = repeat_iso
        try:
            _buffer_graphql(mutation, {"input": repeat_input})
            print(f"  ✅ Buffer 30-day repeat → {repeat_dt.astimezone(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')}")
        except Exception as e:
            print(f"  ⚠️  30-day repeat failed (non-fatal): {e}")

    # Update candidate status → Scheduled
    _update_candidate_status(token, group_key, "Scheduled")

    print(f"\n✅ Phase 4 complete — {group_key} → Scheduled")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OPC Proof-Post Flow")
    parser.add_argument("--phase", type=int, default=1, choices=[1, 2, 3, 4],
                        help="Pipeline phase: 1=scan, 2=enhance+preview, 3=approve/reject, 4=schedule")
    parser.add_argument("--group-key", default=None,
                        help="Phase 2/3: group key to process (default: top above-gate candidate)")
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

    elif args.phase == 3:
        group_key = args.group_key or os.environ.get("PROOF_GROUP_KEY", "").strip()
        decision  = os.environ.get("PROOF_DECISION", "").strip()
        if not group_key:
            print("❌ PROOF_GROUP_KEY not set — required for Phase 3")
            sys.exit(1)
        if not decision:
            print("❌ PROOF_DECISION not set — must be APPROVE, REJECT, or SKIP")
            sys.exit(1)
        run_phase3(token, group_key, decision)

    elif args.phase == 4:
        group_key = args.group_key or os.environ.get("PROOF_GROUP_KEY", "").strip()
        if not group_key:
            # Auto-pick: find first Approved candidate
            rows   = _sheets_get(token, f"'{CANDIDATES_TAB}'!A:Q")
            header = rows[0] if rows else []
            col_st = next((i for i, h in enumerate(header) if h.strip() == "Status"), None)
            col_gk = next((i for i, h in enumerate(header) if "Group Key" in h), None)
            if col_st is not None and col_gk is not None:
                for row in rows[1:]:
                    padded = row + [""] * (max(col_st, col_gk) + 1 - len(row))
                    if padded[col_st].strip() == "Approved":
                        group_key = padded[col_gk].strip()
                        break
        if not group_key:
            print("❌ No Approved candidates found — run Phase 3 (APPROVE) first")
            sys.exit(0)
        print(f"[proof-post] Phase 4 target: {group_key}")
        run_phase4(token, group_key)


if __name__ == "__main__":
    main()
