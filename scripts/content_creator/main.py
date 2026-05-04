#!/usr/bin/env python3
"""
main.py — Content Creator Pipeline Orchestrator
Runs at 2:30 AM ET via GitHub Actions.

Flow:
  1. Pick 3 topics (2 OPC + 1 Brazil) from Inspiration Library
  2. Generate carousel content via Claude Haiku
  3. Render 15 PNGs per topic (3 variants × 5 slides) + 3 motion covers
  4. Upload to Drive (Marketing > OPC > Templates for OPC, News drive for Brazil)
  5. Email preview to Priscila
  6. Update catalog status to pending_approval

4AM agent (1.5 hrs later) checks for:
  - Pipeline failures → retries or creates issue
  - Email replies → processes approvals/changes
"""
import json, os, sys, time, subprocess, shutil
from datetime import datetime
from pathlib import Path
import pytz

ET = pytz.timezone("America/New_York")

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # for routing.py
from topic_picker import pick_topics, get_clip_count_for_topic
from carousel_builder import generate_carousel_content, build_html, render_pngs, generate_image_suggestions, visual_audit, fetch_all_media, fetch_clips, build_motion_html, generate_caption
from routing import get_route
import urllib.request, urllib.parse
from email_preview import send_preview, update_catalog_status

WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/content_creator_run"))
EXPORT_SCRIPT = os.environ.get("EXPORT_SCRIPT", str(Path(__file__).parent / "export_variants.js"))

# Carousel destination per niche is sourced from routing.py: get_route(niche)["carousel_folder_id"].
# New spec (confirmed 2026-04-20):
#   OPC    → Marketing/Content/carousel/v<N>_<slug>/{png,motion,resources}   (16P2JN74J...)
#   Brazil → News/Brazil/Carousel/v<N>_<slug>/{png,motion,resources}        (1gDOjtW...)
#   USA    → News/USA/Carousel/v<N>_<slug>/{png,motion,resources}           (1lRfZE5X...)
# No more _TEMPLATE_CAROUSEL middle folder. No more <series>/ middle folder.
# Series identity lives in slug naming (v1_<name>_<seriestopic>) + the story Google Doc.
# N auto-increments when a slug already has versions. See project_carousel_folder_standard.md.
VERIFICAMOS_CONFIDENCE_THRESHOLD = 0.70  # auto-build gate — items below this score go to manual review queue

# Shortcuts folders — flat index of all built content per niche.
# Carousel shortcut = version folder (v1_slug). Video shortcut = motion subfolder.
SHORTCUT_FOLDERS = {
    "opc":     {"carousels": "13pqneqeDy1-LAtGsRJDg9gmNl07Ye41g", "videos": "1LKS51EfDxrR3ib6TsR2DADMpt3der36D"},
    "brazil":  {"carousels": "1texYwliSc2eJjjVxSmY3bfV-f39USbJg", "videos": "1d5lJi5exZK_vhNVB6MWyjdFotMBBgPVd"},
    "usa":     {"carousels": "1jPB6TjbV8Bu2k3zeN3uT7EIvspwIrWtQ", "videos": "126K6N9UDOFj_zS-h3e4dD30GwZOviugT"},
}

SHEET_ID    = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
INSPO_TAB   = "📥 Inspiration Library"
QUEUE_TAB   = "📋 Content Queue"
CATALOG_TAB = "📸 Project Content Catalog"

ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "priscila@oakpark-construction.com")
TEMPLATE_ROTATION_MODE = os.environ.get("TEMPLATE_ROTATION_MODE", "weekday").strip().lower()
TEMPLATE_ROTATION_ENABLED = os.environ.get("TEMPLATE_ROTATION_ENABLED", "1").strip().lower() not in ("0", "false", "no")
MANUAL_MODE = os.environ.get("MANUAL_MODE", "0").strip().lower() in ("1", "true", "yes")
MANUAL_TOPIC = os.environ.get("MANUAL_TOPIC", "").strip()
if MANUAL_TOPIC:
    MANUAL_MODE = True  # single-topic run — never processes the full queue
MANUAL_NICHE = os.environ.get("MANUAL_NICHE", "").strip().lower()
MANUAL_TEMPLATE = os.environ.get("MANUAL_TEMPLATE", "auto").strip().lower()
MANUAL_TEMPLATE_SET = os.environ.get("MANUAL_TEMPLATE_SET", "").strip().lower()
MANUAL_BRIEF = os.environ.get("MANUAL_BRIEF", "").strip()  # Google Docs URL or plain text brief
# When set, all Drive uploads go to this test folder instead of normal series destinations.
TEST_OUTPUT_FOLDER = os.environ.get("TEST_OUTPUT_FOLDER", "").strip()


def _send_alert(msg: str):
    """Fail-loud email alert when pipeline hits a crash path or produces zero output.
    Uses gh CLI to trigger send_email.yml (uses PRI_OP_GMAIL_APP_PASSWORD)."""
    try:
        print(f"\n🔴 ALERT: {msg}")
        subject = f"[content_creator] Pipeline alert — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}"
        body = f"Pipeline hit a failure:\n\n{msg}\n\nCheck logs: https://github.com/priihigashi/oak-park-ai-hub/actions/workflows/content_creator.yml"
        subprocess.run(
            ["gh", "workflow", "run", "send_email.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", f"to={ALERT_EMAIL}",
             "-f", f"subject={subject}",
             "-f", f"body={body}"],
            check=False, timeout=30,
        )
    except Exception as e:
        print(f"  (alert send itself failed: {e})")


CLIP_THRESHOLD = 8  # Clip Collections count required before building a clips_needed topic


def _trigger_video_research(topic: str, niche: str):
    """Dispatch video-research.yml for a topic that needs clips before building."""
    try:
        # Build niche-appropriate search queries
        if niche in ("brazil", "brasil"):
            queries = f"{topic} brasil,{topic} congresso nacional,{topic} política brasileira"
        elif niche == "usa":
            queries = f"{topic} congress,{topic} united states,{topic} breaking news"
        else:
            queries = f"{topic},Oak Park Construction {topic}"
        subprocess.run(
            ["gh", "workflow", "run", "video-research.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", f"topic={topic[:80]}",
             "-f", f"queries={queries[:300]}",
             "-f", "max_per_query=5",
             "-f", f"niche={niche}"],
            check=False, timeout=30,
        )
        print(f"  video-research.yml triggered for clips_needed topic: {topic[:50]}")
    except Exception as e:
        print(f"  _trigger_video_research failed (non-fatal): {e}")


_token_cache = {}

def get_oauth_token():
    if _token_cache.get("t") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["t"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["t"] = resp["access_token"]
    _token_cache["exp"] = time.time() + resp.get("expires_in", 3500) - 60
    return resp["access_token"]


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _weekday_template(day_idx):
    """Mon(0)→Sun(6) mapping for OPC template rotation.
    Keeps current "tip" frequently, introduces "illustrated" on set days."""
    weekday_map = {
        0: "illustrated",  # Monday
        1: "tip",          # Tuesday
        2: "cutout",       # Wednesday
        3: "tip",          # Thursday
        4: "illustrated",  # Friday
        5: "progress",     # Saturday
        6: "tip",          # Sunday
    }
    return weekday_map.get(day_idx, "tip")


def _resolve_opc_template(topic_entry, topic, run_date):
    """Choose OPC template route with deterministic rotation.
    Priority: explicit sheet override > rotation mode > default tip."""
    explicit = (topic_entry.get("template_key") or "").strip().lower()
    if explicit in ("tip", "progress", "illustrated", "cutout"):
        return explicit

    if not TEMPLATE_ROTATION_ENABLED:
        return "tip"

    mode = TEMPLATE_ROTATION_MODE
    if mode == "alternate":
        parity_seed = f"{run_date}|{topic or ''}"
        return "illustrated" if (sum(ord(c) for c in parity_seed) % 2 == 0) else "cutout"
    if mode == "weekday":
        day_idx = datetime.now(ET).weekday()
        return _weekday_template(day_idx)
    return "tip"


def _resolve_news_template(topic_entry, niche):
    """Brazil/USA template routing.
    Priority:
    1) explicit template_key in sheet
    2) default native motion-first style
    3) every 3rd day, use shared template (illustrated/cutout alternating)
    """
    explicit = (topic_entry.get("template_key") or "").strip().lower()
    if explicit in ("native", "illustrated", "cutout"):
        return None if explicit == "native" else explicit
    if not TEMPLATE_ROTATION_ENABLED:
        return None
    if TEMPLATE_ROTATION_MODE != "weekday":
        return None
    now = datetime.now(ET)
    day_idx = now.weekday()
    # Even/odd ISO week number alternates template assignments so both
    # illustrated and cutout rotate through each weekday slot over 2 weeks.
    week_parity = now.isocalendar()[1] % 2  # 0 = even week, 1 = odd week
    # Brazil: native motion (Rachadinha-style) is the priority.
    # Shared template fires on Wed + Sat only (2 consecutive native days before each).
    # Even week: Wed=illustrated, Sat=cutout
    # Odd  week: Wed=cutout,      Sat=illustrated
    if niche == "brazil":
        if day_idx == 2:   # Wednesday
            return "illustrated" if week_parity == 0 else "cutout"
        if day_idx == 5:   # Saturday
            return "cutout" if week_parity == 0 else "illustrated"
        return None
    # USA: same alternating logic on Tue + Fri
    if niche == "usa":
        if day_idx == 1:   # Tuesday
            return "illustrated" if week_parity == 0 else "cutout"
        if day_idx == 4:   # Friday
            return "cutout" if week_parity == 0 else "illustrated"
        return None
    return None


def _normalize_niche(raw_niche, fmt=""):
    """Normalize source/format labels to opc|brazil|usa."""
    n = (raw_niche or "").strip().lower()
    f = (fmt or "").strip().lower()
    if n in ("opc", "oak park", "oak park construction", "content"):
        return "opc"
    if n in ("usa", "us", "united states", "news-usa", "news-us", "america"):
        return "usa"
    if n in ("brazil", "brasil", "news", "news-brazil", "sovereign"):
        return "brazil"
    # Legacy fallback when Source is blank/noisy
    if "usa" in f or "the chain" in f:
        return "usa"
    if "brazil" in f or "quem" in f or "verificamos" in f:
        return "brazil"
    return "opc"


def _col_letter(n):
    r = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        r = chr(65 + rem) + r
    return r


def _get_header_map(tab_name):
    token = get_oauth_token()
    rows = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{urllib.parse.quote(tab_name + '!1:1', safe='!:')}",
            headers={"Authorization": f"Bearer {token}"})).read()).get("values", [[]])[0]
    return {h.strip().lower(): i for i, h in enumerate(rows)}


def write_inspo_status(row_idx, status):
    """Write flow-tracking status back to Inspiration Library row."""
    token = get_oauth_token()
    now = datetime.now(ET).strftime("%Y-%m-%d")
    hmap = _get_header_map(INSPO_TAB)

    updates = []
    if "status" in hmap:
        updates.append({"range": f"'{INSPO_TAB}'!{_col_letter(hmap['status']+1)}{row_idx}", "values": [[status]]})
    if "date status changed" in hmap:
        updates.append({"range": f"'{INSPO_TAB}'!{_col_letter(hmap['date status changed']+1)}{row_idx}", "values": [[now]]})
    if not updates:
        return
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": updates}).encode()
    req = urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
        data=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"  Inspiration status write failed: {e}")


def write_queue_status(row_idx, status=None, drive_folder_path=None, extra=None):
    """Update Content Queue row. Always writes Date Status Changed when Status is written."""
    token = get_oauth_token()
    now = datetime.now(ET).strftime("%Y-%m-%d")
    hmap = _get_header_map(QUEUE_TAB)

    updates = []
    if status is not None and "status" in hmap:
        updates.append({"range": f"'{QUEUE_TAB}'!{_col_letter(hmap['status']+1)}{row_idx}", "values": [[status]]})
        if "date status changed" in hmap:
            updates.append({"range": f"'{QUEUE_TAB}'!{_col_letter(hmap['date status changed']+1)}{row_idx}", "values": [[now]]})
    if drive_folder_path is not None and "drive folder path" in hmap:
        updates.append({"range": f"'{QUEUE_TAB}'!{_col_letter(hmap['drive folder path']+1)}{row_idx}", "values": [[drive_folder_path]]})
    for k, v in (extra or {}).items():
        if k.lower() in hmap:
            updates.append({"range": f"'{QUEUE_TAB}'!{_col_letter(hmap[k.lower()]+1)}{row_idx}", "values": [[v]]})
    if not updates:
        return
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": updates}).encode()
    req = urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
        data=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"  Queue status write failed: {e}")


def get_approved_queue_rows():
    """Read Content Queue rows with Status='Approved' AND Content Type='Carousel' AND no Drive path yet."""
    token = get_oauth_token()
    tab_enc = urllib.parse.quote(f"'{QUEUE_TAB}'", safe="!:'")
    rows = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{tab_enc}",
            headers={"Authorization": f"Bearer {token}"})).read()).get("values", [])
    if len(rows) < 2:
        return []
    hmap = {h.strip().lower(): i for i, h in enumerate(rows[0])}

    def v(row, name):
        idx = hmap.get(name.lower())
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    approved = []
    for idx, row in enumerate(rows[1:], start=2):
        if v(row, "status").lower() != "approved":
            continue
        if v(row, "content type").lower() != "carousel":
            continue
        if v(row, "drive folder path"):
            continue  # already built
        niche = _normalize_niche(v(row, "source"), v(row, "format"))
        approved.append({
            "queue_row_idx": idx,
            "topic": v(row, "project name"),
            "niche": niche,
            "brief": v(row, "brief / angle"),
            "url": v(row, "inspo url"),
            "format": v(row, "format"),
            "series_override": v(row, "series_override"),
            "fake_news_route": v(row, "fake_news_route") or "B",
            "fake_news_confidence": _safe_float(v(row, "fake_news_confidence"), 0.0),
            "clips_needed": v(row, "clips_needed"),
        })
    return approved


def _caption_fallback(topic, niche):
    """Deterministic caption fallback so scheduling is never blocked by an empty LLM response."""
    if niche == "brazil":
        return {
            "caption": f"Entenda o contexto por trás de {topic}. Os detalhes estão no carrossel.",
            "in_post_hashtags": "#InBrasil #politica #brasil #noticias #contexto",
            "first_comment_hashtags": "#checagem #jornalismo #congresso #stf #governo",
        }
    if niche == "usa":
        return {
            "caption": f"Here is the context behind {topic}. Swipe through for the breakdown.",
            "in_post_hashtags": "#InUS #politics #news #factcheck #context",
            "first_comment_hashtags": "#government #congress #policy #accountability #explainer",
        }
    return {
        "caption": f"Quick homeowner breakdown: {topic}. Save this before your next project conversation.",
        "in_post_hashtags": "#OakParkConstruction #remodeling #construction #homeimprovement #contractor",
        "first_comment_hashtags": "#floridacontractor #renovationtips #homerenovation #contractortips #buildsmart",
    }


def _fallback_source_handle(niche):
    """Brand-safe footer fallback; never let placeholder handles reach rendered HTML."""
    if niche == "brazil":
        return "@InBrasil"
    if niche == "usa":
        return "@InUS"
    return "@oakparkconstruction"


def _default_context_query(slide, topic, niche):
    """Safe fallback query for context-image slots."""
    h_pt = (slide.get("heading_pt") or "").strip()
    h_en = (slide.get("heading_en") or "").strip()
    base = h_pt or h_en or topic
    suffix = "Brasil política" if niche == "brazil" else "US politics"
    return f"{base} {suffix}".strip()


def _enforce_news_visual_targets(content, topic, niche):
    """Guarantee at least 3 middle slides can render real context images."""
    if not isinstance(content, dict):
        return content
    slides = content.get("slides", [])
    if not isinstance(slides, list) or not slides:
        return content

    # Fill missing query where visual_hint is already context-image
    for slide in slides:
        if slide.get("visual_hint") == "context-image" and not (slide.get("context_image_query") or "").strip():
            slide["context_image_query"] = _default_context_query(slide, topic, niche)

    def _ready(s):
        return s.get("visual_hint") == "context-image" and bool((s.get("context_image_query") or "").strip())

    ready = sum(1 for s in slides if _ready(s))
    needed = max(0, 3 - ready)
    if needed <= 0:
        return content

    # Prefer non-profile slides first
    for slide in slides:
        if needed <= 0:
            break
        if _ready(slide):
            continue
        if slide.get("type") == "profile":
            continue
        slide["visual_hint"] = "context-image"
        if not (slide.get("context_image_query") or "").strip():
            slide["context_image_query"] = _default_context_query(slide, topic, niche)
        needed -= 1

    # Last resort: allow profile slides too
    if needed > 0:
        for slide in slides:
            if needed <= 0:
                break
            if _ready(slide):
                continue
            slide["visual_hint"] = "context-image"
            slide["context_image_query"] = _default_context_query(slide, topic, niche)
            needed -= 1

    return content


def get_drive_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    raw = os.environ.get("SHEETS_TOKEN", "")
    td = json.loads(raw)
    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())

    creds = Credentials(
        token=resp["access_token"], refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"], client_secret=td["client_secret"],
    )
    return build("drive", "v3", credentials=creds)


def next_version_number(parent_folder_id, slug, drive):
    """List folders under parent matching v<N>_<slug>, return next available N."""
    resp = drive.files().list(
        q=f"'{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        corpora="allDrives",
    ).execute()
    import re
    pattern = re.compile(rf"^v(\d+)_{re.escape(slug)}$")
    max_n = 0
    for f in resp.get("files", []):
        m = pattern.match(f["name"])
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _mime_for(suffix):
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".mp4": "video/mp4", ".html": "text/html",
        ".json": "application/json",
    }.get(suffix.lower(), "application/octet-stream")


def create_subfolder(parent_id, name, drive):
    folder = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        supportsAllDrives=True, fields="id",
    ).execute()
    return folder["id"]


def add_shortcut(target_id, name, dest_folder_id, drive):
    """Create a Drive shortcut in dest_folder_id pointing to target_id. Silent on failure."""
    try:
        drive.files().create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.shortcut",
                "shortcutDetails": {"targetId": target_id},
                "parents": [dest_folder_id],
            },
            supportsAllDrives=True, fields="id",
        ).execute()
    except Exception as e:
        print(f"  Shortcut creation skipped ({name}): {e}")


def upload_single_file(local_path, parent_id, name, mime, drive):
    from googleapiclient.http import MediaFileUpload
    drive.files().create(
        body={"name": name, "parents": [parent_id]},
        media_body=MediaFileUpload(str(local_path), mimetype=mime),
        supportsAllDrives=True, fields="id",
    ).execute()


def upload_dir_contents(local_dir, parent_id, drive, skip_pattern=None):
    import re as _re
    skip_re = _re.compile(skip_pattern) if skip_pattern else None
    for f in sorted(Path(local_dir).iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if skip_re and skip_re.search(f.name):
            continue
        upload_single_file(str(f), parent_id, f.name, _mime_for(f.suffix), drive)


def create_story_doc(parent_folder_id, slug, version, topic, niche, brief, content, drive, drive_link, series_override=""):
    """Create the per-post story Google Doc inside the version folder.
    Format matches EP001 Rachadinha editorial log: header block, HOW TO USE, slide-by-slide, NOTES.
    Feedback rule: every review appends a new 'NOTE — YYYY-MM-DD' block at the bottom.
    series_override: pass "Verificamos", "Fact-Checked", etc. to override niche-default series name.
    """
    from googleapiclient.http import MediaInMemoryUpload
    series = series_override or ("Tip of the Week" if niche == "opc" else ("The Chain" if niche == "usa" else "Quem Decidiu Isso?"))
    title = f"v{version} — {slug} — {topic[:80]}"

    lines = [
        title,
        "",
        f"Series: {series} | Niche: {niche.upper()}",
        f"Version: v{version}",
        "Status: DRAFT — awaiting review",
        f"Drive: {drive_link}",
        "",
        "─" * 40,
        "",
        "HOW TO USE THIS DOC",
        "Every time Priscila gives feedback on this post in any chat session, append a new NOTE at the",
        "bottom with the date. Before touching this carousel, read this doc first.",
        "",
        "─" * 40,
        "",
        "BRIEF / RESEARCH",
        brief or "(no brief captured — fill in from Inspiration Library row)",
        "",
        "SLIDE-BY-SLIDE SCRIPT",
    ]
    slides = content.get("slides", []) if isinstance(content, dict) else []
    for i, s in enumerate(slides, start=1):
        lines.append("")
        lines.append(f"Slide {i}")
        if isinstance(s, dict):
            for k, v in s.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {s}")
    lines += [
        "",
        "─" * 40,
        "",
        "VISUAL AUDIT (auto-generated at build time)",
    ]
    _, _, audit_txt = visual_audit(content, niche)
    lines.append(audit_txt)

    lines += [
        "",
        "─" * 40,
        "",
        "NOTES",
        "(append each review below as: NOTE — YYYY-MM-DD, then the feedback)",
    ]
    body = "\n".join(lines)

    doc = drive.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [parent_folder_id],
        },
        media_body=MediaInMemoryUpload(body.encode("utf-8"), mimetype="text/plain"),
        supportsAllDrives=True, fields="id,webViewLink",
    ).execute()
    return doc


def record_motion_slides(clip_html_files, output_dir, duration=5):
    """Record each per-slide motion HTML via Playwright → MP4 + GIF.
    clip_html_files: list of (slide_idx, html_path) from build_motion_html().
    Saves canonical names so Ken Burns won't overwrite:
      slide_idx == 1 (cover): black_01_cover_motion.mp4 + .gif
      slide_idx >= 2 (middle): black_<NN>_slide_<N>_motion.mp4 + .gif
    Returns list of (slide_idx, mp4_path) — main loop skips these indices when running Ken Burns.
    Falls back silently per slot; Ken Burns is safety net for every other slide.
    """
    os.makedirs(output_dir, exist_ok=True)
    record_script = Path(__file__).parent / "record_motion.js"
    if not record_script.exists():
        print("  record_motion.js not found — skipping Playwright motion recording")
        return []

    recorded = []
    for slide_idx, html_path in clip_html_files:
        nn = f"{slide_idx:02d}"
        base = "cover" if slide_idx == 1 else f"slide_{slide_idx}"
        webm_path = Path(output_dir) / f"black_{nn}_{base}_motion.webm"
        mp4_path  = Path(output_dir) / f"black_{nn}_{base}_motion.mp4"
        gif_path  = Path(output_dir) / f"black_{nn}_{base}_motion.gif"
        try:
            r = subprocess.run(
                ["node", str(record_script), html_path, str(webm_path), str(duration)],
                capture_output=True, timeout=120
            )
            if r.returncode != 0 or not webm_path.exists():
                stderr_txt = r.stderr.decode("utf-8", errors="replace")[:300] if r.stderr else ""
                print(f"  Playwright recording failed for slide {slide_idx}: {stderr_txt}")
                continue
            # Convert webm → mp4
            subprocess.run([
                "ffmpeg", "-y", "-i", str(webm_path),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
                str(mp4_path)
            ], capture_output=True, timeout=60)
            # Convert mp4 → gif
            subprocess.run([
                "ffmpeg", "-y", "-i", str(mp4_path),
                "-vf", "fps=15,scale=540:-1:flags=lanczos", str(gif_path)
            ], capture_output=True, timeout=60)
            # Clean up webm
            webm_path.unlink(missing_ok=True)
            if mp4_path.exists():
                print(f"  Motion recorded: {mp4_path.name} ({mp4_path.stat().st_size//1024}KB)")
                recorded.append((slide_idx, mp4_path))
        except Exception as e:
            print(f"  record_motion_slides slide {slide_idx} error: {e}")
    return recorded


def render_motion_remotion(cover_png_path, clip_path, output_dir, variant,
                            hook_text="", slide_idx=1):
    """Renderer tier #1 — Remotion programmatic render of CarouselMotion composition.

    Produces <variant>_<NN>_<base>_motion.mp4 (+ gif) at 1080x1350, 5s loop.
    Uses the CarouselMotion composition registered in scripts/remotion/src/Root.tsx.

    Falls back silently (returns None) when:
      • Remotion project missing / node_modules not installed
      • npx remotion render exits non-zero
      • Output file never appears

    Philosophy: this is the preferred cover renderer. If it fails, main.py
    falls through to Playwright, then to Ken Burns (ffmpeg zoompan on PNG).
    """
    remotion_root = Path(__file__).resolve().parents[1] / "remotion"
    if not (remotion_root / "package.json").exists():
        return None
    if not (remotion_root / "node_modules").exists():
        print("  Remotion: node_modules missing — skipping (Playwright/Ken Burns will handle)")
        return None

    os.makedirs(output_dir, exist_ok=True)
    base = Path(cover_png_path).stem
    if base.endswith("_html"):
        base = base[:-5]
    nn = f"{slide_idx:02d}"
    # Strip variant_nn_ prefix if already embedded in the filename (avoids "black_01_black_01_cover")
    _prefix = f"{variant}_{nn}_"
    if base.startswith(_prefix):
        base = base[len(_prefix):]
    mp4_path = Path(output_dir) / f"{variant}_{nn}_{base}_motion.mp4"
    gif_path = Path(output_dir) / f"{variant}_{nn}_{base}_motion.gif"

    # Remotion serves assets from its own public/ dir via localhost:3000.
    # Absolute /tmp paths are 404 — copy assets in, pass relative paths.
    public_dir = remotion_root / "public"
    public_dir.mkdir(exist_ok=True)
    run_id = f"{variant}_{nn}"
    staged_png = public_dir / f"{run_id}_poster.png"
    staged_clip = public_dir / f"{run_id}_clip.mp4" if clip_path and Path(clip_path).exists() else None
    shutil.copy2(cover_png_path, staged_png)
    if staged_clip:
        shutil.copy2(clip_path, staged_clip)

    props = {
        "posterPng": staged_png.name,           # relative — served by Remotion dev server
        "clipSrc": staged_clip.name if staged_clip else None,
        "hookText": hook_text or "",
        "accentColor": "#F4C430",
    }
    props_file = Path(output_dir) / f".remotion_props_{variant}_{nn}.json"
    props_file.write_text(json.dumps(props))

    try:
        r = subprocess.run(
            ["npx", "remotion", "render",
             "src/Root.tsx", "CarouselMotion",
             str(mp4_path.resolve()),
             f"--props={props_file.resolve()}",
             "--codec=h264",
             "--log=error"],
            cwd=str(remotion_root),
            capture_output=True, timeout=180
        )
        props_file.unlink(missing_ok=True)
        staged_png.unlink(missing_ok=True)
        if staged_clip:
            staged_clip.unlink(missing_ok=True)
        if r.returncode != 0 or not mp4_path.exists():
            err = r.stderr.decode("utf-8", errors="replace")[:300] if r.stderr else ""
            print(f"  Remotion render miss ({base}): {err}")
            return None

        # Also emit the GIF for feed preview
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp4_path),
             "-vf", "fps=15,scale=540:-1:flags=lanczos", str(gif_path)],
            capture_output=True, timeout=60
        )
        # Preview frame (only cover slide)
        if slide_idx == 1:
            preview_path = Path(output_dir) / f"{variant}_preview_frame.jpg"
            shutil.copy2(cover_png_path, preview_path)
        print(f"  Remotion → {mp4_path.name} ({mp4_path.stat().st_size//1024}KB)")
        return str(mp4_path)
    except Exception as e:
        props_file.unlink(missing_ok=True)
        staged_png.unlink(missing_ok=True)
        if staged_clip:
            staged_clip.unlink(missing_ok=True)
        print(f"  Remotion exception ({base}): {e}")
        return None


def render_motion_cover(cover_png_path, output_dir, variant):
    """Ken Burns ffmpeg zoom on ONE static PNG → MP4 + GIF.
    Output MP4 inherits PNG name so every slide gets its own motion file:
      black_01_cover_html.png → black_01_cover_motion.mp4
      black_02_why_html.png   → black_02_why_motion.mp4
    Preview frame (used by email) is written only for slide 01 (cover).
    """
    os.makedirs(output_dir, exist_ok=True)
    # Strip _html suffix and replace with _motion to keep naming 1:1 with PNGs
    base = Path(cover_png_path).stem
    if base.endswith("_html"):
        base = base[:-5]
    mp4_path = os.path.join(output_dir, f"{base}_motion.mp4")
    gif_path = os.path.join(output_dir, f"{base}_motion.gif")

    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", cover_png_path,
        "-vf", "scale=2160:2700,zoompan=z='min(zoom+0.0005,1.06)':d=120:s=1080x1350:fps=30,format=yuv420p",
        "-t", "4", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18", mp4_path,
    ], capture_output=True, timeout=120)

    subprocess.run([
        "ffmpeg", "-y", "-i", mp4_path,
        "-vf", "fps=15,scale=540:-1:flags=lanczos", gif_path,
    ], capture_output=True, timeout=60)

    # Only write preview frame for cover (slide 01) — email_preview.py expects one per variant
    if "_01_" in base:
        preview_path = os.path.join(output_dir, f"{variant}_preview_frame.jpg")
        shutil.copy2(cover_png_path, preview_path)
    return mp4_path


def add_catalog_row(post_id, niche, series, topic, static_link, motion_link, token):
    import urllib.request, urllib.parse
    now = datetime.now(ET).strftime("%Y-%m-%d")
    row = [[
        post_id, niche, "", series, "", "",
        now, "", static_link, motion_link, "",
        "", "pending_approval", topic[:100], "N",
    ]]
    enc = urllib.parse.quote(f"'{CATALOG_TAB}'!A:O", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    payload = json.dumps({"values": row}).encode()
    req = urllib.request.Request(url, data=payload,
                                headers={"Authorization": f"Bearer {token}",
                                         "Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print(f"  Catalog row added: {post_id}")


def _animate_cover_kling(png_path, prompt, output_dir, variant):
    """Animate cover PNG via Kling AI image-to-video (direct API).

    Primary: KLING_API_KEY → api.klingai.com/v1/videos/image2video
      Key format 'accessKey:secretKey' → JWT generated per-request.
      Key without ':' → used as pre-generated Bearer token.
    Falls back silently to Ken Burns if key missing or API fails.
    """
    import base64, hashlib, hmac as _hmac

    kling_key = os.environ.get("KLING_API_KEY", "").strip()
    if not kling_key:
        print("  Kling: KLING_API_KEY not set — skipping animated cover")
        return None

    try:
        with open(png_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        print(f"  Kling: failed to read PNG ({e}) — skipping")
        return None

    clean_prompt = (prompt or "Subtle cinematic camera movement, documentary style").strip()[:500]
    os.makedirs(output_dir, exist_ok=True)
    kling_mp4 = os.path.join(output_dir, f"{variant}_01_cover_kling.mp4")

    def _make_token(raw_key):
        """Return Bearer token: generate JWT if key is 'accessKey:secretKey', else pass through."""
        if ":" not in raw_key:
            return raw_key
        access_key, secret_key = raw_key.split(":", 1)
        now = int(time.time())
        h = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        p = base64.urlsafe_b64encode(json.dumps({"iss": access_key, "exp": now + 1800, "nbf": now - 5}).encode()).rstrip(b"=").decode()
        msg = f"{h}.{p}"
        sig = _hmac.new(secret_key.encode(), msg.encode(), hashlib.sha256).digest()
        return f"{msg}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"

    try:
        import urllib.error as _uerr
        token = _make_token(kling_key)
        payload = json.dumps({
            "model_name": "kling-v1",
            "image": img_b64,
            "prompt": clean_prompt,
            "duration": "5",
            "aspect_ratio": "9:16",
            "mode": "std",
        }).encode()
        req = urllib.request.Request(
            "https://api.klingai.com/v1/videos/image2video",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except _uerr.HTTPError as _he:
            body = _he.read(500).decode("utf-8", errors="replace")
            print(f"  Kling: HTTP {_he.code} from API — body: {body[:300]}")
            return None
        task_id = (resp.get("data") or {}).get("task_id") or resp.get("task_id")
        if not task_id:
            print(f"  Kling: no task_id in response ({str(resp)[:200]}) — skipping")
            return None

        # Poll until done (max ~3 min)
        for _ in range(36):
            time.sleep(5)
            poll_req = urllib.request.Request(
                f"https://api.klingai.com/v1/videos/image2video/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            poll = json.loads(urllib.request.urlopen(poll_req, timeout=15).read())
            status = (poll.get("data") or {}).get("task_status") or poll.get("task_status", "")
            if status.lower() in ("succeed", "success"):
                videos = ((poll.get("data") or {}).get("task_result") or {}).get("videos") or []
                video_url = videos[0].get("url", "") if videos else ""
                if not video_url:
                    print(f"  Kling: succeeded but no video URL — keeping Ken Burns")
                    return None
                with urllib.request.urlopen(video_url, timeout=60) as r:
                    kling_data = r.read()
                with open(kling_mp4, "wb") as f:
                    f.write(kling_data)
                print(f"  Kling animated cover: {variant}_01_cover_kling.mp4 ({len(kling_data)//1024}KB)")
                return kling_mp4
            if status.lower() in ("failed", "error"):
                print(f"  Kling: task {task_id} failed (status={status}) — keeping Ken Burns")
                return None

        print(f"  Kling: timed out on task {task_id} — keeping Ken Burns")
        return None
    except Exception as e:
        print(f"  Kling animation failed (non-fatal): {e}")
        return None


def _check_media_presence(png_dir, motion_dir, resources_dir, post_id):
    """Verify media completeness locally before shipping.
    Returns (ok: bool, issues: list[str]).
    Non-blocking — caller sends alert but post still ships.
    """
    issues = []
    pngs = list(Path(png_dir).glob("*.png")) if Path(png_dir).exists() else []
    if len(pngs) < 3:
        issues.append(f"PNG count low: {len(pngs)} (expected ≥ 3 slides × 1 variant min)")

    mp4s = list(Path(motion_dir).glob("*.mp4")) if Path(motion_dir).exists() else []
    if not mp4s:
        issues.append("Motion folder has no MP4s — motion delivery will fail")

    images_dir = Path(resources_dir) / "images"
    images = [f for f in images_dir.iterdir() if f.is_file()] if images_dir.exists() else []
    if not images:
        issues.append("resources/images/ empty — no context/cover images fetched (placeholder text will show)")

    return len(issues) == 0, issues


def process_one_topic(topic_entry, run_date, drive):
    topic = topic_entry["topic"]
    niche = topic_entry["niche"]
    series_override = topic_entry.get("series_override", "")
    fake_news_route = topic_entry.get("fake_news_route", "B")
    fake_news_confidence = topic_entry.get("fake_news_confidence", 0.0)
    queue_row = topic_entry.get("queue_row_idx")

    # Confidence gate — Verificamos items below threshold go to manual review, never auto-build
    if series_override == "VERIFICAMOS" and fake_news_confidence < VERIFICAMOS_CONFIDENCE_THRESHOLD:
        print(f"  SKIPPED: Verificamos confidence {fake_news_confidence:.2f} < {VERIFICAMOS_CONFIDENCE_THRESHOLD} — needs manual review")
        if queue_row:
            write_queue_status(queue_row, status="Needs Review",
                               extra={"notes": f"fake_news_confidence={fake_news_confidence:.2f} below threshold"})
        _send_alert(
            f"Verificamos item held for manual review:\n'{topic[:60]}'\n"
            f"Confidence: {fake_news_confidence:.2f} (threshold: {VERIFICAMOS_CONFIDENCE_THRESHOLD})\n"
            f"Route: {fake_news_route}\nFlip Status to Approved in Content Queue to force-build."
        )
        return None

    slug = topic[:40].lower().replace(" ", "-").replace("'", "").replace('"', '')
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    if niche == "opc":
        _opc_type = _resolve_opc_template(topic_entry, topic, run_date)
        post_id = f"opc-{_opc_type}-{run_date}-{slug[:20]}"
    elif niche == "usa":
        _tmpl = (topic_entry.get("template_key") or "native").lower()
        post_id = f"usa-{_tmpl}-{run_date}-{slug[:20]}"
    elif series_override == "VERIFICAMOS":
        post_id = f"verificamos-{run_date}-{slug[:20]}"
    else:
        _tmpl = (topic_entry.get("template_key") or "native").lower()
        post_id = f"brazil-{_tmpl}-{run_date}-{slug[:20]}"

    print(f"\n{'='*60}")
    print(f"Processing: [{niche}] {topic}")
    print(f"Post ID: {post_id} | Queue row: {queue_row}")

    # Pre-build clip gate — if clips_needed flag set in Inspiration Library
    # and Clip Collections count < threshold, defer build and trigger research.
    clips_needed = str(topic_entry.get("clips_needed", "")).strip().lower() in ("true", "yes", "1", "x")
    if clips_needed:
        clip_count = get_clip_count_for_topic(topic)
        print(f"  Clip gate: clips_needed=True, current count={clip_count}/{CLIP_THRESHOLD}")
        if clip_count < CLIP_THRESHOLD:
            _trigger_video_research(topic, niche)
            _send_alert(
                f"Clip gate: '{topic[:60]}' needs {CLIP_THRESHOLD} clips, has {clip_count}. "
                f"video-research.yml triggered. Build skipped — will retry when clips ready."
            )
            return None
        print(f"  Clip gate: {clip_count} clips ready — proceeding with build.")

    # 1. Generate content
    print("  Generating content via Claude Haiku...")
    brief = topic_entry.get("brief", "")
    # FIX 5: FORMAT-019 brief gate — skip post if no capture brief exists
    if series_override == "DADOS OU AGENDA" and not brief.strip():
        print(f"  SKIP: no capture brief found for {post_id} — FORMAT-019 (Dados ou Agenda?) requires /capture first")
        if queue_row:
            write_queue_status(queue_row, status="Needs Brief",
                               extra={"notes": "FORMAT-019 skipped: no capture brief. Run /capture first."})
        _send_alert(
            f"FORMAT-019 skipped: '{topic[:60]}'\n"
            f"Reason: no capture brief found in Content Queue 'brief / angle' column.\n"
            f"Fix: run /capture on the source post, then re-approve in Content Queue."
        )
        return None
    # Verificamos: Route A = clip overlay (verificamos_clip), Route B = debunk carousel (verificamos)
    # Dados ou Agenda: always uses the dados-ou-agenda template (9-slide bias check)
    if series_override == "VERIFICAMOS":
        template_key = "verificamos_clip" if fake_news_route == "A" else "verificamos"
    elif series_override == "DADOS OU AGENDA":
        template_key = "dados-ou-agenda"
    elif niche == "opc":
        template_key = _resolve_opc_template(topic_entry, topic, run_date)
    elif niche in ("brazil", "usa"):
        template_key = _resolve_news_template(topic_entry, niche)
    else:
        template_key = None
    content = generate_carousel_content(topic, niche, template_key, brief=brief)
    if not content:
        print("  FAILED: content generation")
        return None

    if content and template_key:
        content["_template_key"] = template_key

    if niche in ("brazil", "usa"):
        content = _enforce_news_visual_targets(content, topic, niche)

    # 1b. Visual audit — flag boring/incomplete carousels before rendering
    _, audit_issues, audit_summary = visual_audit(content, niche)
    print(f"  {audit_summary}")
    if audit_issues:
        _send_alert(f"Visual audit issues for '{topic[:50]}':\n\n{audit_summary}")

    # 2. Build HTML
    work = WORK_DIR / post_id
    work.mkdir(parents=True, exist_ok=True)
    png_dir = work / "png"
    motion_dir = work / "motion"

    # 1c. Fetch media (CC context images + AI cover) before building HTML so
    # _build_brazil_html() can inject real <img> tags instead of placeholder text.
    print("  Fetching context images + cover...")
    media_paths = fetch_all_media(content, niche, str(work), brief=brief)

    # 1d. Fetch video clips for motion version (Clip Collections → Apify → Pexels → ...)
    # Inject topic into each suggestion so tier_clip_collections can match by topic name.
    for sugg in content.get("clip_suggestions", []):
        sugg.setdefault("topic", topic)
    print("  Fetching video clips for motion...")
    clips, clip_failures = fetch_clips(content, str(work))

    # Thread clips into media_paths so HTML builders can render first-frame stills
    # on motion slides in the static cover.html (alternating clip-bg pattern).
    if clips:
        media_paths["clips"] = clips

    _raw_handle = content.get("source_handle", "")
    handle_arg = (f"@{_raw_handle}" if _raw_handle and not _raw_handle.startswith("@") else _raw_handle)
    if not handle_arg or "PLACEHOLDER" in handle_arg.upper():
        handle_arg = _fallback_source_handle(niche)
    html_path = build_html(content, niche, slug, str(work), handle=handle_arg, media_paths=media_paths)
    if not html_path:
        print("  FAILED: HTML build")
        return None

    # 2b. Build per-slide motion HTML files (one per clip slot, separate from cover.html)
    clip_html_files = build_motion_html(content, niche, slug, str(work), clips, media_paths=media_paths, clip_failures=clip_failures)

    # 3. Render PNGs (uses static cover.html — unchanged)
    print("  Rendering PNGs...")
    os.environ["EXPORT_SCRIPT"] = EXPORT_SCRIPT
    if not render_pngs(html_path, str(png_dir)):
        print("  FAILED: PNG render")
        return None

    # 4. Render motion — RENDERER CASCADE: Remotion → Playwright → Ken Burns
    #    Each tier is a fallback for the one above. Motion is default-ON; Ken Burns is the floor.
    #    4a. Remotion render for the black cover — React-source, highest quality.
    #    4b. Playwright video recording for slides with clips + HTML-source templates.
    #    4c. Ken Burns ffmpeg zoom for every slide not already covered above.
    print("  Rendering motion (cascade: Remotion → Playwright → Ken Burns)...")
    recorded_mp4s = []
    recorded_indices = set()

    # 4a — Remotion cover render (if Remotion project present). Uses the clip from slide 1 if we got one.
    clip_suggestions = content.get("clip_suggestions", [])
    cover_sugg = next((c for c in clip_suggestions if c.get("slide", 0) == 1),
                     (clip_suggestions[0] if clip_suggestions else {}))
    cover_motion_prompt = cover_sugg.get("motion_prompt", "")
    cover_renderer_pref = cover_sugg.get("motion_renderer", "remotion")  # default: remotion for cover
    black_covers = sorted(png_dir.glob("black_01_*_html.png"))
    remotion_cover_done = False
    if black_covers and cover_renderer_pref == "remotion":
        remotion_clip = clips.get(1, "")
        r_path = render_motion_remotion(
            str(black_covers[0]), remotion_clip, str(motion_dir), "black",
            hook_text=(content.get("hook") or "")[:48], slide_idx=1
        )
        if r_path:
            remotion_cover_done = True
            recorded_indices.add(1)

    # 4b — Playwright recording for any clip slide (cover included if Remotion missed + HTML clip file exists).
    if clip_html_files:
        pw_inputs = [(idx, h) for idx, h in clip_html_files
                     if not (idx == 1 and remotion_cover_done)]
        if pw_inputs:
            print(f"  Recording {len(pw_inputs)} clip slide(s) via Playwright...")
            recorded_mp4s = record_motion_slides(pw_inputs, str(motion_dir), duration=5)
            recorded_indices |= {idx for idx, _ in recorded_mp4s}
            print(f"  Playwright recorded: {len(recorded_mp4s)} MP4(s) (slides {sorted({idx for idx,_ in recorded_mp4s})})")

    # 4c. Kling — fires automatically when Remotion didn't produce an mp4 and no
    # real clip exists for the cover. Cascade: Remotion → Kling → Ken Burns.
    # KLING_APPROVE=0 can explicitly disable Kling for a specific run if needed.
    kling_disabled = os.environ.get("KLING_APPROVE", "").strip().lower() == "0"
    cover_has_real_clip = bool(clips.get(1))
    black_covers = sorted(png_dir.glob("black_01_*_html.png"))
    if black_covers and not remotion_cover_done and not cover_has_real_clip and not kling_disabled:
        anim_prompt = (
            content.get("cover_visual", {}).get("option_b", {}).get("prompt", "")
            or "Subtle cinematic camera movement, documentary editorial style"
        )
        print(f"  Kling: Remotion missed + no real clip — animating cover PNG...")
        _animate_cover_kling(str(black_covers[0]), anim_prompt, str(motion_dir), "black")

    # Motion completeness guard — never email preview with empty motion folder
    motion_mp4s = list(motion_dir.glob("*.mp4")) if motion_dir.exists() else []
    if not motion_mp4s:
        _send_alert(f"Motion folder empty for '{topic[:40]}' — skipping preview. Check Playwright + record_motion_slides logs.")
        return None

    # Media presence check (non-blocking) — alert if images/clips are missing
    media_ok, media_issues = _check_media_presence(
        str(png_dir), str(motion_dir), str(work / "resources"), post_id)
    if media_issues:
        _send_alert(
            f"Media gaps for '{topic[:40]}':\n" +
            "\n".join(f"  - {x}" for x in media_issues) +
            "\nPost will still ship but may have placeholder text on some slides."
        )

    # 5. Upload to Drive — ONE version folder per post, png/ + motion/ + resources/ nested inside.
    # Shape (NEW 2026-04-20): <carousel_folder_id>/v<N>_<slug>/{cover.html, png/, motion/, resources/, story doc}
    # carousel_folder_id is the niche-level Carousel parent (no series, no _TEMPLATE_CAROUSEL middle layer).
    print("  Uploading to Drive...")
    parent = TEST_OUTPUT_FOLDER or get_route(niche).get("carousel_folder_id", "")
    if not parent:
        _send_alert(f"No carousel_folder_id configured for niche '{niche}' in routing.py — skipping upload for '{topic[:40]}'")
        return None

    version = next_version_number(parent, slug, drive)
    version_name = f"v{version}_{slug}"
    print(f"  Version folder: {version_name}")

    version_folder_id = create_subfolder(parent, version_name, drive)
    sc = SHORTCUT_FOLDERS.get(niche, {})

    # cover.html at version-folder root
    upload_single_file(html_path, version_folder_id, "cover.html", "text/html", drive)

    # png/  — full static post (all variants × all slides)
    # Carousel shortcut points here so opening it shows slides immediately (no subfolders).
    png_sub = create_subfolder(version_folder_id, "png", drive)
    upload_dir_contents(png_dir, png_sub, drive)
    if sc.get("carousels"):
        add_shortcut(png_sub, version_name, sc["carousels"], drive)
        print(f"  Shortcut → Shortcuts/Carousels/{version_name}")

    # motion/  — self-contained full post: animated covers + duplicated non-cover PNGs
    # Scheduler posts slides 1..N in order from ONE folder; never stitches across png/+motion/.
    motion_sub = create_subfolder(version_folder_id, "motion", drive)
    if motion_dir.exists():
        upload_dir_contents(motion_dir, motion_sub, drive)
    # duplicate non-cover PNGs so motion/ holds the complete sequence
    upload_dir_contents(png_dir, motion_sub, drive, skip_pattern=r"_01_cover")
    # Video shortcut: point to the cover MP4 file directly so Drive plays it inline.
    # Only created when an actual MP4 exists in the motion folder.
    if sc.get("videos"):
        from googleapiclient.errors import HttpError as _HttpError
        mp4s = drive.files().list(
            q=f"'{motion_sub}' in parents and mimeType='video/mp4' and trashed=false",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            fields="files(id,name)"
        ).execute().get("files", [])
        cover_mp4 = next((f for f in mp4s if "cover" in f["name"].lower()), mp4s[0] if mp4s else None)
        if cover_mp4:
            add_shortcut(cover_mp4["id"], version_name, sc["videos"], drive)
            print(f"  Shortcut → Shortcuts/Videos/{version_name} (MP4)")

    # resources/ — image suggestions + screenshot crops + clip hints
    resources_sub = create_subfolder(version_folder_id, "resources", drive)
    try:
        from googleapiclient.http import MediaInMemoryUpload
        img_sugg = generate_image_suggestions(content, niche)
        drive.files().create(
            body={"name": "image_suggestions.txt", "parents": [resources_sub]},
            media_body=MediaInMemoryUpload(img_sugg.encode("utf-8"), mimetype="text/plain"),
            supportsAllDrives=True, fields="id",
        ).execute()
        print("  image_suggestions.txt → resources/")
    except Exception as e:
        print(f"  image_suggestions.txt upload failed (non-fatal): {e}")

    # Write/upload image provenance so we always know who generated each image next run.
    try:
        from googleapiclient.http import MediaInMemoryUpload
        prov = media_paths.get("provenance", {}) if isinstance(media_paths, dict) else {}
        prov_payload = {
            "post_id": post_id,
            "topic": topic,
            "niche": niche,
            "version_folder_id": version_folder_id,
            "generated_at": datetime.now(ET).isoformat(),
            "cover": prov.get("cover", {}),
            "slides": prov.get("slides", {}),
        }
        drive.files().create(
            body={"name": "media_provenance.json", "parents": [resources_sub]},
            media_body=MediaInMemoryUpload(
                json.dumps(prov_payload, indent=2, ensure_ascii=False).encode("utf-8"),
                mimetype="application/json"
            ),
            supportsAllDrives=True, fields="id",
        ).execute()
        print("  media_provenance.json → resources/")
    except Exception as e:
        print(f"  media_provenance.json upload failed (non-fatal): {e}")

    # Upload any CC photos downloaded by _fetch_person_photo() into resources/images/
    # Filenames are human-readable slugs (slide1_trump_oval_office.jpg, slide2_congress_vote.jpg)
    # so Priscila can scan resources/images/ and see who/what each slide references.
    local_images = work / "resources" / "images"
    if local_images.exists():
        try:
            images_sub = create_subfolder(resources_sub, "images", drive)
            upload_dir_contents(local_images, images_sub, drive)
            print(f"  resources/images/ → Drive ({sum(1 for _ in local_images.iterdir())} file(s))")
        except Exception as e:
            print(f"  resources/images/ upload failed (non-fatal): {e}")

    # Upload raw YouTube/Pexels clips downloaded by fetch_clips() into resources/clips/
    # Keeps source MP4s archived alongside images — Priscila can review the actual footage
    # that went into each motion slide, not just the composited output.
    local_clips = work / "clips"
    if local_clips.exists() and any(local_clips.iterdir()):
        try:
            clips_sub = create_subfolder(resources_sub, "clips", drive)
            upload_dir_contents(local_clips, clips_sub, drive)
            print(f"  resources/clips/ → Drive ({sum(1 for _ in local_clips.iterdir())} file(s))")
        except Exception as e:
            print(f"  resources/clips/ upload failed (non-fatal): {e}")

    # Alternate template renders: cutout/ + illustrated/ alongside png/ + motion/
    # Runs when OPC and the primary template is "tip" (default) — adds the other styles
    # to the same version folder so all three variants live in one place.
    primary_tkey = content.get("_template_key", "tip")
    if niche == "opc" and primary_tkey in ("tip", "native", "auto", ""):
        for alt_key in ("cutout", "illustrated"):
            try:
                import copy
                alt_content = copy.deepcopy(content)
                alt_content["_template_key"] = alt_key
                alt_work = work / f"_{alt_key}"
                alt_work.mkdir(parents=True, exist_ok=True)
                alt_html = build_html(alt_content, niche, slug, str(alt_work), handle=handle_arg, media_paths=media_paths)
                if not alt_html:
                    print(f"  {alt_key}/ HTML build failed — skipping subfolder")
                    continue
                alt_png_dir = alt_work / "png"
                if render_pngs(alt_html, str(alt_png_dir)):
                    alt_sub = create_subfolder(version_folder_id, alt_key, drive)
                    upload_dir_contents(alt_png_dir, alt_sub, drive)
                    n_pngs = sum(1 for _ in alt_png_dir.glob("*.png"))
                    print(f"  {alt_key}/ subfolder: {n_pngs} PNGs → Drive")
                else:
                    print(f"  {alt_key}/ PNG render failed — skipping subfolder")
            except Exception as e:
                print(f"  {alt_key}/ render failed (non-fatal): {e}")

    folder_link = f"https://drive.google.com/drive/folders/{version_folder_id}"
    motion_link = f"https://drive.google.com/drive/folders/{motion_sub}"
    print(f"  Version: {folder_link}")
    print(f"  Motion:  {motion_link}")

    # story (Google Doc) — slide-by-slide script + research
    story_doc = create_story_doc(version_folder_id, slug, version, topic, niche, brief, content, drive, folder_link,
                                 series_override=topic_entry.get("series_override", ""))
    story_link = story_doc.get("webViewLink", "")
    print(f"  Story: {story_link}")

    # Flow tracking: Content Queue → Built + Drive path
    # Verificamos + Dados ou Agenda get "Pending Approval" — approval email gate prevents auto-schedule
    queue_status = "Pending Approval" if series_override in ("VERIFICAMOS", "DADOS OU AGENDA") else "Built"
    if queue_row:
        write_queue_status(queue_row, status=queue_status, drive_folder_path=folder_link)

    # 6. Add catalog row (OPC project tracker) — motion column deep-links to /motion subfolder
    # series_override supports: Verificamos, Fact-Checked, DADOS OU AGENDA, etc.
    # Map override codes to display names before falling back to niche defaults.
    _series_display_map = {
        "VERIFICAMOS": "Verificamos",
        "DADOS OU AGENDA": "Dados ou Agenda?",
        "FACT-CHECKED": "Fact-Checked",
    }
    series = _series_display_map.get(series_override, series_override) or (
        "Tip of the Week" if niche == "opc" else ("The Chain" if niche == "usa" else "Quem Decidiu Isso?")
    )
    add_catalog_row(post_id, niche, series, topic, folder_link, motion_link, get_oauth_token())

    # Classify post type + format for In Production tab
    _series_lower = (series_override or series or "").lower()
    if niche == "opc":
        _post_type = "General Tip"  # all auto-built OPC posts are tips; Before & After / Project Showcase set manually
    elif series_override == "VERIFICAMOS" or "verificamos" in _series_lower:
        _post_type = "Fake News"
    elif series_override == "DADOS OU AGENDA" or "dados" in _series_lower or "agenda" in _series_lower:
        _post_type = "Bias Check"
    elif "quem" in _series_lower or "decidiu" in _series_lower:
        _post_type = "Who Decided"
    elif "conta" in _series_lower or "money" in _series_lower:
        _post_type = "Money"
    elif "history" in _series_lower or "arquivo" in _series_lower:
        _post_type = "History"
    elif "explainer" in _series_lower or "o que" in _series_lower or "what is" in _series_lower:
        _post_type = "Explainer"
    else:
        _post_type = "Breaking"
    _fmt = "Both"  # motion is always built (NONNEGOTIABLES: MOTION IS DEFAULT ON)

    # Mirror to the correct In Production tab based on niche
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        if niche == "opc":
            from content_tracker import update_in_production
            update_in_production(
                title=topic[:100],
                content_type="Carousel",
                status="Built",
                drive_folder_link=folder_link,
                output_link=motion_link,
                date_created=datetime.now(ET).strftime("%Y-%m-%d"),
                fmt=_fmt,
                post_type=_post_type,
            )
        else:
            from content_tracker import update_news_in_production
            update_news_in_production(
                title=topic[:100],
                niche=niche.upper(),
                content_type="Carousel",
                status="Built",
                drive_folder_link=folder_link,
                output_link=motion_link,
                date_created=datetime.now(ET).strftime("%Y-%m-%d"),
                fmt=_fmt,
                post_type=_post_type,
            )
    except Exception as _e:
        print(f"  In Production write skipped (non-fatal): {_e}")

    # Collect mentioned people + cover_visual for reply guide in preview email
    mentioned_people = []
    for slide in content.get("slides", []):
        for p in slide.get("mentioned_people", []):
            if isinstance(p, str):
                mentioned_people.append(p)
            elif isinstance(p, dict):
                name_val = p.get("name", "")
                # Guard: Haiku can return nested dict as name (e.g. {"en":…,"pt":…}) — stringify it
                mentioned_people.append(str(name_val) if name_val else str(p))
    # dict.fromkeys requires hashable elements — stringify anything that slipped through
    mentioned_people = list(dict.fromkeys(
        x if isinstance(x, str) else str(x) for x in mentioned_people
    ))

    # F6 fix: populate cover_urls so first-pass preview email shows real images.
    # make_cover_thumbnails_public scans direct children only — pass png_sub (not version_folder_id).
    cover_urls = {}
    try:
        from email_preview import make_cover_thumbnails_public
        cover_urls = make_cover_thumbnails_public(png_sub, get_oauth_token())
    except Exception as _cover_err:
        print(f"  cover_urls fetch failed (non-fatal): {_cover_err}")

    # FIX 3: Generate Instagram caption + hashtags
    print("  Generating Instagram caption...")
    _slide_texts = []
    for _sl in content.get("slides", []):
        for _field in ("heading_pt", "heading_en", "items_pt", "quote", "context_pt"):
            _v = _sl.get(_field)
            if isinstance(_v, list):
                _slide_texts.extend(_v)
            elif _v:
                _slide_texts.append(str(_v))
    caption_result = {}
    try:
        caption_result = generate_caption(topic, niche, _slide_texts)
    except Exception as _cap_err:
        print(f"  Caption generation failed (non-fatal): {_cap_err}")
    if not caption_result.get("caption"):
        caption_result = _caption_fallback(topic, niche)
        print("  Caption fallback used")
    # Save caption.txt alongside PNGs in work_dir
    if caption_result.get("caption"):
        try:
            caption_txt = (
                f"CAPTION\n{caption_result['caption']}\n\n"
                f"IN-POST HASHTAGS\n{caption_result.get('in_post_hashtags', '')}\n\n"
                f"FIRST COMMENT HASHTAGS\n{caption_result.get('first_comment_hashtags', '')}\n"
            )
            (work / "caption.txt").write_text(caption_txt, encoding="utf-8")
            # Upload caption.txt to version folder root in Drive
            try:
                from googleapiclient.http import MediaInMemoryUpload
                drive.files().create(
                    body={"name": "caption.txt", "parents": [version_folder_id]},
                    media_body=MediaInMemoryUpload(caption_txt.encode("utf-8"), mimetype="text/plain"),
                    supportsAllDrives=True, fields="id",
                ).execute()
                print("  caption.txt → Drive version folder")
            except Exception as _cap_up_err:
                print(f"  caption.txt Drive upload failed (non-fatal): {_cap_up_err}")
        except Exception as _cap_save_err:
            print(f"  caption.txt save failed (non-fatal): {_cap_save_err}")

    return {
        "post_id": post_id,
        "topic": topic,
        "niche": niche,
        "series_override": series_override,
        "fake_news_route": fake_news_route,
        "requires_approval": series_override in ("VERIFICAMOS", "DADOS OU AGENDA"),
        "queue_row_idx": queue_row,
        "version": version,
        "version_folder_id": version_folder_id,
        "version_link": folder_link,
        "story_link": story_link,
        # legacy keys kept for email_preview.py + approval_handler.py compatibility
        "static_folder_id": version_folder_id,
        "motion_folder_id": motion_sub,
        "static_link": folder_link,
        "motion_link": motion_link,
        # cover thumbnails for first-pass preview email
        "cover_urls": cover_urls,
        # reply guide data
        "cover_visual": content.get("cover_visual", {}),
        "clip_suggestions": content.get("clip_suggestions", []),
        "mentioned_people": mentioned_people,
        # clip failure map — {slide_idx: slot_name} for slots that exhausted all 7 tiers
        # non-empty = motion HTML shows ⚠️ placeholder; email preview surfaces the warning
        "clip_failures": clip_failures,
        # FIX 3: caption data for preview email
        "caption": caption_result.get("caption", ""),
        "in_post_hashtags": caption_result.get("in_post_hashtags", ""),
        "first_comment_hashtags": caption_result.get("first_comment_hashtags", ""),
    }


def run_motion_only(slug, niche, drive):
    """Re-render motion for an existing carousel version without full rebuild.

    Use: MANUAL_TEMPLATE=motion, MANUAL_TOPIC=<slug>, MANUAL_NICHE=<niche>.
    Finds the latest v<N>_<slug> version folder, downloads PNGs from png/,
    applies CSS Ken Burns zoom via ffmpeg on each PNG, uploads to motion_remotion/
    subfolder alongside the existing motion/ (Playwright) folder for comparison.

    NOTE: This is a comparison/test path. The primary motion/ folder (built by
    process_one_topic via build_motion_html + record_motion_slides) uses CSS KB
    on the background layer only with optional clip sticker — text stays static.
    This path applies ffmpeg zoompan to the full PNG (text moves too) and is
    labelled motion_remotion/ so it never overwrites the proper motion output.
    """
    import io, re as _re
    from googleapiclient.http import MediaIoBaseDownload

    parent = TEST_OUTPUT_FOLDER or get_route(niche).get("carousel_folder_id", "")
    if not parent:
        print(f"[motion-only] No carousel_folder_id for niche='{niche}' — aborting")
        return None

    # Find latest v<N>_<slug> version folder
    resp = drive.files().list(
        q=f"'{parent}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute()
    pattern = _re.compile(rf"^v(\d+)_{_re.escape(slug)}$")
    best, best_n = None, 0
    for f in resp.get("files", []):
        m = pattern.match(f["name"])
        if m and int(m.group(1)) > best_n:
            best_n, best = int(m.group(1)), f
    if not best:
        print(f"[motion-only] No version folder for slug='{slug}' under parent '{parent}'")
        return None

    version_folder_id = best["id"]
    version_name = best["name"]
    print(f"[motion-only] Found: {version_name} (id={version_folder_id})")

    # Find png/ + motion/ subfolders
    children = drive.files().list(
        q=f"'{version_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute().get("files", [])
    png_sub_id    = next((f["id"] for f in children if f["name"] == "png"),    None)
    motion_sub_id = next((f["id"] for f in children if f["name"] == "motion_remotion"), None)

    if not png_sub_id:
        print(f"[motion-only] No png/ subfolder in {version_name} — nothing to animate")
        return None
    if not motion_sub_id:
        motion_sub_id = create_subfolder(version_folder_id, "motion_remotion", drive)
        print(f"[motion-only] Created motion_remotion/ subfolder (KB ffmpeg comparison)")

    # Download all PNGs from png/ → /tmp
    png_files = drive.files().list(
        q=f"'{png_sub_id}' in parents and mimeType='image/png' and trashed=false",
        fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives",
    ).execute().get("files", [])
    if not png_files:
        print(f"[motion-only] No PNGs in png/ subfolder")
        return None

    local_png_dir    = WORK_DIR / "png"
    local_motion_dir = WORK_DIR / "motion"
    local_png_dir.mkdir(parents=True, exist_ok=True)
    local_motion_dir.mkdir(parents=True, exist_ok=True)

    for pf in png_files:
        local_path = local_png_dir / pf["name"]
        request = drive.files().get_media(fileId=pf["id"])
        fh = io.FileIO(str(local_path), "wb")
        dl = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = dl.next_chunk()
        print(f"  downloaded {pf['name']}")

    # Apply ffmpeg KB zoom to each PNG → comparison MP4 (text moves — intentional for comparison)
    for png in sorted(local_png_dir.glob("*.png")):
        variant = png.name.split("_")[0]  # black / cream / lime
        render_motion_cover(str(png), str(local_motion_dir), variant)
        print(f"  KB ffmpeg comparison: {png.name}")

    # Upload to motion_remotion/ — comparison only, does NOT replace primary motion/ output
    upload_dir_contents(local_motion_dir, motion_sub_id, drive)
    motion_link = f"https://drive.google.com/drive/folders/{motion_sub_id}"
    print(f"[motion-only] Comparison done — motion_remotion/ folder: {motion_link}")
    return {"slug": slug, "niche": niche, "version": version_name,
            "motion_folder_id": motion_sub_id, "motion_link": motion_link}


def main():
    start = time.time()
    now_et = datetime.now(ET)
    run_date = now_et.strftime("%Y%m%d")

    print(f"[content_creator] Starting — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    # Clean work dir
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True)
    # Seed empty results.json immediately so reviewer/auditor always have a valid file
    # even when the run exits early (no approved rows, all topics fail, etc.)
    try:
        (WORK_DIR / "results.json").write_text("[]")
    except Exception:
        pass

    # Phase A: Manual build mode (workflow-dispatch controls) OR queue-driven build

    # Motion-only shortcut: re-run Ken Burns on existing PNGs without rebuilding carousel.
    # Use: MANUAL_TEMPLATE=motion, MANUAL_TOPIC=<slug>, MANUAL_NICHE=<niche>
    if MANUAL_MODE and MANUAL_TEMPLATE == "motion" and MANUAL_TOPIC and MANUAL_NICHE in ("opc", "brazil", "usa"):
        import re as _re_slug
        slug = _re_slug.sub(r"[^a-z0-9-]", "-", MANUAL_TOPIC.strip().lower())[:50].strip("-")
        print(f"\n--- Motion-only mode: re-render Ken Burns for '{slug}' ({MANUAL_NICHE}) ---")
        drive = get_drive_service()
        res = run_motion_only(slug, MANUAL_NICHE, drive)
        if res:
            print(f"  Motion folder: {res['motion_link']}")
            results_file = WORK_DIR / "results.json"
            try:
                results_file.write_text(json.dumps([res], ensure_ascii=False, indent=2))
            except Exception as e:
                print(f"  Could not write results.json: {e}")
        else:
            print("  Motion-only run produced no output — check slug + niche")
            sys.exit(1)
        return

    if MANUAL_MODE and MANUAL_TOPIC and MANUAL_NICHE in ("opc", "brazil", "usa"):
        print("\n--- Manual Mode: direct topic/template build ---")
        print(f"  topic={MANUAL_TOPIC[:80]}")
        print(f"  niche={MANUAL_NICHE}")
        print(f"  template={MANUAL_TEMPLATE}")
        print(f"  template_set={MANUAL_TEMPLATE_SET}")

        # Resolve MANUAL_BRIEF: fetch Google Doc if URL, else use as-is
        resolved_brief = ""
        if MANUAL_BRIEF:
            if "docs.google.com/document" in MANUAL_BRIEF or (MANUAL_BRIEF.startswith("https://") and "/d/" in MANUAL_BRIEF):
                from topic_picker import fetch_drive_doc_content
                resolved_brief = fetch_drive_doc_content(MANUAL_BRIEF) or MANUAL_BRIEF
                print(f"  brief: fetched {len(resolved_brief)} chars from Drive doc")
            else:
                resolved_brief = MANUAL_BRIEF
                print(f"  brief: using provided text ({len(resolved_brief)} chars)")
        else:
            print("  brief: none provided (MANUAL_BRIEF not set)")

        if MANUAL_TEMPLATE_SET == "all":
            if MANUAL_NICHE == "opc":
                tkeys = ["tip", "illustrated", "cutout"]
            else:
                tkeys = ["native", "illustrated", "cutout"]
        else:
            tkeys = [MANUAL_TEMPLATE or "auto"]

        manual_topics = []
        for t in tkeys:
            entry = {
                "topic": MANUAL_TOPIC,
                "niche": MANUAL_NICHE,
                "brief": resolved_brief or f"Manual run from workflow_dispatch. Template request: {t}",
                "url": "",
                "format": "",
                "series_override": "DADOS OU AGENDA" if t == "dados-ou-agenda" else "",
                "fake_news_route": "B",
                "fake_news_confidence": 1.0,
                "queue_row_idx": None,
            }
            if t not in ("", "auto"):
                entry["template_key"] = t
            manual_topics.append(entry)

        drive = get_drive_service()
        results = []
        errors = []
        for topic in manual_topics:
            try:
                result = process_one_topic(topic, run_date, drive)
                if result:
                    results.append(result)
            except Exception as e:
                err = f"manual '{topic['topic'][:40]}' ({topic.get('template_key','auto')}): {e}"
                print(f"  ERROR processing {err}")
                errors.append(err)

        if not results:
            _send_alert("Manual mode rendered zero carousels.\n" + ("\n".join(errors) if errors else "No errors captured."))
            return

        try:
            send_preview(results, now_et.strftime("%Y-%m-%d"))
        except Exception as e:
            print(f"  Manual preview email send failed: {e}")

        print(f"\n[content_creator] Manual mode done — {len(results)} posts")
        results_file = WORK_DIR / "results.json"
        try:
            results_file.write_text(json.dumps(results, default=str))
        except Exception as e:
            print(f"  Could not write results.json: {e}")
        return

    # Phase A: Promote fresh topics from Inspiration → Content Queue
    # Pre-approved Inspiration rows → CQ Status=Approved (auto-builds this run)
    # Scored but un-approved rows → CQ Status=Draft (you flip to Approved in sheet to release)
    print("\n--- Phase A: Promoting Inspiration → Content Queue ---")
    try:
        picks = pick_topics(count_opc=2, count_brazil=1, count_usa=1)
        pick_counts = {"opc": 0, "brazil": 0, "usa": 0}
        for p in picks or []:
            n = (p.get("niche") or "").lower()
            if n in pick_counts:
                pick_counts[n] += 1
        shortfall = []
        if pick_counts["brazil"] < 1:
            shortfall.append("brazil<1")
        if pick_counts["usa"] < 1:
            shortfall.append("usa<1")
        if shortfall:
            msg = (
                "Niche shortfall during topic promotion: "
                + ", ".join(shortfall)
                + f" | picks={pick_counts}. "
                "Pipeline may overfill OPC unless Brazil/USA topics are approved and eligible."
            )
            print(f"  WARNING: {msg}")
            _send_alert(msg)
    except Exception as e:
        print(f"  Topic picker failed: {e}")
        _send_alert(f"Topic picker crashed: {e}")

    # Phase B: Build every Content Queue row where Status=Approved
    print("\n--- Phase B: Reading Content Queue for Approved rows ---")
    approved = get_approved_queue_rows()
    if not approved:
        print("  No Approved rows in Content Queue — nothing to build this run")
        _send_alert("No Approved rows in Content Queue — pipeline picked zero topics to build. Check Inspiration Library scoring + Queue status flips.")
        return
    print(f"  Found {len(approved)} Approved carousel(s) to build")
    approved_counts = {"opc": 0, "brazil": 0, "usa": 0}
    for a in approved:
        n = (a.get("niche") or "").lower()
        if n in approved_counts:
            approved_counts[n] += 1
    if approved_counts["brazil"] == 0 or approved_counts["usa"] == 0:
        _send_alert(
            "Approved queue niche gap: "
            f"opc={approved_counts['opc']}, brazil={approved_counts['brazil']}, usa={approved_counts['usa']}. "
            "No auto-build for missing niche in this run."
        )

    drive = get_drive_service()
    results = []
    errors = []
    for topic in approved:
        try:
            result = process_one_topic(topic, run_date, drive)
            if result:
                results.append(result)
        except Exception as e:
            err = f"'{topic['topic'][:40]}': {e}"
            print(f"  ERROR processing {err}")
            errors.append(err)
            continue

    if not results:
        print("\nNo carousels rendered — exiting without email")
        msg = f"Zero carousels rendered out of {len(approved)} Approved topics.\n\nErrors:\n" + "\n".join(errors) if errors else "Zero carousels rendered — no per-topic errors captured (silent failure)."
        _send_alert(msg)
        return
    if errors:
        _send_alert(f"{len(results)}/{len(approved)} carousels rendered — {len(errors)} failures:\n\n" + "\n".join(errors))

    # Phase C: Send preview email → mark each row 'Email Sent'
    print(f"\n--- Phase C: Sending preview email ({len(results)} posts) ---")
    try:
        send_preview(results, now_et.strftime("%Y-%m-%d"))
        for r in results:
            if r.get("queue_row_idx"):
                write_queue_status(r["queue_row_idx"], status="Email Sent")
    except Exception as e:
        print(f"  Email send failed: {e}")

    elapsed = time.time() - start
    print(f"\n[content_creator] Done — {len(results)} posts rendered in {elapsed:.0f}s")

    # Write results for downstream carousel reviewer
    results_file = WORK_DIR / "results.json"
    try:
        results_file.write_text(json.dumps(results, default=str))
    except Exception as e:
        print(f"  Could not write results.json: {e}")

    # Log each rendered post to Content Creation Log
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from content_tracker import log_run
        for r in results:
            log_run(pipeline="content_creator", trigger="scheduled",
                    niche=r.get("niche", ""), project="content", status="success",
                    drive_path=r.get("version_link", "") or r.get("static_folder_link", ""),
                    notes=r.get("topic", "")[:100])
        if errors:
            for err in errors:
                log_run(pipeline="content_creator", trigger="scheduled",
                        status="failed", notes=err[:200])
    except Exception: pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"\n🔴 UNCAUGHT: {e}\n{tb}")
        _send_alert(f"Uncaught crash in main():\n{e}\n\n{tb[-2000:]}")
        sys.exit(1)
