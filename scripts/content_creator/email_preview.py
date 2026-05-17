#!/usr/bin/env python3
"""
email_preview.py — Sends per-carousel HTML review emails with full slide stack.
Reply workflow keywords:
  APPROVE / approved
  NOT GOOD - <feedback>
  REJECT - <feedback>
"""
import json
import os
import re
import smtplib
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
CATALOG_TAB = "📸 Project Content Catalog"


def get_token():
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN set")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    ).read())
    return resp["access_token"], td


def _send_via_workflow(subject, html_body):
    gh_token = os.environ.get("GH_TOKEN", "")
    if not gh_token:
        print("  No GH_TOKEN — cannot send email")
        return False

    payload = json.dumps({
        "ref": "main",
        "inputs": {
            "to": "priscila@oakpark-construction.com",
            "subject": subject,
            "html_body": html_body[:100000],
        },
    }).encode()

    req = urllib.request.Request(
        "https://api.github.com/repos/priihigashi/oak-park-ai-hub/actions/workflows/send_email.yml/dispatches",
        data=payload,
        headers={
            "Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    urllib.request.urlopen(req)
    print(f"  Preview email triggered via send_email.yml: {subject}")
    return True


def _drive_service(token):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=Credentials(token=token))


def _extract_folder_id(link: str) -> str:
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", link or "")
    return m.group(1) if m else ""


def _make_public(drive, file_id: str):
    try:
        drive.permissions().create(
            fileId=file_id,
            supportsAllDrives=True,
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except Exception:
        pass


def _list_png_links_from_version_folder(version_folder_id: str, token: str):
    drive = _drive_service(token)
    q = (
        f"'{version_folder_id}' in parents and trashed=false and "
        f"mimeType='application/vnd.google-apps.folder' and name='png'"
    )
    res = drive.files().list(
        q=q,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id,name)",
    ).execute().get("files", [])
    if not res:
        return []
    png_folder_id = res[0]["id"]

    files = drive.files().list(
        q=f"'{png_folder_id}' in parents and trashed=false and name contains '.png'",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        fields="files(id,name)",
        orderBy="name",
    ).execute().get("files", [])

    # Prefer black variant sequence when present.
    black = [f for f in files if "black_" in f.get("name", "").lower()]
    chosen = black if black else files

    slides = []
    for f in chosen:
        _make_public(drive, f["id"])
        url = f"https://drive.google.com/uc?export=download&id={f['id']}"
        slides.append({"name": f["name"], "url": url})
    return slides


def make_cover_thumbnails_public(folder_id, token):
    """Compatibility helper used by main/approval. folder_id should be png/ folder."""
    drive = _drive_service(token)
    files = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false and name contains '_01_cover'",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id,name)",
    ).execute().get("files", [])

    urls = {}
    for f in files:
        _make_public(drive, f["id"])
        name = f["name"].lower()
        variant = "black" if "black" in name else "cream" if "cream" in name else "lime"
        urls[variant] = f"https://drive.google.com/uc?id={f['id']}&export=download"
    return urls


def _build_one_carousel_html(post: dict, slides: list[dict]) -> str:
    topic = post.get("topic", "Untitled")
    niche = (post.get("niche") or "opc").upper()
    post_id = post.get("post_id", "")
    static_link = post.get("static_link", "")
    motion_link = post.get("motion_link", "")
    reel_link   = post.get("reel_link", "")
    folder_id = _extract_folder_id(static_link)
    clip_failures = post.get("clip_failures") or {}
    reviewer_issues = post.get("_review_issues") or []
    generation_trace = post.get("_generation_trace") or {}     # SH-147
    storytelling_scores = post.get("_storytelling_scores") or {}  # SH-147
    # FIX 3: caption data
    caption_body = post.get("caption", "")
    in_post_hashtags = post.get("in_post_hashtags", "")
    first_comment_hashtags = post.get("first_comment_hashtags", "")

    # SH-147: pre-compute story quality + model trace blocks (avoids backslash-in-fstring issue)
    if storytelling_scores:
        _st_overall = storytelling_scores.get("overall", "?")
        _st_summary = storytelling_scores.get("summary", "")[:160]
        _cb_found = storytelling_scores.get("closing_callback_found")
        _cb_text = str(storytelling_scores.get("closing_callback_text") or "")[:160]
        if _cb_found is True:
            _cb_line = f'Closing callback found: "{_cb_text}"'
        elif _cb_found is False:
            _cb_line = "CLOSING CALLBACK MISSING"
        else:
            _cb_line = "Closing callback found: unknown"
        _st_slide_lines = "<br/>".join(
            "· slide " + str(s.get("slide", "?")) + ": " + str(s.get("score", "?")) + "/100 — " + str(s.get("reason", ""))[:80]
            for s in (storytelling_scores.get("slide_scores") or [])[:8]
        )
        _story_quality_block = (
            f'<div style="background:#0d1a0a;border-left:3px solid #86efac;padding:14px 16px;margin-top:20px;border-radius:4px;">'
            f'<div style="font-family:Arial,sans-serif;color:#86efac;font-size:13px;font-weight:700;">&#128214; STORY QUALITY &#8212; {_st_overall} / 100</div>'
            f'<div style="font-family:Arial,sans-serif;color:#d0d0d0;font-size:13px;line-height:1.6;margin-top:8px;">{_st_summary}</div>'
            f'<div style="font-family:monospace;color:#86efac;font-size:12px;line-height:1.8;margin-top:8px;">{_cb_line}</div>'
            f'<div style="font-family:monospace;color:#aaaaaa;font-size:12px;line-height:1.8;margin-top:8px;">{_st_slide_lines}</div>'
            f'</div>'
        )
    else:
        _story_quality_block = ""

    if generation_trace:
        _gt_provider = generation_trace.get("provider", "unknown")
        _gt_model = generation_trace.get("model", "unknown")
        _gt_fallback = "yes &#x26A0;&#xFE0F;" if generation_trace.get("fallback_used") else "no &#x2713;"
        _model_trace_block = (
            f'<div style="background:#1a1005;border-left:3px solid #f59e0b;padding:14px 16px;margin-top:20px;border-radius:4px;">'
            f'<div style="font-family:Arial,sans-serif;color:#fbbf24;font-size:13px;font-weight:700;">&#9881;&#65039; MODEL / BUILD TRACE</div>'
            f'<div style="font-family:monospace;color:#d0d0d0;font-size:12px;line-height:1.8;margin-top:8px;">'
            f'generated_by: {_gt_provider}<br/>model: {_gt_model}<br/>fallback_used: {_gt_fallback}'
            f'</div></div>'
        )
    else:
        _model_trace_block = ""

    blocks = []
    for i, s in enumerate(slides, start=1):
        caption = s.get("name", f"slide_{i}.png")
        blocks.append(
            f"""
            <div style=\"margin:0 0 16px 0;\">
              <img src=\"{s['url']}\" width=\"540\" style=\"display:block;width:540px;max-width:100%;border-radius:8px;border:1px solid #2a2a2a;\" />
              <div style=\"font-family:Arial,sans-serif;color:#aaaaaa;font-size:12px;line-height:1.4;margin-top:6px;\">{caption}</div>
            </div>
            """
        )

    return f"""<html><body style="background:#0a0a0a;padding:24px;">
      <div style="max-width:620px;margin:0 auto;">
        <h2 style="font-family:Arial,sans-serif;color:#CBCC10;margin:0 0 6px 0;">[{niche}] {topic}</h2>
        <div style="font-family:Arial,sans-serif;color:#c7c7c7;font-size:13px;margin-bottom:16px;">
          Post ID: <b>{post_id}</b><br/>
          Folder ID: <b>{folder_id}</b><br/>
          Static: <a href="{static_link}" style="color:#CBCC10;">open folder</a> ·
          Motion: <a href="{motion_link}" style="color:#CBCC10;">open folder</a>{f' · Reel: <a href="{reel_link}" style="color:#CBCC10;">watch</a>' if reel_link else ''}
        </div>
        {''.join(blocks)}

        {f'''
        <div style="background:#1a0000;border-left:3px solid #ff4444;padding:14px 16px;margin-top:20px;border-radius:4px;">
          <div style="font-family:Arial,sans-serif;color:#ff6666;font-size:13px;font-weight:700;">⚠️ CLIPS INDISPONÍVEIS — {len(clip_failures)} slot(s) falharam</div>
          <div style="font-family:monospace;color:#ff9999;font-size:12px;line-height:1.7;margin-top:8px;">
            {"<br/>".join(f"Slide {idx}: resources/clips/{slot}.mp4 — adicione manualmente" for idx, slot in clip_failures.items())}
          </div>
        </div>''' if clip_failures else ''}

        {f'''
        <div style="background:#0d1a0d;border-left:3px solid #4ade80;padding:14px 16px;margin-top:20px;border-radius:4px;">
          <div style="font-family:Arial,sans-serif;color:#4ade80;font-size:13px;font-weight:700;">📝 INSTAGRAM CAPTION</div>
          <div style="font-family:Arial,sans-serif;color:#d0d0d0;font-size:14px;line-height:1.6;margin-top:10px;white-space:pre-wrap;">{caption_body}</div>
          {f'<div style="font-family:Arial,sans-serif;color:#aaaaaa;font-size:13px;margin-top:10px;">{in_post_hashtags}</div>' if in_post_hashtags else ''}
          {f'<div style="margin-top:12px;"><div style="font-family:Arial,sans-serif;color:#4ade80;font-size:12px;font-weight:700;">First comment hashtags:</div><div style="font-family:monospace;color:#aaaaaa;font-size:12px;line-height:1.6;margin-top:4px;white-space:pre-wrap;">{first_comment_hashtags}</div></div>' if first_comment_hashtags else ''}
        </div>''' if caption_body else ''}

        {f'''
        <div style="background:#0d0d1a;border-left:3px solid #7c83fd;padding:14px 16px;margin-top:20px;border-radius:4px;">
          <div style="font-family:Arial,sans-serif;color:#a5aaff;font-size:13px;font-weight:700;">🔍 REVIEWER NOTES ({len(reviewer_issues)})</div>
          <div style="font-family:monospace;color:#c8caff;font-size:12px;line-height:1.9;margin-top:8px;">
            {"<br/>".join("· " + issue for issue in reviewer_issues[:12])}
            {("<br/>· ... +" + str(len(reviewer_issues)-12) + " more") if len(reviewer_issues) > 12 else ""}
          </div>
        </div>''' if reviewer_issues else ''}

        {_story_quality_block}

        {_model_trace_block}

        <div style="background:#111;border-left:3px solid #CBCC10;padding:14px 16px;margin-top:20px;border-radius:4px;">
          <div style="font-family:Arial,sans-serif;color:#CBCC10;font-size:13px;font-weight:700;">Reply commands</div>
          <div style="font-family:Arial,sans-serif;color:#d0d0d0;font-size:13px;line-height:1.55;margin-top:8px;">
            APPROVE<br/>
            NOT GOOD - your feedback prompt<br/>
            REJECT - your feedback prompt
          </div>
        </div>
      </div>
    </body></html>"""


def send_preview(posts, date_str):
    """Send one email per carousel with full slide stack for precise reply targeting."""
    token, _ = get_token()
    sent = 0
    for post in posts:
        folder_id = _extract_folder_id(post.get("static_link", ""))
        if not folder_id:
            print(f"  No static folder ID for {post.get('post_id','?')} — skip preview")
            continue

        slides = _list_png_links_from_version_folder(folder_id, token)
        if not slides:
            print(f"  No PNG slides found for {post.get('post_id','?')} — skip preview")
            continue

        slug = (post.get("post_id") or "carousel").strip()
        niche_label = (post.get("niche") or "opc").upper()
        subject = (
            f"[REVIEW] {niche_label} — {slug} — {len(slides)} slides — "
            f"FOLDER:{folder_id} — reply APPROVE or feedback"
        )
        html = _build_one_carousel_html(post, slides)

        gmail_user = "priscila@oakpark-construction.com"
        gmail_pass = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")

        if gmail_pass:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = gmail_user
            msg["To"] = gmail_user
            msg["X-Content-Post-ID"] = slug
            msg["X-Content-Folder-ID"] = folder_id
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, gmail_user, msg.as_string())
            print(f"  Preview email sent: {subject}")
            sent += 1
        else:
            _send_via_workflow(subject, html)
            sent += 1
    return sent > 0


def update_catalog_status(post_id, status="pending_approval"):
    token, _ = get_token()
    enc = urllib.parse.quote(f"'{CATALOG_TAB}'!A:M", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(urllib.request.urlopen(req).read()).get("values", [])

    for i, row in enumerate(rows):
        if len(row) > 0 and row[0].strip() == post_id:
            cell = f"'{CATALOG_TAB}'!M{i+1}"
            enc2 = urllib.parse.quote(cell, safe="!:'")
            url2 = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc2}?valueInputOption=USER_ENTERED"
            payload = json.dumps({"values": [[status]]}).encode()
            req2 = urllib.request.Request(
                url2,
                data=payload,
                method="PUT",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req2)
            print(f"  Catalog updated: {post_id} → {status}")
            return
