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
from carousel_builder import generate_carousel_content, build_html, render_pngs
from email_preview import send_preview, update_catalog_status

WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/content_creator_run"))
EXPORT_SCRIPT = os.environ.get("EXPORT_SCRIPT", str(Path(__file__).parent / "export_variants.js"))

# Drive folder IDs
OPC_TEMPLATES_PARENT = "1HHQGPM3iOP6m1pdUnAKtpRXfBi1ejEvZ"
OPC_TIPS_FOLDER = "13TXtFL88Q2z8lkluSfBs_EoirxIdFCni"
BRAZIL_TEMPLATES_FOLDER = os.environ.get("BRAZIL_TEMPLATES_FOLDER", "")

SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
CATALOG_TAB = "📸 Project Content Catalog"


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


def upload_folder_to_drive(local_dir, parent_folder_id, folder_name, drive):
    from googleapiclient.http import MediaFileUpload

    folder = drive.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_folder_id]},
        supportsAllDrives=True, fields="id",
    ).execute()
    folder_id = folder["id"]

    for f in sorted(Path(local_dir).iterdir()):
        if f.is_file() and not f.name.startswith("."):
            mime = "image/png" if f.suffix == ".png" else "video/mp4" if f.suffix == ".mp4" else "image/gif" if f.suffix == ".gif" else "application/octet-stream"
            drive.files().create(
                body={"name": f.name, "parents": [folder_id]},
                media_body=MediaFileUpload(str(f), mimetype=mime),
                supportsAllDrives=True, fields="id",
            ).execute()

    return folder_id


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
        post_id, "general", "", series, "", "",
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
    slug = topic[:40].lower().replace(" ", "-").replace("'", "").replace('"', '')
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    post_id = f"opc-tip-{run_date}-{slug[:20]}" if niche == "opc" else f"brazil-{run_date}-{slug[:20]}"

    print(f"\n{'='*60}")
    print(f"Processing: [{niche}] {topic}")
    print(f"Post ID: {post_id}")

    # 1. Generate content
    print("  Generating content via Claude Haiku...")
    content = generate_carousel_content(topic, niche, "tip" if niche == "opc" else None)
    if not content:
        print("  FAILED: content generation")
        return None

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

    # 4. Render motion covers
    print("  Rendering motion covers...")
    for variant in ["black", "cream", "lime"]:
        cover_png = png_dir / f"{variant}_01_cover_html.png"
        if cover_png.exists():
            render_motion_cover(str(cover_png), str(motion_dir), variant)

    # 5. Upload to Drive
    print("  Uploading to Drive...")
    if niche == "opc":
        parent = OPC_TIPS_FOLDER
    else:
        parent = BRAZIL_TEMPLATES_FOLDER or OPC_TIPS_FOLDER
        if not BRAZIL_TEMPLATES_FOLDER:
            print("  WARNING: BRAZIL_TEMPLATES_FOLDER not set — using OPC folder as fallback")

    static_folder_id = upload_folder_to_drive(str(png_dir), parent, f"{post_id}_v1_static", drive)
    motion_folder_id = upload_folder_to_drive(str(motion_dir), parent, f"{post_id}_v1_motion", drive)

    static_link = f"https://drive.google.com/drive/folders/{static_folder_id}"
    motion_link = f"https://drive.google.com/drive/folders/{motion_folder_id}"
    print(f"  Static: {static_link}")
    print(f"  Motion: {motion_link}")

    # 6. Add catalog row
    import urllib.request, urllib.parse
    raw = os.environ.get("SHEETS_TOKEN", "")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    add_catalog_row(post_id, niche, "Tip of the Week", topic, static_link, motion_link, resp["access_token"])

    return {
        "post_id": post_id,
        "topic": topic,
        "niche": niche,
        "static_folder_id": static_folder_id,
        "motion_folder_id": motion_folder_id,
        "static_link": static_link,
        "motion_link": motion_link,
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

    # 1. Pick topics
    print("\n--- Step 1: Picking topics ---")
    topics = pick_topics(count_opc=2, count_brazil=1)
    if not topics:
        print("No topics found — exiting")
        return

    # 2. Process each topic
    drive = get_drive_service()
    results = []
    for topic in topics:
        try:
            result = process_one_topic(topic, run_date, drive)
            if result:
                results.append(result)
        except Exception as e:
            print(f"  ERROR processing '{topic['topic'][:40]}': {e}")
            continue

    if not results:
        print("\nNo carousels rendered — exiting without email")
        return

    # 3. Send preview email
    print(f"\n--- Step 3: Sending preview email ({len(results)} posts) ---")
    send_preview(results, now_et.strftime("%Y-%m-%d"))

    elapsed = time.time() - start
    print(f"\n[content_creator] Done — {len(results)} posts rendered in {elapsed:.0f}s")
    print(f"Status: pending_approval — waiting for email reply")


if __name__ == "__main__":
    main()
