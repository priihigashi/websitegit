#!/usr/bin/env python3
"""
email_resurface.py — Email snooze/tickler: resurface archived emails after N days

Runs daily. For each label with a timer, finds archived emails older than N days
and moves them back to inbox so Priscila sees them again.

Auth: OAuth2 via sheets_token.json (has gmail.modify scope)
GitHub Action: email-resurface.yml (runs daily 8AM ET)
"""

import os
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# ── TIMER CONFIG (label_id → days until resurface) ────────────────────────────
RESURFACE_TIMERS = {
    "Label_9":                         4,   # 🎯 Leads / LSA
    "Label_13":                        4,   # 📁 Drive Shares
    "Label_12":                        3,   # 🔒 Security
    "Label_8":                         2,   # 📅 Calendar & Meetings
    "Label_7":                         7,   # 💳 Billing
    "Label_10":                       14,   # 🏥 Health / Insurance
    "Label_4":                        14,   # 📊 Google Business
    "Label_6":                         7,   # 🤖 Automation
    "Label_5":                         1,   # 🚨 Automation Errors
    "Label_3141389375098560388":        4,   # THUMBTACK
}

LABEL_NAMES = {
    "Label_9":   "Leads/LSA",
    "Label_13":  "Drive Shares",
    "Label_12":  "Security",
    "Label_8":   "Calendar",
    "Label_7":   "Billing",
    "Label_10":  "Health/Insurance",
    "Label_4":   "Google Business",
    "Label_6":   "Automation",
    "Label_5":   "Automation Errors",
    "Label_3141389375098560388": "THUMBTACK",
}

# ── AUTH ──────────────────────────────────────────────────────────────────────
def get_access_token():
    """Get access token from SHEETS_TOKEN env var (JSON string of OAuth token)"""
    token_json = os.environ.get("SHEETS_TOKEN", "")
    if not token_json:
        raise ValueError("SHEETS_TOKEN env var not set")
    
    token_data = json.loads(token_json)
    access_token = token_data.get("token") or token_data.get("access_token")
    
    # Check if expired and refresh
    expiry = token_data.get("expiry") or token_data.get("token_expiry")
    if expiry:
        try:
            from datetime import timezone
            exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp_dt <= datetime.now(timezone.utc):
                access_token = refresh_token(token_data)
        except Exception as e:
            print(f"  Could not check expiry: {e}")
    
    return access_token

def refresh_token(token_data: dict) -> str:
    """Refresh OAuth token using refresh_token"""
    client_id = token_data.get("client_id")
    client_secret = token_data.get("client_secret")
    refresh = token_data.get("refresh_token")
    
    if not all([client_id, client_secret, refresh]):
        raise ValueError("Missing OAuth fields in token — cannot refresh")
    
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        return result["access_token"]

# ── GMAIL API ─────────────────────────────────────────────────────────────────
def gmail_get(path: str, token: str, params: dict = None) -> dict:
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def gmail_post(path: str, token: str, body: dict) -> dict:
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def search_archived_label(label_id: str, days: int, token: str) -> list[str]:
    """Find archived (not in inbox) emails with this label older than N days"""
    # older_than:Nd filters by email date (not archive date) — close enough
    query = f"label:{label_id} -in:inbox older_than:{days}d"
    try:
        result = gmail_get("messages", token, {"q": query, "maxResults": 100})
        messages = result.get("messages", [])
        return [m["id"] for m in messages]
    except Exception as e:
        print(f"  Search error for {label_id}: {e}")
        return []

def resurface_messages(message_ids: list[str], token: str) -> bool:
    """Move messages back to inbox by adding INBOX label"""
    if not message_ids:
        return True
    try:
        gmail_post("messages/batchModify", token, {
            "ids": message_ids,
            "addLabelIds": ["INBOX"]
        })
        return True
    except Exception as e:
        print(f"  Batch modify error: {e}")
        return False

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run():
    print(f"\n=== EMAIL RESURFACE — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    
    token = get_access_token()
    
    total_resurfaced = 0
    
    for label_id, days in RESURFACE_TIMERS.items():
        name = LABEL_NAMES.get(label_id, label_id)
        print(f"\n[{name}] Looking for emails archived {days}+ days ago...")
        
        ids = search_archived_label(label_id, days, token)
        
        if not ids:
            print(f"  None found")
            continue
        
        print(f"  Found {len(ids)} emails — resurfacing...")
        
        # Process in batches of 1000 (Gmail API limit)
        for i in range(0, len(ids), 1000):
            batch = ids[i:i+1000]
            ok = resurface_messages(batch, token)
            if ok:
                total_resurfaced += len(batch)
                print(f"  ✅ Resurfaced {len(batch)} emails from {name}")
            else:
                print(f"  ❌ Failed to resurface batch from {name}")
    
    print(f"\n=== DONE: {total_resurfaced} emails moved back to inbox ===")

if __name__ == "__main__":
    run()
