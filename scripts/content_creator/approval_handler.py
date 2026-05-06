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
import json, os, re, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
import pytz

ET = pytz.timezone("America/New_York")
APPROVAL_REMINDER_SUBJECT = "⏰ Content approvals pending"
APPROVAL_REMINDER_SUBJECTS = {
    "opc": "⏰ OPC content approvals pending",
    "news": "⏰ News content approvals pending",
    "other": "⏰ Other content approvals pending",
}

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

    # SH-104 subject line is `[SH-104] Evidence manifest ready — <person> — <niche>`.
    # Reply preserves the bracket header so we match Re: subject:"[SH-104]".
    query = urllib.parse.quote(
        '(subject:"Re: [REVIEW]" OR subject:"Re: DAILY CONTENT" '
        'OR subject:"Re: [SH-104]") after:' + after_date
    )
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={query}&maxResults=20"
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


def is_sh104_reply(subject: str) -> bool:
    """SH-104 manifest emails carry [SH-104] in the subject line. Replies
    keep the bracket prefix (Gmail standard). Detect to route to a
    different parser than the OPC/News carousel preview."""
    s = (subject or "")
    return "[SH-104]" in s or "[sh-104]" in s.lower()


# Recognized reply tokens for SH-104 manifest emails. Tokens are case-
# insensitive, must match the line as a whole (after subject-line stripping).
_SH104_TOKENS = {
    "APPROVE MANIFEST":     "approve_manifest",
    "RENDER CAROUSEL":      "render_carousel",
    "RENDER REMOTION":      "render_remotion",
    "NEEDS MORE EVIDENCE":  "needs_more_evidence",
    "REJECT MANIFEST":      "reject_manifest",
    # Additional tokens emitted by clipmine_render.yml preview email
    "APPROVE PREVIEW":      "approve_preview",
    "CHANGE":               "render_change",
    "REJECT":               "render_reject",
}


def parse_sh104_reply(reply_text: str) -> dict:
    """Parse SH-104 / clipmine_render reply into a routable action.

    Returns:
      {"sh104": True,
       "action": "approve_manifest|render_carousel|...|unknown",
       "raw_token": "<original token text>",
       "feedback": "<freeform text below the token, if any>"}
    """
    out = {"sh104": True, "action": "unknown", "raw_token": "", "feedback": ""}
    text = (reply_text or "").strip()
    if not text:
        return out
    # First non-quoted line is the token line; everything below = feedback.
    first_line = text.split("\n", 1)[0].strip()
    rest = text[len(first_line):].strip()
    upper = first_line.upper().strip(" .!?:")
    for token, action in _SH104_TOKENS.items():
        if upper == token or upper.startswith(token):
            out["action"] = action
            out["raw_token"] = token
            extra = upper[len(token):].strip(" -:.\n\t")
            out["feedback"] = (rest or extra).strip()
            return out
    return out


def _gh_dispatch_clipmine_render(manifest_url: str, mode: str, niche: str = "brazil") -> bool:
    """Trigger clipmine_render.yml via gh CLI."""
    import shutil, subprocess
    gh = shutil.which("gh") or os.path.expanduser("~/bin/gh")
    if not os.path.exists(gh):
        return False
    try:
        r = subprocess.run(
            [gh, "workflow", "run", "clipmine_render.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", f"manifest_url={manifest_url}",
             "-f", f"mode={mode}",
             "-f", f"niche={niche}"],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"  clipmine_render dispatch failed: {e}")
        return False


def _gh_dispatch_video_research_retry(seed_url: str, person_name: str,
                                       requirement: str, target_count: int,
                                       niche: str) -> bool:
    """Trigger video-research.yml again with broader query intent."""
    import shutil, subprocess
    gh = shutil.which("gh") or os.path.expanduser("~/bin/gh")
    if not os.path.exists(gh):
        return False
    try:
        r = subprocess.run(
            [gh, "workflow", "run", "video-research.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", "mode=person_evidence_mining",
             "-f", f"seed_url={seed_url}",
             "-f", f"person_name={person_name}",
             "-f", f"evidence_requirement={requirement}",
             "-f", f"target_clip_count={target_count}",
             "-f", f"niche={niche}"],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception as e:
        print(f"  video-research re-dispatch failed: {e}")
        return False


def _update_content_queue_status(person_name: str, niche: str, new_status: str) -> bool:
    """Update the SH-104 row in 📋 Content Queue (key: SOURCE+TITLE+NICHE)."""
    try:
        token, _ = get_gmail_token()
        tab = "📋 Content Queue"
        enc = urllib.parse.quote(f"'{tab}'!A:Z", safe="!:'")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        ).read()).get("values", [])
        if not rows:
            return False
        headers = rows[0]
        try:
            idx_source = headers.index("SOURCE")
            idx_title = headers.index("TITLE")
            idx_niche = headers.index("NICHE")
            idx_status = headers.index("STATUS")
        except ValueError:
            return False
        title_match = f"{person_name} — evidence clip set (Phase 1 manifest)"
        for ri, row in enumerate(rows[1:], start=2):
            def _cell(i): return row[i] if i < len(row) else ""
            if (_cell(idx_source).strip() == "person_evidence_mining"
                    and _cell(idx_title).strip() == title_match
                    and _cell(idx_niche).strip() == niche):
                col = chr(64 + idx_status + 1) if (idx_status + 1) <= 26 else None
                if not col:
                    return False
                rng = urllib.parse.quote(f"'{tab}'!{col}{ri}", safe="!:'")
                u = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
                     f"/values/{rng}?valueInputOption=USER_ENTERED")
                payload = json.dumps({"values": [[new_status]]}).encode()
                req = urllib.request.Request(u, data=payload, method="PUT",
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"})
                urllib.request.urlopen(req).read()
                print(f"  Content Queue: {person_name} → {new_status}")
                return True
    except Exception as exc:
        print(f"  Content Queue update failed: {exc}")
    return False


def _routing_capture_folder(niche: str) -> str:
    """Resolve niche capture parent via routing.py. Returns "" on any failure."""
    try:
        import sys
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).parent.parent))
        import routing
        return routing.capture_folder(niche) or routing.capture_folder("brazil") or ""
    except Exception as exc:
        print(f"  routing.capture_folder lookup failed: {exc}")
        return ""


def _find_latest_render_folder(drive, niche: str) -> dict:
    """Newest clipmine_render_* folder under the niche's Captures parent.
    Returns {"id": str, "name": str, "createdTime": str} or {} when none."""
    parent = _routing_capture_folder(niche)
    if not parent:
        return {}
    try:
        res = drive.files().list(
            q=(f"'{parent}' in parents and name contains 'clipmine_render_' "
               f"and mimeType='application/vnd.google-apps.folder' and trashed=false"),
            orderBy="createdTime desc",
            pageSize=10,
            fields="files(id,name,createdTime)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        return res[0] if res else {}
    except Exception as exc:
        print(f"  _find_latest_render_folder failed: {exc}")
        return {}


def _ensure_sh104_ready_folder(drive, niche: str) -> str:
    """Find or create 'Ready to Post — SH-104' under the niche's Captures parent.
    Distinct from OPC `Ready to Post` (which lives under OPC_TEMPLATES).
    Returns folder ID or "" on failure."""
    parent = _routing_capture_folder(niche)
    if not parent:
        return ""
    name = "Ready to Post — SH-104"
    try:
        res = drive.files().list(
            q=(f"'{parent}' in parents and name='{name}' "
               f"and mimeType='application/vnd.google-apps.folder' and trashed=false"),
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            fields="files(id)",
        ).execute().get("files", [])
        if res:
            return res[0]["id"]
        f = drive.files().create(
            body={"name": name,
                  "mimeType": "application/vnd.google-apps.folder",
                  "parents": [parent]},
            supportsAllDrives=True, fields="id",
        ).execute()
        return f["id"]
    except Exception as exc:
        print(f"  _ensure_sh104_ready_folder failed: {exc}")
        return ""


def _copy_sh104_render_to_ready(person_name: str, niche: str,
                                 render_folder_id: str = "") -> dict:
    """APPROVE PREVIEW side-effect — copy the latest clipmine_render_* contents
    into a per-render subfolder of `Ready to Post — SH-104`.

    Strategy:
      1. If render_folder_id explicitly provided, use it.
      2. Otherwise pick the newest clipmine_render_* folder for the niche
         (orderBy createdTime desc).
      3. Ensure the niche's `Ready to Post — SH-104` parent exists.
      4. Create per-render subfolder named `<person> — YYYYMMDD-HHMM`.
      5. Copy each non-folder file from render folder into that subfolder.

    Returns audit dict with concrete IDs + counts so the caller can write
    evidence-grade status updates.
    """
    audit = {
        "copied": 0, "skipped_folders": 0, "errors": [],
        "render_folder_id": render_folder_id, "render_folder_name": "",
        "ready_folder_id": "", "ready_subfolder_id": "",
        "ready_subfolder_name": "",
    }
    drive = _get_drive_service()

    if not render_folder_id:
        latest = _find_latest_render_folder(drive, niche)
        if not latest:
            audit["errors"].append("no_render_folder_found_for_niche")
            return audit
        audit["render_folder_id"] = latest["id"]
        audit["render_folder_name"] = latest.get("name", "")
        render_folder_id = latest["id"]
    else:
        try:
            meta = drive.files().get(
                fileId=render_folder_id, supportsAllDrives=True,
                fields="name",
            ).execute()
            audit["render_folder_name"] = meta.get("name", "")
        except Exception as exc:
            audit["errors"].append(f"render_meta: {exc}")

    ready_folder_id = _ensure_sh104_ready_folder(drive, niche)
    if not ready_folder_id:
        audit["errors"].append("could_not_create_ready_folder")
        return audit
    audit["ready_folder_id"] = ready_folder_id

    sub_name = f"{(person_name or 'unknown').strip()} — {datetime.now(ET).strftime('%Y%m%d-%H%M')}".strip(" —")
    audit["ready_subfolder_name"] = sub_name
    try:
        sub_id = drive.files().create(
            body={"name": sub_name,
                  "mimeType": "application/vnd.google-apps.folder",
                  "parents": [ready_folder_id]},
            supportsAllDrives=True, fields="id",
        ).execute()["id"]
        audit["ready_subfolder_id"] = sub_id
    except Exception as exc:
        audit["errors"].append(f"create_subfolder: {exc}")
        return audit

    try:
        files = drive.files().list(
            q=f"'{render_folder_id}' in parents and trashed=false",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            fields="files(id,name,mimeType)",
        ).execute().get("files", [])
    except Exception as exc:
        audit["errors"].append(f"list_render_files: {exc}")
        return audit

    for f in files:
        if f.get("mimeType") == "application/vnd.google-apps.folder":
            audit["skipped_folders"] += 1
            continue
        try:
            drive.files().copy(
                fileId=f["id"],
                body={"name": f["name"], "parents": [sub_id]},
                supportsAllDrives=True,
            ).execute()
            audit["copied"] += 1
        except Exception as exc:
            audit["errors"].append(f"{f['name']}: {exc}")
    return audit


def _find_manifest_url_for(person_name: str, niche: str) -> str:
    """Look up MANIFEST_URL in 📋 Content Queue by SOURCE+TITLE+NICHE."""
    try:
        token, _ = get_gmail_token()
        tab = "📋 Content Queue"
        enc = urllib.parse.quote(f"'{tab}'!A:Z", safe="!:'")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        ).read()).get("values", [])
        if not rows:
            return ""
        headers = rows[0]
        if "TITLE" not in headers or "MANIFEST_URL" not in headers:
            return ""
        idx_title = headers.index("TITLE")
        idx_man = headers.index("MANIFEST_URL")
        idx_niche = headers.index("NICHE") if "NICHE" in headers else None
        target = f"{person_name} — evidence clip set (Phase 1 manifest)"
        for row in rows[1:]:
            def _cell(i): return row[i] if i < len(row) else ""
            if _cell(idx_title).strip() == target:
                if idx_niche is None or _cell(idx_niche).strip() == niche:
                    return _cell(idx_man).strip()
    except Exception as exc:
        print(f"  manifest-url lookup failed: {exc}")
    return ""


def _handle_sh104_reply(sh: dict, meta: dict, reply: dict) -> bool:
    """Route a parsed SH-104 reply to the right side-effect.
    Returns True if the action was handled (regardless of dispatch success);
    False if the action was unknown / no-op."""
    action = sh["action"]
    person = meta.get("person_name", "")
    niche = meta.get("niche", "brazil")

    if action == "approve_manifest":
        return _update_content_queue_status(person, niche, "Approved — Manifest")

    if action == "render_carousel":
        manifest_url = _find_manifest_url_for(person, niche)
        if not manifest_url:
            print("  No MANIFEST_URL found in Content Queue — cannot dispatch render")
            return False
        if _gh_dispatch_clipmine_render(manifest_url, "carousel", niche):
            _update_content_queue_status(person, niche, "Rendering — Carousel")
            return True
        return False

    if action == "render_remotion":
        manifest_url = _find_manifest_url_for(person, niche)
        if not manifest_url:
            print("  No MANIFEST_URL found in Content Queue — cannot dispatch render")
            return False
        if _gh_dispatch_clipmine_render(manifest_url, "remotion", niche):
            _update_content_queue_status(person, niche, "Rendering — Remotion")
            return True
        return False

    if action == "needs_more_evidence":
        # NOT implemented as auto re-dispatch. Phase 4 keeps this as a
        # status-only flag because re-triggering video-research.yml would
        # need the original seed_url + requirement, which only live in the
        # manifest JSON (not the Content Queue row). Marking the row tells
        # Priscila to dispatch retry_clipmine_freigilson.yml (or a custom
        # video-research.yml run with `target_clip_count + 4`) manually.
        _update_content_queue_status(person, niche,
            "Needs More Evidence — manual re-dispatch required")
        return True

    if action == "reject_manifest":
        return _update_content_queue_status(person, niche, "Rejected — Manifest")

    if action == "approve_preview":
        # Real side-effect: copy render assets to `Ready to Post — SH-104`.
        # Status update reflects the concrete count + subfolder name so the
        # sheet doesn't drift from Drive truth.
        result = _copy_sh104_render_to_ready(person, niche)
        if result["copied"] > 0:
            sub = result.get("ready_subfolder_name", "")
            _update_content_queue_status(
                person, niche,
                f"Approved — {result['copied']} files copied to "
                f"Ready to Post — SH-104 / {sub}",
            )
            print(f"  APPROVE PREVIEW: copied={result['copied']} → "
                  f"{result.get('ready_subfolder_id','')} ({sub})")
            return True
        # Copy failed — tell the truth in the sheet, alert Priscila so she
        # can fix manually rather than leaving the row falsely "Approved".
        err = "; ".join(result.get("errors", [])[:3]) or "unknown"
        _update_content_queue_status(
            person, niche,
            f"Approved — Render (copy FAILED: {err[:80]})",
        )
        _log_pipeline_failure_to_sheet(
            "approve_preview_copy_failed",
            f"person={person} niche={niche} render_id={result.get('render_folder_id')} "
            f"errors={result.get('errors')}",
        )
        return False

    if action == "render_change":
        return _update_content_queue_status(person, niche, "Render — Change Requested")

    if action == "render_reject":
        return _update_content_queue_status(person, niche, "Rejected — Render")

    return False


def _extract_sh104_metadata(subject: str) -> dict:
    """Pull person + niche out of `Re: [SH-104] Evidence manifest ready — <person> — <niche>`."""
    s = subject or ""
    # Strip "Re: " variants
    body = re.sub(r"^(?:re|fwd?)\s*:\s*", "", s, flags=re.IGNORECASE).strip()
    body = body.replace("[SH-104]", "").strip().lstrip("—").strip()
    # Pattern: Evidence manifest ready — <person> — <niche>
    m = re.search(r"Evidence manifest ready\s*[—-]\s*([^—]+?)\s*[—-]\s*(\w+)\s*$", body)
    if m:
        return {"person_name": m.group(1).strip(), "niche": m.group(2).strip().lower()}
    return {"person_name": "", "niche": "brazil"}


def parse_approval(reply_text):
    text = reply_text.lower().strip()

    # Model override keywords — detected before action parsing
    model = "claude-sonnet-4-6"  # default
    if "use haiku" in text or "with haiku" in text:
        model = "claude-haiku-4-5-20251001"
    elif "use opus" in text or "with opus" in text:
        model = "claude-opus-4-6"

    if text.startswith("not good"):
        fb = reply_text[len("not good"):].strip(" -:\n\t")
        return {"action": "change", "feedback": fb or reply_text, "keyword": "NOT GOOD", "model": model}
    if text.startswith("reject"):
        fb = reply_text[len("reject"):].strip(" -:\n\t")
        return {"action": "change", "feedback": fb or reply_text, "keyword": "REJECT", "model": model}

    if text == "skip":
        return {"action": "skip"}

    approved_match = re.match(r'^(black|cream|lime)\s+approved?$', text)
    if approved_match:
        return {"action": "approve", "variant": approved_match.group(1)}

    if "approved" in text or "approve" in text:
        for v in ["black", "cream", "lime"]:
            if v in text:
                return {"action": "approve", "variant": v}
        return {"action": "approve", "variant": "black"}  # no color = default to black

    return {"action": "change", "feedback": reply_text, "keyword": "FEEDBACK", "model": model}


def _extract_target_from_subject(subject: str) -> dict:
    s = subject or ""
    m_folder = re.search(r"FOLDER:([a-zA-Z0-9_-]{20,})", s)
    m_post = re.search(r"\[REVIEW\]\s+[A-Z]+\s+—\s+([a-zA-Z0-9_-]+)\s+—", s)
    return {
        "folder_id": m_folder.group(1) if m_folder else "",
        "post_id_hint": m_post.group(1) if m_post else "",
    }


def _pick_target_posts(reply: dict, pending: list[dict]) -> list[dict]:
    tgt = _extract_target_from_subject(reply.get("subject", ""))
    fid = tgt.get("folder_id", "")
    if fid:
        scoped = []
        for p in pending:
            m = re.search(r"/folders/([a-zA-Z0-9_-]+)", p.get("static_link", ""))
            if m and m.group(1) == fid:
                scoped.append(p)
        if scoped:
            return scoped
    pid = (tgt.get("post_id_hint") or "").strip()
    if pid:
        scoped = [p for p in pending if (p.get("post_id", "") == pid)]
        if scoped:
            return scoped
    # legacy fallback
    return pending[:1]


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


# Buffer expiry: BUFFER_API_KEY_EXP04092027 expires 2027-04-09
_BUFFER_EXPIRY = datetime(2027, 4, 9, tzinfo=pytz.UTC)


def _buffer_expiry_check():
    """Send an alert email if the Buffer token expires within 30 days."""
    days_left = (_BUFFER_EXPIRY - datetime.now(pytz.UTC)).days
    if days_left <= 30:
        import subprocess, shutil
        gh = shutil.which("gh") or os.path.expanduser("~/bin/gh")
        try:
            subprocess.run([
                gh, "workflow", "run", "send_email.yml",
                "--repo", "priihigashi/oak-park-ai-hub",
                "-f", "to=priscila@oakpark-construction.com",
                "-f", "subject=⚠️ Buffer API Key Expires in {} Days".format(days_left),
                "-f", "body=Buffer API key expires in {} days on 2027-04-09. Renew at buffer.com → Settings → Apps → Generate new token. Update BUFFER_API_KEY_EXP04092027 secret in GitHub.".format(days_left),
            ], check=False, capture_output=True, timeout=30)
            print(f"  ⚠️ Buffer expiry alert sent: {days_left} days remaining")
        except Exception as exc:
            print(f"  Buffer expiry check failed: {exc}")


def _buffer_find_slot(profile_id, min_ts=None):
    """
    Return scheduled_at Unix timestamp for the next available slot (max 3/day).
    Posting times: 9am / 1pm / 6pm ET. Searches up to 60 days ahead.
    min_ts: start searching from this Unix timestamp (default = now).
    """
    from collections import defaultdict
    try:
        url = f"{BUFFER_API}/profiles/{profile_id}/updates/pending.json?access_token={BUFFER_KEY}"
        updates = json.loads(urllib.request.urlopen(url, timeout=15).read()).get("updates", [])
        day_counts = defaultdict(int)
        for u in updates:
            ts = u.get("scheduled_at") or u.get("due_at") or 0
            if ts:
                day_counts[datetime.fromtimestamp(int(ts), ET).strftime("%Y-%m-%d")] += 1
        slot_hours = [9, 13, 18]
        if min_ts:
            start = datetime.fromtimestamp(min_ts, ET).replace(
                hour=0, minute=0, second=0, microsecond=0)
        else:
            start = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in range(60):
            day_str = start.strftime("%Y-%m-%d")
            count = day_counts[day_str]
            if count < 3:
                h = slot_hours[min(count, 2)]
                return int(start.replace(hour=h, minute=0, second=0, microsecond=0).timestamp())
            start += timedelta(days=1)
    except Exception as exc:
        print(f"  _buffer_find_slot error: {exc}")
    return None


def schedule_to_buffer(variant, drive_folder_id, caption="", platform="instagram",
                       _repeat=True, _min_ts=None):
    if not BUFFER_KEY:
        print("  No BUFFER_API_KEY — cannot schedule")
        return False

    _buffer_expiry_check()

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

    slot_ts = _buffer_find_slot(profile_id, min_ts=_min_ts)

    params = [
        ("access_token", BUFFER_KEY),
        ("text", caption),
        ("now", "false"),
    ]
    params.append(("profile_ids[]", profile_id))
    if slot_ts:
        params.append(("scheduled_at", str(slot_ts)))

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

    last_error = None
    for attempt in range(3):
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
            if resp.get("success") or resp.get("id") or "updates" in resp:
                slot_info = (datetime.fromtimestamp(slot_ts, ET).strftime("%Y-%m-%d %H:%M ET")
                             if slot_ts else "queue")
                print(f"  Buffer scheduled: {variant} ({len(image_urls)} slides) → {slot_info}")
                if _repeat and slot_ts:
                    repeat_min = slot_ts + 30 * 24 * 3600
                    schedule_to_buffer(variant, drive_folder_id, caption=caption,
                                       platform=platform, _repeat=False, _min_ts=repeat_min)
                    rdt = datetime.fromtimestamp(repeat_min, ET).strftime("%Y-%m-%d")
                    print(f"  Buffer 30-day repeat queued → earliest slot from {rdt}")
                return True
            print(f"  Buffer rejected: {resp}")
            return False
        except urllib.error.HTTPError as he:
            last_error = he
            if he.code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                print(f"  Buffer attempt {attempt + 1} failed (HTTP {he.code}) — retry in {wait}s")
                time.sleep(wait)
            else:
                print(f"  Buffer HTTP {he.code}: {he}")
                return False
        except Exception as exc:
            last_error = exc
            wait = 2 ** attempt
            print(f"  Buffer attempt {attempt + 1} error: {exc} — retry in {wait}s")
            time.sleep(wait)

    print(f"  Buffer failed after 3 attempts: {last_error}")
    return False


# ── PIPELINE FAILURE LOGGING ──────────────────────────────────────────────────
_FAILURES_SHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
_FAILURES_TAB = "🚨 Pipeline Failures"
_GHA_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")


def _log_pipeline_failure_to_sheet(stage: str, error: str):
    """Append a row to the Pipeline Failures tab in Ideas & Inbox.
    Non-fatal: any error here is printed but does not crash the handler."""
    try:
        token, _ = get_gmail_token()
        run_url = (
            f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{_GHA_RUN_ID}"
            if _GHA_RUN_ID else ""
        )
        row = [
            datetime.utcnow().isoformat() + "Z",
            "approval_handler.py",
            _GHA_RUN_ID,
            stage,
            str(error)[:500],
            run_url,
            "",  # RESOLVED — leave empty (checkbox)
            "",  # NOTE
        ]
        enc = urllib.parse.quote(f"'{_FAILURES_TAB}'", safe="!:'")
        append_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{_FAILURES_SHEET_ID}"
            f"/values/{enc}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        )
        payload = json.dumps({"values": [row]}).encode()
        req = urllib.request.Request(
            append_url, data=payload, method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15).read()
        print(f"  ❌ Pipeline failure logged [{stage}]: {str(error)[:120]}")
    except Exception as log_exc:
        print(f"  (pipeline-failure log write failed: {log_exc})")


def _send_failure_alert(subject: str, body: str):
    """Trigger send_email.yml to alert priscila@oakpark-construction.com."""
    import subprocess, shutil
    gh = shutil.which("gh") or os.path.expanduser("~/bin/gh")
    try:
        subprocess.run([
            gh, "workflow", "run", "send_email.yml",
            "--repo", "priihigashi/oak-park-ai-hub",
            "-f", "to=priscila@oakpark-construction.com",
            "-f", f"subject={subject}",
            "-f", f"body={body}",
        ], check=False, capture_output=True, timeout=30)
    except Exception as exc:
        print(f"  (failure alert send error: {exc})")


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


def re_render_post(post, feedback, model="claude-sonnet-4-6"):
    """Re-render a post with feedback. Creates v{n+1} folder in same parent as current static folder."""
    import sys, shutil
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from carousel_builder import generate_carousel_content, build_html, render_pngs, fetch_all_media
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

    # Support both naming conventions: v{N}_{slug} (current) and {id}_v{N}_static (legacy)
    ver_match = re.match(r'^v(\d+)_(.+)$', folder_name)
    if ver_match:
        current_ver = int(ver_match.group(1))
        slug = ver_match.group(2)
        new_ver = current_ver + 1
        new_folder_name = f"v{new_ver}_{slug}"
    else:
        legacy_match = re.search(r'_v(\d+)_static$', folder_name)
        current_ver = int(legacy_match.group(1)) if legacy_match else 1
        new_ver = current_ver + 1
        new_folder_name = f"v{new_ver}_{post_id[:30]}"

    # Normalize niche for carousel_builder
    niche = _normalize_niche(niche)
    if not os.environ.get("CLAUDE_KEY_4_CONTENT"):
        print(f"  re_render: no CLAUDE_KEY_4_CONTENT — cannot regenerate content")
        return False

    # Signal re-render is in progress → "Approved — Rebuild" in In Production tab
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent))
        if niche == "opc":
            from content_tracker import update_in_production
            update_in_production(title=topic[:100], content_type="Carousel",
                                 status="Approved — Rebuild",
                                 drive_folder_link=post.get("static_link", ""))
        else:
            from content_tracker import update_news_in_production
            update_news_in_production(title=topic[:100], niche=niche.upper(),
                                      content_type="Carousel", status="Approved — Rebuild",
                                      drive_folder_link=post.get("static_link", ""))
    except Exception as _te:
        print(f"  In Production pre-render status update skipped: {_te}")

    content = generate_carousel_content(topic, niche, brief=f"Revision feedback:\n{feedback}", model=model)
    if not content:
        print(f"  re_render: content generation failed")
        return False

    work = Path(f"/tmp/rerender_{post_id}_v{new_ver}")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    slug = post_id.replace("opc-tip-", "").replace("brazil-", "")[:30]
    media_paths = fetch_all_media(content, niche, str(work))
    html_path = build_html(content, niche, slug, str(work), media_paths=media_paths)
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

    # Upload cover.html at version root
    drive.files().create(
        body={"name": "cover.html", "parents": [new_folder_id]},
        media_body=MediaFileUpload(str(html_path), mimetype="text/html"),
        supportsAllDrives=True, fields="id",
    ).execute()

    # Create png/ subfolder inside version folder (matches carousel folder standard)
    png_drive_folder = drive.files().create(
        body={"name": "png", "mimeType": "application/vnd.google-apps.folder", "parents": [new_folder_id]},
        supportsAllDrives=True, fields="id",
    ).execute()["id"]

    for f in sorted(png_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            drive.files().create(
                body={"name": f.name, "parents": [png_drive_folder]},
                media_body=MediaFileUpload(str(f), mimetype="image/png"),
                supportsAllDrives=True, fields="id",
            ).execute()

    # resources/ with media provenance including user feedback
    resources_folder_id = drive.files().create(
        body={"name": "resources", "mimeType": "application/vnd.google-apps.folder", "parents": [new_folder_id]},
        supportsAllDrives=True, fields="id",
    ).execute()["id"]
    images_folder_id = drive.files().create(
        body={"name": "images", "mimeType": "application/vnd.google-apps.folder", "parents": [resources_folder_id]},
        supportsAllDrives=True, fields="id",
    ).execute()["id"]
    local_images = work / "resources" / "images"
    if local_images.exists():
        for f in sorted(local_images.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                drive.files().create(
                    body={"name": f.name, "parents": [images_folder_id]},
                    media_body=MediaFileUpload(str(f)),
                    supportsAllDrives=True, fields="id",
                ).execute()

    prov = media_paths.get("provenance", {}) if isinstance(media_paths, dict) else {}
    if isinstance(prov, dict):
        if isinstance(prov.get("cover"), dict):
            prov["cover"]["user_feedback"] = feedback
        for sv in (prov.get("slides", {}) or {}).values():
            if isinstance(sv, dict):
                sv["user_feedback"] = feedback
    prov_payload = {
        "post_id": post_id,
        "topic": topic,
        "niche": niche,
        "version_folder_id": new_folder_id,
        "generated_at": datetime.now(ET).isoformat(),
        "user_feedback": feedback,
        "cover": prov.get("cover", {}),
        "slides": prov.get("slides", {}),
    }
    prov_path = work / "media_provenance.json"
    prov_path.write_text(json.dumps(prov_payload, indent=2), encoding="utf-8")
    drive.files().create(
        body={"name": "media_provenance.json", "parents": [resources_folder_id]},
        media_body=MediaFileUpload(str(prov_path), mimetype="application/json"),
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
                    {"range": f"'{CATALOG_TAB}'!M{row_num}", "values": [["pending_approval"]]},
                ],
            }).encode()
            req2 = urllib.request.Request(batch_url, data=batch_payload,
                                          headers={"Authorization": f"Bearer {token}",
                                                    "Content-Type": "application/json"})
            urllib.request.urlopen(req2)
            print(f"  Catalog updated: {post_id} → pending_approval, v{new_ver} static link")
            break

    from email_preview import send_preview, make_cover_thumbnails_public
    cover_urls = make_cover_thumbnails_public(png_drive_folder, token)
    post_updated = dict(post)
    post_updated["static_link"] = new_static_link
    post_updated["static_folder_id"] = new_folder_id
    post_updated["cover_urls"] = cover_urls
    # Carry forward reply-guide fields from new content so checklist is populated
    if content:
        post_updated["cover_visual"] = content.get("cover_visual", {})
        raw_people = []
        for slide in content.get("slides", []):
            raw_people.extend(p.get("name", "") for p in slide.get("secondary_people", []) if p.get("name"))
        post_updated["mentioned_people"] = list(dict.fromkeys(raw_people))
        post_updated["clip_suggestions"] = content.get("clip_suggestions", [])
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


def _normalize_niche(raw):
    """Map any catalog niche value to the 3 canonical values carousel_builder expects."""
    r = (raw or "").lower().strip()
    if any(x in r for x in ("brazil", "brasil", "quem", "news-brazil", "sovereign", "news")):
        return "brazil"
    if any(x in r for x in ("usa", "united", "news-usa", "news-us", "the chain")):
        return "usa"
    if any(x in r for x in ("opc", "oak park", "tip")):
        return "opc"
    # post_id prefix fallback
    return r  # will surface the real value in logs


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
        VALID_NICHES = {"opc", "brazil", "usa", "ugc", "stocks", "higashi", "book"}
        if v("status") == "pending_approval":
            raw_niche = v("niche") or ""
            post_id = v("post_id") or (row[0] if len(row) > 0 else "")
            # Infer niche from post_id prefix when catalog has no niche col or invalid value
            if raw_niche.lower() not in VALID_NICHES:
                if post_id.startswith("opc-"):
                    raw_niche = "opc"
                elif post_id.startswith("usa-"):
                    raw_niche = "usa"
                elif post_id.startswith("brazil-"):
                    raw_niche = "brazil"
            pending.append({
                "post_id": post_id,
                "niche": _normalize_niche(raw_niche),
                "static_link": v("static folder") or (row[8] if len(row) > 8 else ""),
                "motion_link": v("motion folder") or (row[9] if len(row) > 9 else ""),
                "topic": v("topic") or (row[13] if len(row) > 13 else ""),
                "date_created": v("date_created") or v("date") or (row[6] if len(row) > 6 else ""),
            })
    return pending


def _approval_group(niche):
    n = (niche or "").lower()
    if n == "opc":
        return "opc"
    if n in {"brazil", "usa"}:
        return "news"
    return "other"


def _send_approval_reminder(stale_posts, group="other"):
    """Nudge email for posts that have been pending approval for >24h."""
    if not stale_posts:
        return
    import subprocess, shutil
    gh = shutil.which("gh") or os.path.expanduser("~/bin/gh")
    lines = []
    for p in stale_posts:
        lines.append(
            f"- {p['topic'][:80]} ({p['niche'].upper()})\n  Drive: {p['static_link']}"
        )
    count = len(stale_posts)
    subject = APPROVAL_REMINDER_SUBJECTS.get(group, APPROVAL_REMINDER_SUBJECTS["other"])
    group_label = group.upper() if group != "news" else "NEWS"
    body = (
        f"{count} {group_label} post(s) are currently waiting for your approval.\n\n"
        "The following posts are waiting for your approval:\n\n"
        + "\n\n".join(lines)
        + "\n\nReply 'black approved', 'cream approved', or 'skip' to the original preview email."
    )
    # Keep only one active reminder thread in inbox: archive older reminders first.
    try:
        token, _ = get_gmail_token()
        q = urllib.parse.quote(f'subject:"{subject}" in:inbox')
        url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={q}&maxResults=200"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        msgs = json.loads(urllib.request.urlopen(req).read()).get("messages", [])
        ids = [m["id"] for m in msgs]
        if ids:
            mod_req = urllib.request.Request(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/batchModify",
                data=json.dumps({"ids": ids, "removeLabelIds": ["INBOX"]}).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(mod_req).read()
            print(f"  Archived {len(ids)} older approval reminder email(s) before sending new one")
    except Exception as exc:
        print(f"  Could not pre-archive older approval reminders: {exc}")
    try:
        subprocess.run(
            [gh, "workflow", "run", "send_email.yml",
             "--repo", "priihigashi/oak-park-ai-hub",
             "-f", "to=priscila@oakpark-construction.com",
             "-f", f"subject={subject}",
             "-f", f"body={body}"],
            check=False, capture_output=True, timeout=30,
        )
        print(f"  Approval reminder sent: {count} stale {group_label} post(s)")
    except Exception as exc:
        print(f"  Approval reminder failed (non-fatal): {exc}")


def _check_stale_reminders(pending):
    # Send max once per 24h per approval group even if workflow runs hourly.
    today = datetime.now(ET).strftime("%Y-%m-%d")
    stale = [p for p in pending if p.get("date_created") and p["date_created"] < today]
    if not stale:
        return

    groups = {}
    for post in stale:
        groups.setdefault(_approval_group(post.get("niche")), []).append(post)

    try:
        token, _ = get_gmail_token()
    except Exception as exc:
        token = None
        print(f"  Reminder dedupe token failed (continuing): {exc}")

    for group, posts in groups.items():
        subject = APPROVAL_REMINDER_SUBJECTS.get(group, APPROVAL_REMINDER_SUBJECTS["other"])
        if token:
            try:
                q = urllib.parse.quote(f'subject:"{subject}" newer_than:1d')
                req = urllib.request.Request(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={q}&maxResults=1",
                    headers={"Authorization": f"Bearer {token}"},
                )
                existing = json.loads(urllib.request.urlopen(req).read()).get("messages", [])
                if existing:
                    print(f"  {group.upper()} approval reminder already sent in last 24h — skipping duplicate")
                    continue
            except Exception as exc:
                print(f"  {group.upper()} reminder dedupe check failed (continuing): {exc}")
        _send_approval_reminder(posts, group)


def process_replies():
    token, _ = get_gmail_token()
    replies = search_gmail_replies(token)
    pending = _get_pending_posts()

    if not replies:
        print("  No approval replies found")
        _check_stale_reminders(pending)
        return {"approved": 0, "changes": 0, "skipped": 0}

    if not pending:
        print("  No pending_approval posts in catalog")
        return {"approved": 0, "changes": 0, "skipped": 0}

    stats = {"approved": 0, "changes": 0, "skipped": 0,
             "sh104_actions": 0, "sh104_unknown": 0}

    for reply in replies:
        # SH-104 reply routing — separate from carousel preview replies.
        if is_sh104_reply(reply.get("subject", "")):
            sh = parse_sh104_reply(reply["reply_text"])
            meta = _extract_sh104_metadata(reply.get("subject", ""))
            print(
                f"  SH-104 reply: '{reply['reply_text'][:60]}' → "
                f"{sh['action']} (person={meta['person_name']}, niche={meta['niche']})"
            )
            if sh["action"] == "unknown":
                stats["sh104_unknown"] += 1
                continue
            handled = _handle_sh104_reply(sh, meta, reply)
            if handled:
                stats["sh104_actions"] += 1
            continue

        result = parse_approval(reply["reply_text"])
        scoped_posts = _pick_target_posts(reply, pending)
        print(
            f"  Reply: '{reply['reply_text'][:60]}' → {result['action']} "
            f"(targets={len(scoped_posts)})"
        )

        if result["action"] == "approve":
            variant = result["variant"]
            for post in scoped_posts:
                post_id = post["post_id"]
                niche = post["niche"]
                static_folder_id = re.search(r'/folders/([a-zA-Z0-9_-]+)', post["static_link"])
                static_folder_id = static_folder_id.group(1) if static_folder_id else ""

                if not static_folder_id:
                    print(f"  No static folder ID for {post_id} — skipping")
                    continue

                if BUFFER_KEY:
                    # SH-029 fix: BUFFER_PROFILE_ID guard removed — schedule_to_buffer()
                    # auto-discovers the Instagram profile from Buffer API /profiles.json.
                    caption = post.get("topic", "")
                    try:
                        _buf_ok = schedule_to_buffer(variant, static_folder_id, caption=caption)
                        if not _buf_ok:
                            raise RuntimeError("schedule_to_buffer returned False")
                        print(f"  Buffer scheduled OK: {post_id} ({variant})")
                    except Exception as _buf_exc:
                        _err_str = str(_buf_exc)
                        _log_pipeline_failure_to_sheet("buffer_schedule", _err_str)
                        _send_failure_alert(
                            f"❌ Buffer scheduling failed — {post_id}",
                            f"Post {post_id} ({variant}) was approved but Buffer scheduling failed.\n"
                            f"Error: {_err_str}\n"
                            f"Static folder: https://drive.google.com/drive/folders/{static_folder_id}\n"
                            f"Run: https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{_GHA_RUN_ID}",
                        )

                copy_to_ready_folder(variant, static_folder_id, niche)
                update_catalog(post_id, "approved")
                _delete_old_versions(post_id, static_folder_id)
                print(f"  Approved: {post_id} ({variant})")

                # Copy carousel_reel.mp4 to Reels_Shorts folder for this niche
                _reel_link = post.get("reel_link", "")
                if _reel_link:
                    try:
                        import sys as _sys
                        from pathlib import Path as _Path
                        _sys.path.insert(0, str(_Path(__file__).parent.parent))
                        from routing import reels_folder as _reels_folder
                        _reels_dest = _reels_folder(niche)
                        if _reels_dest:
                            _drive_svc = _get_drive_service()
                            _motion_fid = post.get("motion_folder_id", "")
                            if _motion_fid:
                                _reel_files = _drive_svc.files().list(
                                    q=f"'{_motion_fid}' in parents and name='carousel_reel.mp4' and trashed=false",
                                    supportsAllDrives=True, includeItemsFromAllDrives=True,
                                    fields="files(id,name)",
                                ).execute().get("files", [])
                                if _reel_files:
                                    _reel_fid = _reel_files[0]["id"]
                                    _drive_svc.files().copy(
                                        fileId=_reel_fid,
                                        body={"name": f"{post_id}_carousel_reel.mp4", "parents": [_reels_dest]},
                                        supportsAllDrives=True,
                                    ).execute()
                                    print(f"  Reel copied → Reels_Shorts/{niche}")
                                else:
                                    print(f"  [reel] carousel_reel.mp4 not found in motion folder — skipping Reels_Shorts copy")
                    except Exception as _reel_copy_err:
                        print(f"  [reel] Reels_Shorts copy failed (non-fatal): {_reel_copy_err}")

                # Mirror status to correct In Production tab (Content Control)
                try:
                    import sys
                    from pathlib import Path as _Path
                    sys.path.insert(0, str(_Path(__file__).parent.parent))
                    _post_niche = post.get("niche", "opc").lower()
                    if _post_niche == "opc":
                        from content_tracker import update_in_production
                        update_in_production(
                            title=post.get("topic", post_id)[:100],
                            content_type="Carousel",
                            status="Approved",
                            drive_folder_link=post.get("static_link", ""),
                            output_link=post.get("motion_link", ""),
                        )
                    else:
                        from content_tracker import update_news_in_production
                        update_news_in_production(
                            title=post.get("topic", post_id)[:100],
                            niche=_post_niche.upper(),
                            content_type="Carousel",
                            status="Approved",
                            drive_folder_link=post.get("static_link", ""),
                            output_link=post.get("motion_link", ""),
                        )
                except Exception as _e:
                    print(f"  In Production update skipped (non-fatal): {_e}")

            stats["approved"] += 1

        elif result["action"] == "skip":
            for post in scoped_posts:
                update_catalog(post["post_id"], "skipped")
            stats["skipped"] += 1

        else:
            feedback = result.get("feedback", "")
            stats["changes"] += 1
            print(f"  Change requested: {feedback[:80]}")
            for post in scoped_posts:
                try:
                    if re_render_post(post, feedback, model=result.get("model", "claude-sonnet-4-6")):
                        print(f"  Re-render triggered: {post['post_id']}")
                    else:
                        print(f"  Re-render failed: {post['post_id']}")
                except Exception as exc:
                    print(f"  Re-render crashed for {post['post_id']}: {exc}")

    if stats["approved"] == 0 and stats["skipped"] == 0:
        _check_stale_reminders(pending)

    return stats


if __name__ == "__main__":
    stats = process_replies()
    print(json.dumps(stats, indent=2))
