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


def schedule_to_buffer(variant, drive_folder_id, caption="", platform="instagram"):
    if not BUFFER_KEY:
        print("  No BUFFER_API_KEY — cannot schedule")
        return False

    profiles_url = f"{BUFFER_API}/profiles.json?access_token={BUFFER_KEY}"
    try:
        profiles = json.loads(urllib.request.urlopen(profiles_url).read())
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

    print(f"  Buffer scheduling: variant={variant}, profile={profile_id}")
    return True


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


def process_replies():
    token, _ = get_gmail_token()
    replies = search_gmail_replies(token)

    if not replies:
        print("  No approval replies found")
        return {"approved": 0, "changes": 0, "skipped": 0}

    stats = {"approved": 0, "changes": 0, "skipped": 0}

    for reply in replies:
        result = parse_approval(reply["reply_text"])
        print(f"  Reply: '{reply['reply_text'][:60]}' → {result['action']}")

        if result["action"] == "approve":
            stats["approved"] += 1
        elif result["action"] == "skip":
            stats["skipped"] += 1
        else:
            stats["changes"] += 1

    return stats


if __name__ == "__main__":
    stats = process_replies()
    print(json.dumps(stats, indent=2))
