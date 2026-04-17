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
from topic_picker import pick_topics
from carousel_builder import generate_carousel_content, build_html, render_pngs, generate_image_suggestions, visual_audit
import urllib.request, urllib.parse
from email_preview import send_preview, update_catalog_status

WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/content_creator_run"))
EXPORT_SCRIPT = os.environ.get("EXPORT_SCRIPT", str(Path(__file__).parent / "export_variants.js"))

# Drive folder IDs — _TEMPLATE_CAROUSEL parents per series.
# Every build lands at <SERIES>/_TEMPLATE_CAROUSEL/v<N>_<slug>/ (+ v<N>_<slug>_motion sibling).
# N auto-increments when a slug already has versions. See project_carousel_folder_standard.md.
OPC_TIP_TEMPLATE_FOLDER         = "1PWrZfuOvyHUbTRlFNqYxdhtg-Zvv_bXb"  # Marketing > OPC > Tip of the Week > _TEMPLATE_CAROUSEL
BRAZIL_QUEM_TEMPLATE_FOLDER     = "1Ts4OlXT_KxtYNziGmHUcsjHVh8Z7D1ds"  # News > Brazil > Quem decidiu isso > _TEMPLATE_CAROUSEL
USA_THE_CHAIN_TEMPLATE_FOLDER   = "1sDMyPHVYcOqZ3NK9ch4e48AaJ7KVvxL3"  # News > USA > Content > Series > The Chain > _TEMPLATE_CAROUSEL
SOVEREIGN_TEMPLATE_FOLDER       = os.environ.get("SOVEREIGN_TEMPLATE_FOLDER", "")  # News drive > SOVEREIGN > _TEMPLATE_CAROUSEL (set after folder creation)
VERIFICAMOS_TEMPLATE_FOLDER     = "1QhILiMiIM9WrpHhIqXXrPs6JqoAdDijA"  # News > Brazil > Series > Verificamos > _TEMPLATE_CAROUSEL
VERIFICAMOS_CONFIDENCE_THRESHOLD = 0.70  # auto-build gate — items below this score go to manual review queue

SHEET_ID    = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
INSPO_TAB   = "📥 Inspiration Library"
QUEUE_TAB   = "📋 Content Queue"
CATALOG_TAB = "📸 Project Content Catalog"

ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "priscila@oakpark-construction.com")


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
        niche = v(row, "source").lower() or ("opc" if "opc" in v(row, "format").lower() else "brazil")
        if niche not in ("opc", "brazil"):
            niche = "brazil" if "quem" in v(row, "format").lower() else "opc"
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
        })
    return approved


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


def render_motion_cover(cover_png_path, output_dir, variant):
    os.makedirs(output_dir, exist_ok=True)
    mp4_path = os.path.join(output_dir, f"{variant}_01_cover_motion.mp4")
    gif_path = os.path.join(output_dir, f"{variant}_01_cover_motion.gif")
    preview_path = os.path.join(output_dir, f"{variant}_preview_frame.jpg")

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
        post_id = f"opc-tip-{run_date}-{slug[:20]}"
    elif niche == "usa":
        post_id = f"usa-{run_date}-{slug[:20]}"
    elif series_override == "VERIFICAMOS":
        post_id = f"verificamos-{run_date}-{slug[:20]}"
    else:
        post_id = f"brazil-{run_date}-{slug[:20]}"

    print(f"\n{'='*60}")
    print(f"Processing: [{niche}] {topic}")
    print(f"Post ID: {post_id} | Queue row: {queue_row}")

    # 1. Generate content
    print("  Generating content via Claude Haiku...")
    brief = topic_entry.get("brief", "")
    # Verificamos: Route A = clip overlay (verificamos_clip), Route B = debunk carousel (verificamos)
    if series_override == "VERIFICAMOS":
        template_key = "verificamos_clip" if fake_news_route == "A" else "verificamos"
    elif niche == "opc":
        template_key = "tip"
    else:
        template_key = None
    content = generate_carousel_content(topic, niche, template_key, brief=brief)
    if not content:
        print("  FAILED: content generation")
        return None

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

    html_path = build_html(content, niche, slug, str(work))
    if not html_path:
        print("  FAILED: HTML build")
        return None

    # 3. Render PNGs
    print("  Rendering PNGs...")
    os.environ["EXPORT_SCRIPT"] = EXPORT_SCRIPT
    if not render_pngs(html_path, str(png_dir)):
        print("  FAILED: PNG render")
        return None

    # 4. Render motion — cover AND all middle slides get slow-zoom (Ken Burns)
    print("  Rendering motion...")
    for variant in ["black", "cream", "lime"]:
        for png in sorted(png_dir.glob(f"{variant}_*_html.png")):
            render_motion_cover(str(png), str(motion_dir), variant)

    # Motion completeness guard — never email preview with empty motion folder
    motion_mp4s = list(motion_dir.glob("*.mp4")) if motion_dir.exists() else []
    if not motion_mp4s:
        _send_alert(f"Motion folder empty for '{topic[:40]}' — skipping preview. Check ffmpeg + render_motion_cover logs.")
        return None

    # 5. Upload to Drive — ONE version folder per post, png/ + motion/ + resources/ nested inside.
    # Shape: <SERIES>/_TEMPLATE_CAROUSEL/v<N>_<slug>/{cover.html, png/, motion/, resources/, story doc}
    print("  Uploading to Drive...")
    if niche == "opc":
        parent = OPC_TIP_TEMPLATE_FOLDER
    elif niche == "usa":
        parent = USA_THE_CHAIN_TEMPLATE_FOLDER
    elif niche == "sovereign":
        parent = SOVEREIGN_TEMPLATE_FOLDER or BRAZIL_QUEM_TEMPLATE_FOLDER
    elif series_override == "VERIFICAMOS":
        parent = VERIFICAMOS_TEMPLATE_FOLDER
    else:
        parent = BRAZIL_QUEM_TEMPLATE_FOLDER

    version = next_version_number(parent, slug, drive)
    version_name = f"v{version}_{slug}"
    print(f"  Version folder: {version_name}")

    version_folder_id = create_subfolder(parent, version_name, drive)

    # cover.html at version-folder root
    upload_single_file(html_path, version_folder_id, "cover.html", "text/html", drive)

    # png/  — full static post (all variants × all slides)
    png_sub = create_subfolder(version_folder_id, "png", drive)
    upload_dir_contents(png_dir, png_sub, drive)

    # motion/  — self-contained full post: animated covers + duplicated non-cover PNGs
    # Scheduler posts slides 1..N in order from ONE folder; never stitches across png/+motion/.
    motion_sub = create_subfolder(version_folder_id, "motion", drive)
    if motion_dir.exists():
        upload_dir_contents(motion_dir, motion_sub, drive)
    # duplicate non-cover PNGs so motion/ holds the complete sequence
    upload_dir_contents(png_dir, motion_sub, drive, skip_pattern=r"_01_cover")

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
    # Verificamos gets "Pending Approval" — approval email gate prevents auto-schedule
    queue_status = "Pending Approval" if series_override == "VERIFICAMOS" else "Built"
    if queue_row:
        write_queue_status(queue_row, status=queue_status, drive_folder_path=folder_link)

    # 6. Add catalog row (OPC project tracker) — motion column deep-links to /motion subfolder
    # series_override supports Verificamos (Brazil) / Fact-Checked (USA) verification series
    series = topic_entry.get("series_override") or (
        "Tip of the Week" if niche == "opc" else ("The Chain" if niche == "usa" else "Quem Decidiu Isso?")
    )
    add_catalog_row(post_id, niche, series, topic, folder_link, motion_link, get_oauth_token())

    # Collect mentioned people + cover_visual for reply guide in preview email
    mentioned_people = []
    for slide in content.get("slides", []):
        mentioned_people.extend(slide.get("mentioned_people", []))
    mentioned_people = list(dict.fromkeys(mentioned_people))  # dedupe, preserve order

    return {
        "post_id": post_id,
        "topic": topic,
        "niche": niche,
        "series_override": series_override,
        "fake_news_route": fake_news_route,
        "requires_approval": series_override == "VERIFICAMOS",
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
        # reply guide data
        "cover_visual": content.get("cover_visual", {}),
        "clip_suggestions": content.get("clip_suggestions", []),
        "mentioned_people": mentioned_people,
    }


def main():
    start = time.time()
    now_et = datetime.now(ET)
    run_date = now_et.strftime("%Y%m%d")

    print(f"[content_creator] Starting — {now_et.strftime('%Y-%m-%d %H:%M ET')}")

    # Clean work dir
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True)

    # Phase A: Promote fresh topics from Inspiration → Content Queue
    # Pre-approved Inspiration rows → CQ Status=Approved (auto-builds this run)
    # Scored but un-approved rows → CQ Status=Draft (you flip to Approved in sheet to release)
    print("\n--- Phase A: Promoting Inspiration → Content Queue ---")
    try:
        pick_topics(count_opc=2, count_brazil=1, count_usa=1)
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
