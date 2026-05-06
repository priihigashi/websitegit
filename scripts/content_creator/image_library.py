#!/usr/bin/env python3
import io
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

from image_providers import build_scene_lock_prompt, regenerate_from_feedback, log_failure

SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
TAB_NAME = os.environ.get("IMAGE_LIBRARY_TAB", "🖼️ Image Library Log")

HEADERS = [
    "IMAGE_ID", "DRIVE_URL", "LOCAL_PATH", "TOPIC_KEYWORDS",
    "NICHE", "USED_IN_POSTS", "DATE_ADDED",
]


def _oauth_token() -> str:
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
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data),
        timeout=15,
    ).read())
    return resp["access_token"]


def _sheet_get(range_a1: str):
    tok = _oauth_token()
    enc = urllib.parse.quote(range_a1, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("values", [])


def _sheet_append(range_a1: str, rows: list[list[str]]):
    tok = _oauth_token()
    enc = urllib.parse.quote(range_a1, safe="!:'")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}:append"
        f"?valueInputOption=USER_ENTERED"
    )
    body = json.dumps({"values": rows}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    urllib.request.urlopen(req, timeout=15)


def _sheet_batch_update(data_ranges: list[dict]):
    tok = _oauth_token()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
    body = json.dumps({"valueInputOption": "USER_ENTERED", "data": data_ranges}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    urllib.request.urlopen(req, timeout=15)


def ensure_tab():
    try:
        rows = _sheet_get(f"'{TAB_NAME}'!1:1")
        if rows and rows[0]:
            return
    except Exception:
        pass
    try:
        tok = _oauth_token()
        add_url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate"
        payload = {
            "requests": [{"addSheet": {"properties": {"title": TAB_NAME[:100]}}}]
        }
        req = urllib.request.Request(
            add_url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req)
    except Exception:
        pass
    _sheet_batch_update([{"range": f"'{TAB_NAME}'!A1:G1", "values": [HEADERS]}])


def _norm_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def search_library(keywords: str, niche: str):
    """Return dict with best match {drive_url,image_id,topic_keywords,row_idx} or None."""
    ensure_tab()
    rows = _sheet_get(f"'{TAB_NAME}'!A2:G")
    if not rows:
        return None
    q = _norm_tokens(keywords)
    best = None
    best_score = 0
    for i, r in enumerate(rows, start=2):
        image_id = r[0].strip() if len(r) > 0 else ""
        drive_url = r[1].strip() if len(r) > 1 else ""
        topic_kw = r[3].strip() if len(r) > 3 else ""
        row_niche = r[4].strip().lower() if len(r) > 4 else ""
        if not image_id and not drive_url:
            continue
        if row_niche and niche and row_niche != niche.lower():
            continue
        t = _norm_tokens(topic_kw)
        overlap = len(q.intersection(t))
        if overlap > best_score:
            best_score = overlap
            best = {
                "image_id": image_id,
                "drive_url": drive_url,
                "topic_keywords": topic_kw,
                "row_idx": i,
            }
    return best if best and best_score >= 2 else None


def _drive_service():
    raw = os.environ.get("SHEETS_TOKEN", "")
    td = json.loads(raw)
    tok = _oauth_token()
    creds = Credentials(
        token=tok,
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )
    return build("drive", "v3", credentials=creds)


def _file_id_from_url(url: str) -> str:
    m = re.search(r"id=([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/d/([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/file/d/([a-zA-Z0-9_-]{20,})", url)
    return m.group(1) if m else ""


def enhance_library_image(drive_url: str, work_dir: str, filename: str, slide_text: str) -> str:
    """Download image from library and apply scene-preservation enhancement."""
    drive = _drive_service()
    file_id = _file_id_from_url(drive_url) or ""
    if not file_id:
        raise RuntimeError(f"Invalid Drive URL for library image: {drive_url}")

    src = Path(work_dir) / "resources" / "images" / f"lib_src_{filename}"
    src.parent.mkdir(parents=True, exist_ok=True)
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    src.write_bytes(buf.getvalue())

    prompt = build_scene_lock_prompt(
        f"This is a photo of {slide_text}. Keep everything in place. Only adjust lighting, clarity, and realism.",
        subject_hint="opc construction photo",
        is_opc=True,
    )
    out_rel = regenerate_from_feedback(prompt, str(src), work_dir, filename)
    if out_rel:
        return out_rel
    return f"resources/images/{src.name}"


def log_new_image(drive_url: str, topic_keywords: str, niche: str, post_id: str):
    ensure_tab()
    image_id = _file_id_from_url(drive_url)
    row = [
        image_id,
        drive_url,
        "",
        topic_keywords,
        niche,
        post_id,
        datetime.utcnow().strftime("%Y-%m-%d"),
    ]
    try:
        _sheet_append(f"'{TAB_NAME}'!A:G", [row])
    except Exception as e:
        log_failure("image_library/log_new_image", e)


def mark_used(row_idx: int, post_id: str):
    try:
        val = _sheet_get(f"'{TAB_NAME}'!F{row_idx}:F{row_idx}")
        prev = val[0][0] if val and val[0] else ""
        merged = f"{prev}, {post_id}".strip(", ") if prev else post_id
        _sheet_batch_update([{"range": f"'{TAB_NAME}'!F{row_idx}", "values": [[merged]]}])
    except Exception:
        pass
