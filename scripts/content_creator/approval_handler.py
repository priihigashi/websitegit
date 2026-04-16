#!/usr/bin/env python3
"""
approval_handler.py — Polls Gmail for replies to preview emails.
Called by 4AM agent to check for approvals or change requests.

Handles:
  - "black approved" → schedule OPC to Buffer + copy to Ready to Post
  - "cream approved" / "lime approved" → same with that variant
  - "skip" → mark skipped in catalog
  - anything else → treat as change request, flag for next content_creator run
"""
import json, os, re, time, urllib.request, urllib.parse
from datetime import datetime, timedelta
import pytz

ET = pytz.timezone("America/New_York")

SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
CATALOG_TAB = "📸 Project Content Catalog"
BUFFER_KEY = os.environ.get("BUFFER_API_KEY", "")
BUFFER_API = "https://api.bufferapp.com/1"

# Drive folder IDs
READY_TO_POST_OPC = ""  # Created on first use
READY_TO_POST_BRAZIL = ""


def get_gmail_token():
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    return resp["access_token"], td


def search_gmail_replies(token, after_date=None):
    if not after_date:
        after_date = (datetime.now(ET) - timedelta(days=1)).strftime("%Y/%m/%d")

    query = urllib.parse.quote(f'subject:"DAILY CONTENT" after:{after_date} in:inbox')
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={query}&maxResults=10"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})

    try:
        resp = json.loads(urllib.request.urlopen(req).read())
    except Exception as e:
        print(f"  Gmail search error: {e}")
        return []

    messages = resp.get("messages", [])
    results = []

    for msg in messages:
        msg_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=full"
        req2 = urllib.request.Request(msg_url, headers={"Authorization": f"Bearer {token}"})
        try:
            detail = json.loads(urllib.request.urlopen(req2).read())
        except Exception:
            continue

        headers = {h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])}

        if "re:" not in headers.get("subject", "").lower():
            continue

        body = _extract_body(detail.get("payload", {}))
        if not body:
            continue

        reply_text = _clean_reply(body)
        if not reply_text:
            continue

        results.append({
            "message_id": msg["id"],
            "thread_id": detail.get("threadId", ""),
            "subject": headers.get("subject", ""),
            "reply_text": reply_text,
            "date": headers.get("date", ""),
        })

    return results


def _extract_body(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        import base64
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return ""


def _clean_reply(text):
    lines = []
    for line in text.split("\n"):
        if line.strip().startswith(">") or line.strip().startswith("On ") and "wrote:" in line:
            break
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
    return " ".join(lines).strip()


def parse_approval(reply_text):
    text = reply_text.lower().strip()

    if text == "skip":
        return {"action": "skip"}

    approved_match = re.match(r'^(black|cream|lime)\s+approved?$', text)
    if approved_match:
        return {"action": "approve", "variant": approved_match.group(1)}

    if "approved" in text or "approve" in text:
        for v in ["black", "cream", "lime"]:
            if v in text:
                return {"action": "approve", "variant": v}

    return {"action": "change", "feedback": reply_text}


def _get_drive_service():
    token, td = get_gmail_token()
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=token, refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"], client_secret=td["client_secret"],
    )
    return build("drive", "v3", credentials=creds)


def _get_variant_image_urls(drive, folder_id, variant):
    files = drive.files().list(
        q=f"'{folder_id}' in parents and name contains '{variant}_' and trashed=false",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id,name)", orderBy="name",
    ).execute().get("files", [])

    urls = []
    for f in files:
        if not f["name"].lower().endswith(".png"):
            continue
        try:
            drive.permissions().create(
                fileId=f["id"], supportsAllDrives=True,
                body={"type": "anyone", "role": "reader"},
            ).execute()
        except Exception:
            pass
        urls.append(f"https://drive.google.com/uc?export=download&id={f['id']}")
    return urls


def schedule_to_buffer(variant, drive_folder_id, caption="", platform="instagram"):
    if not BUFFER_KEY:
        print("  No BUFFER_API_KEY — cannot schedule")
        return False

    profiles_url = f"{BUFFER_API}/profiles.json?access_token={BUFFER_KEY}"
    try:
        profiles = json.loads(urllib.request.urlopen(profiles_url, timeout=15).read())
    except Exception as e:
        print(f"  Buffer profiles error: {e}")
        return False

    profile_id = None
    for p in profiles:
        if platform.lower() in p.get("service", "").lower():
            profile_id = p["id"]
            break

    if not profile_id:
        print(f"  No Buffer profile for {platform}")
        return False

    drive = _get_drive_service()
    image_urls = _get_variant_image_urls(drive, drive_folder_id, variant)
    if not image_urls:
        print(f"  No {variant} images found in Drive folder {drive_folder_id}")
        return False

    params = [
        ("access_token", BUFFER_KEY),
        ("text", caption),
        ("now", "false"),
    ]
    params.append(("profile_ids[]", profile_id))

    if len(image_urls) == 1:
        params.append(("media[picture]", image_urls[0]))
    else:
        for url in image_urls[:10]:
            params.append(("media[photos][]", url))

    payload = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{BUFFER_API}/updates/create.json",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        if resp.get("success") or resp.get("id") or "updates" in resp:
            print(f"  Buffer scheduled: {variant} ({len(image_urls)} slides)")
            return True
        print(f"  Buffer rejected: {resp}")
        return False
    except Exception as e:
        print(f"  Buffer error: {e}")
        return False


def copy_to_ready_folder(variant, source_folder_id, niche):
    token, td = get_gmail_token()
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=token,
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )
    drive = build("drive", "v3", credentials=creds)

    files = drive.files().list(
        q=f"'{source_folder_id}' in parents and name contains '{variant}_' and trashed=false",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id,name)",
    ).execute().get("files", [])

    ready_folder = _ensure_ready_folder(drive, niche)
    copied = 0
    for f in files:
        drive.files().copy(
            fileId=f["id"],
            body={"name": f["name"], "parents": [ready_folder]},
            supportsAllDrives=True,
        ).execute()
        copied += 1

    print(f"  Copied {copied} {variant} files to Ready to Post/{niche}")
    return copied


def _ensure_ready_folder(drive, niche):
    MARKETING_DRIVE = "0AIPzwsJD_qqzUk9PVA"
    OPC_TEMPLATES = "1HHQGPM3iOP6m1pdUnAKtpRXfBi1ejEvZ"

    res = drive.files().list(
        q=f"name='Ready to Post' and '{OPC_TEMPLATES}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id)",
    ).execute()

    if res.get("files"):
        return res["files"][0]["id"]

    folder = drive.files().create(
        body={"name": "Ready to Post", "mimeType": "application/vnd.google-apps.folder", "parents": [OPC_TEMPLATES]},
        supportsAllDrives=True, fields="id",
    ).execute()
    print(f"  Created Ready to Post folder: {folder['id']}")
    return folder["id"]


def update_catalog(post_id, status, variant=None):
    token, _ = get_gmail_token()
    enc = urllib.parse.quote(f"'{CATALOG_TAB}'!A:O", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(urllib.request.urlopen(req).read()).get("values", [])

    for i, row in enumerate(rows):
        if len(row) > 0 and row[0].strip() == post_id:
            updates = [[status]]
            cell = f"'{CATALOG_TAB}'!M{i+1}"
            enc2 = urllib.parse.quote(cell, safe="!:'")
            url2 = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc2}?valueInputOption=USER_ENTERED"
            payload = json.dumps({"values": updates}).encode()
            req2 = urllib.request.Request(url2, data=payload, method="PUT",
                                         headers={"Authorization": f"Bearer {token}",
                                                   "Content-Type": "application/json"})
            urllib.request.urlopen(req2)
            print(f"  Catalog: {post_id} → {status}")
            return


def re_render_post(post, feedback):
    """Re-render a post with feedback. Creates v{n+1} folder in same parent as current static folder."""
    import sys, shutil
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from carousel_builder import generate_carousel_content, build_html, render_pngs
    from googleapiclient.http import MediaFileUpload

    post_id = post["post_id"]
    niche = post["niche"]
    topic = post["topic"]

    folder_id_match = re.search(r'/folders/([a-zA-Z0-9_-]+)', post["static_link"])
    if not folder_id_match:
        print(f"  re_render: cannot parse folder ID from {post['static_link']}")
        return False
    current_folder_id = folder_id_match.group(1)

    drive = _get_drive_service()
    try:
        folder_meta = drive.files().get(
            fileId=current_folder_id, supportsAllDrives=True, fields="name,parents",
        ).execute()
    except Exception as e:
        print(f"  re_render: Drive lookup failed: {e}")
        return False

    folder_name = folder_meta.get("name", "")
    parents = folder_meta.get("parents", [])
    if not parents:
        print(f"  re_render: no parent for folder {current_folder_id}")
        return False
    parent_id = parents[0]

    ver_match = re.search(r'_v(\d+)_static$', folder_name)
    current_ver = int(ver_match.group(1)) if ver_match else 1
    new_ver = current_ver + 1
    new_folder_name = re.sub(r'_v\d+_static$', f'_v{new_ver}_static', folder_name)
    if new_folder_name == folder_name:
        new_folder_name = f"{post_id}_v{new_ver}_static"

    topic_with_feedback = f"{topic}\n\nRevision feedback: {feedback}"
    content = generate_carousel_content(topic_with_feedback, niche)
    if not content:
        print(f"  re_render: content generation failed")
        return False

    work = Path(f"/tmp/rerender_{post_id}_v{new_ver}")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    slug = post_id.replace("opc-tip-", "").replace("brazil-", "")[:30]
    html_path = build_html(content, niche, slug, str(work))
    if not html_path:
        print(f"  re_render: HTML build failed")
        shutil.rmtree(work, ignore_errors=True)
        return False

    png_dir = work / "png"
    export_script = os.environ.get("EXPORT_SCRIPT", str(Path(__file__).parent / "export_variants.js"))
    os.environ["EXPORT_SCRIPT"] = export_script
    if not render_pngs(html_path, str(png_dir)):
        print(f"  re_render: PNG render failed")
        shutil.rmtree(work, ignore_errors=True)
        return False

    new_folder = drive.files().create(
        body={"name": new_folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        supportsAllDrives=True, fields="id",
    ).execute()
    new_folder_id = new_folder["id"]

    for f in sorted(png_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            drive.files().create(
                body={"name": f.name, "parents": [new_folder_id]},
                media_body=MediaFileUpload(str(f), mimetype="image/png"),
                supportsAllDrives=True, fields="id",
            ).execute()

    new_static_link = f"https://drive.google.com/drive/folders/{new_folder_id}"

    token, _ = get_gmail_token()
    enc = urllib.parse.quote(f"'{CATALOG_TAB}'!A:O", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(urllib.request.urlopen(req).read()).get("values", [])
    for i, row in enumerate(rows):
        if len(row) > 0 and row[0].strip() == post_id:
            row_num = i + 1
            batch_url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
            batch_payload = json.dumps({
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": f"'{CATALOG_TAB}'!I{row_num}", "values": [[new_static_link]]},
                    {"range": f"'{CATALOG_TAB}'!M{row_num}", "values": [["in_review"]]},
                ],
            }).encode()
            req2 = urllib.request.Request(batch_url, data=batch_payload,
                                          headers={"Authorization": f"Bearer {token}",
                                                    "Content-Type": "application/json"})
            urllib.request.urlopen(req2)
            print(f"  Catalog updated: {post_id} → in_review, v{new_ver} static link")
            break

    from email_preview import send_preview, make_cover_thumbnails_public
    cover_urls = make_cover_thumbnails_public(new_folder_id, token)
    post_updated = dict(post)
    post_updated["static_link"] = new_static_link
    post_updated["static_folder_id"] = new_folder_id
    post_updated["cover_urls"] = cover_urls
    send_preview([post_updated], datetime.now(ET).strftime("%Y-%m-%d"))

    shutil.rmtree(work, ignore_errors=True)
    print(f"  Re-render done: {post_id} v{new_ver} → {new_static_link}")
    return True


def _delete_old_versions(post_id, approved_folder_id):
    """Delete all v* static/motion folders for post_id except the approved one."""
    drive = _get_drive_service()
    try:
        meta = drive.files().get(
            fileId=approved_folder_id, supportsAllDrives=True, fields="parents",
        ).execute()
    except Exception:
        return
    parents = meta.get("parents", [])
    if not parents:
        return
    parent_id = parents[0]

    folders = drive.files().list(
        q=f"'{parent_id}' in parents and name contains '{post_id}_v' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        fields="files(id,name)",
    ).execute().get("files", [])

    for f in folders:
        if f["id"] == approved_folder_id:
            continue
        if "_static" not in f["name"] and "_motion" not in f["name"]:
            continue
        try:
            drive.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
            print(f"  Deleted old version: {f['name']}")
        except Exception as e:
            print(f"  Could not delete {f['name']}: {e}")


def _get_pending_posts():
    token, _ = get_gmail_token()
    enc = urllib.parse.quote(f"'{CATALOG_TAB}'!A:O", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    rows = json.loads(urllib.request.urlopen(req).read()).get("values", [])
    if len(rows) < 2:
        return []

    header = rows[0]
    header_map = {h.strip().lower(): i for i, h in enumerate(header)}
    pending = []
    for row in rows[1:]:
        def v(name):
            idx = header_map.get(name.lower())
            return row[idx].strip() if idx is not None and idx < len(row) else ""
        if v("status") == "pending_approval":
            pending.append({
                "post_id": v("post_id") or (row[0] if len(row) > 0 else ""),
                "niche": v("niche") or (row[1] if len(row) > 1 else ""),
                "static_link": v("static folder") or (row[8] if len(row) > 8 else ""),
                "motion_link": v("motion folder") or (row[9] if len(row) > 9 else ""),
                "topic": v("topic") or (row[13] if len(row) > 13 else ""),
            })
    return pending


def process_replies():
    token, _ = get_gmail_token()
    replies = search_gmail_replies(token)

    if not replies:
        print("  No approval replies found")
        return {"approved": 0, "changes": 0, "skipped": 0}

    pending = _get_pending_posts()
    if not pending:
        print("  No pending_approval posts in catalog")
        return {"approved": 0, "changes": 0, "skipped": 0}

    stats = {"approved": 0, "changes": 0, "skipped": 0}

    for reply in replies:
        result = parse_approval(reply["reply_text"])
        print(f"  Reply: '{reply['reply_text'][:60]}' → {result['action']}")

        if result["action"] == "approve":
            variant = result["variant"]
            for post in pending:
                post_id = post["post_id"]
                niche = post["niche"]
                static_folder_id = re.search(r'/folders/([a-zA-Z0-9_-]+)', post["static_link"])
                static_folder_id = static_folder_id.group(1) if static_folder_id else ""

                if not static_folder_id:
                    print(f"  No static folder ID for {post_id} — skipping")
                    continue

                if niche == "opc" and BUFFER_KEY:
                    caption = post.get("topic", "")
                    schedule_to_buffer(variant, static_folder_id, caption=caption)

                copy_to_ready_folder(variant, static_folder_id, niche)
                update_catalog(post_id, "approved")
                _delete_old_versions(post_id, static_folder_id)
                print(f"  Approved: {post_id} ({variant})")

            stats["approved"] += 1

        elif result["action"] == "skip":
            for post in pending:
                update_catalog(post["post_id"], "skipped")
            stats["skipped"] += 1

        else:
            feedback = result.get("feedback", "")
            stats["changes"] += 1
            print(f"  Change requested: {feedback[:80]}")
            for post in pending:
                if re_render_post(post, feedback):
                    print(f"  Re-render triggered: {post['post_id']}")
                else:
                    print(f"  Re-render failed: {post['post_id']}")

    return stats


if __name__ == "__main__":
    stats = process_replies()
    print(json.dumps(stats, indent=2))
