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

import json, os, sys, time
from datetime import date
from pathlib import Path
import urllib.request, urllib.parse

# ── Constants ─────────────────────────────────────────────────────────────────

SHEET_ID       = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CATALOG_TAB    = "📸 Photo Catalog"
CANDIDATES_TAB = "📸 Proof Post Candidates"

TOKEN_FILE_PATH = os.environ.get("SHEETS_TOKEN_PATH", "")

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

    _sheets_append(token, CANDIDATES_TAB, rows)
    above = sum(1 for c in candidates if c["above_gate"])
    print(f"[proof-post] {len(candidates)} groups scored | {above} above confidence gate | written to {CANDIDATES_TAB}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN_FILE_PATH or not Path(TOKEN_FILE_PATH).exists():
        print("❌ SHEETS_TOKEN_PATH not set or missing")
        sys.exit(1)

    print(f"\n🏗️  OPC Proof-Post Candidate Scan — {date.today()}")
    token = _get_token()

    _ensure_candidates_tab(token)

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


if __name__ == "__main__":
    main()
