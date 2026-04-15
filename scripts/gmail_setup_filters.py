#!/usr/bin/env python3
"""
Gmail filter + label setup for priscila@oakpark-construction.com.

Creates nested automation sub-labels and Gmail filters that:
  - Route automation self-emails to nested labels (keep in inbox, archived later by gmail_archive.yml)
  - Skip-inbox for sales/subscription junk (Google Ads reps, Tasty Lunchboxes, Freepik, Zapier)
  - Label ✅ 4AM Content Ready + star it
  - Label Google security alerts

Idempotent: skips labels that already exist, skips filters whose criteria already exist.

Credentials: /Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json
Scopes required: gmail.settings.basic + gmail.modify (confirmed present 2026-04-12).
"""
import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

TOKEN_PATH = Path("/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json")

SUB_LABELS_TO_CREATE = [
    "🤖 Automation/Captures",
    "🤖 Automation/Daily Digests",
    "🤖 Automation/Notifications",
]


def load_creds() -> Credentials:
    data = json.loads(TOKEN_PATH.read_text())
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def ensure_label(gmail, name: str, existing: dict) -> str:
    if name in existing:
        return existing[name]
    body = {
        "name": name,
        "messageListVisibility": "show",
        "labelListVisibility": "labelShow",
    }
    label = gmail.users().labels().create(userId="me", body=body).execute()
    print(f"  created label: {name} → {label['id']}")
    existing[name] = label["id"]
    return label["id"]


def list_existing_filters(gmail) -> list:
    res = gmail.users().settings().filters().list(userId="me").execute()
    return res.get("filter", [])


def criteria_match(a: dict, b: dict) -> bool:
    keys = {"from", "to", "subject", "query", "negatedQuery", "hasAttachment", "size", "sizeComparison"}
    return {k: a.get(k) for k in keys} == {k: b.get(k) for k in keys}


def ensure_filter(gmail, criteria: dict, action: dict, existing_filters: list, description: str):
    for f in existing_filters:
        if criteria_match(f.get("criteria", {}), criteria):
            print(f"  skip (exists): {description}")
            return
    body = {"criteria": criteria, "action": action}
    try:
        res = gmail.users().settings().filters().create(userId="me", body=body).execute()
        print(f"  created filter: {description} → {res.get('id')}")
        existing_filters.append(res)
    except HttpError as e:
        print(f"  ERROR creating filter '{description}': {e}")


def main():
    creds = load_creds()
    gmail = build("gmail", "v1", credentials=creds)

    print("Loading existing labels...")
    labels_resp = gmail.users().labels().list(userId="me").execute()
    existing = {lbl["name"]: lbl["id"] for lbl in labels_resp.get("labels", [])}

    print("Ensuring sub-labels exist...")
    for name in SUB_LABELS_TO_CREATE:
        ensure_label(gmail, name, existing)

    # Resolve label IDs we'll need
    LID_CAPTURES = existing["🤖 Automation/Captures"]
    LID_DIGESTS = existing["🤖 Automation/Daily Digests"]
    LID_NOTIFS = existing["🤖 Automation/Notifications"]
    LID_AUTO_ERRORS = existing.get("🚨 Automation Errors")
    LID_4AM = existing.get("Reports/4AM Agent")
    LID_GADS_HELP = existing.get("google ads help")
    LID_PROMOS = existing.get("📬 Promos & News")
    LID_SECURITY = existing.get("🔒 Security")

    missing = [n for n, v in {
        "🚨 Automation Errors": LID_AUTO_ERRORS,
        "Reports/4AM Agent": LID_4AM,
        "google ads help": LID_GADS_HELP,
        "📬 Promos & News": LID_PROMOS,
        "🔒 Security": LID_SECURITY,
    }.items() if v is None]
    if missing:
        print(f"ERROR: expected pre-existing labels missing: {missing}")
        sys.exit(1)

    print("\nLoading existing filters...")
    existing_filters = list_existing_filters(gmail)
    print(f"  {len(existing_filters)} filter(s) already configured")

    print("\nCreating filters...")

    # 1) Capture done — route to Captures
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "Capture done"},
        {"addLabelIds": [LID_CAPTURES]},
        existing_filters,
        "Capture done → 🤖 Automation/Captures",
    )

    # 2) CAPTURE FAILED — route to Automation Errors
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "CAPTURE FAILED"},
        {"addLabelIds": [LID_AUTO_ERRORS]},
        existing_filters,
        "CAPTURE FAILED → 🚨 Automation Errors",
    )

    # 3) Daily Advancer — route to Daily Digests
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "Daily Advancer"},
        {"addLabelIds": [LID_DIGESTS]},
        existing_filters,
        "Daily Advancer → 🤖 Automation/Daily Digests",
    )

    # 4) Calendar Optimized — route to Daily Digests
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "Calendar Optimized"},
        {"addLabelIds": [LID_DIGESTS]},
        existing_filters,
        "Calendar Optimized → 🤖 Automation/Daily Digests",
    )

    # 5) 4AM Agent FAILED / ran OK — route to Notifications (not ✅ Content Ready emails)
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "4AM Agent FAILED"},
        {"addLabelIds": [LID_NOTIFS]},
        existing_filters,
        "4AM Agent FAILED → 🤖 Automation/Notifications",
    )
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "4AM Agent ran OK"},
        {"addLabelIds": [LID_NOTIFS]},
        existing_filters,
        "4AM Agent ran OK → 🤖 Automation/Notifications",
    )

    # 6) ✅ 4AM Content Ready — label Reports/4AM Agent + star
    ensure_filter(
        gmail,
        {"from": "priscila@oakpark-construction.com", "subject": "4AM Content Ready"},
        {"addLabelIds": [LID_4AM, "STARRED"]},
        existing_filters,
        "4AM Content Ready → Reports/4AM Agent + ⭐",
    )

    # 7) Google Ads sales outreach — skip inbox, label "google ads help"
    ensure_filter(
        gmail,
        {"from": "@xwf.google.com"},
        {"addLabelIds": [LID_GADS_HELP], "removeLabelIds": ["INBOX"]},
        existing_filters,
        "@xwf.google.com (Google Ads reps) → google ads help, skip inbox",
    )
    ensure_filter(
        gmail,
        {"from": "sailakshmiu@google.com"},
        {"addLabelIds": [LID_GADS_HELP], "removeLabelIds": ["INBOX"]},
        existing_filters,
        "sailakshmiu@google.com → google ads help, skip inbox",
    )

    # 8) Promo/subscription junk — skip inbox, label Promos & News
    junk_senders = [
        "info@tastylunchboxes.com",
        "info@freepik.com",
        "@mail.zapier.com",
        "no-reply@zapier.com",
        "notifications@zapier.com",
    ]
    for sender in junk_senders:
        ensure_filter(
            gmail,
            {"from": sender},
            {"addLabelIds": [LID_PROMOS], "removeLabelIds": ["INBOX"]},
            existing_filters,
            f"{sender} → 📬 Promos & News, skip inbox",
        )

    # 9) Google security alerts — label 🔒 Security (keep in inbox, archived after 7d by workflow)
    ensure_filter(
        gmail,
        {"from": "no-reply@accounts.google.com", "subject": "Security alert"},
        {"addLabelIds": [LID_SECURITY]},
        existing_filters,
        "Security alert → 🔒 Security",
    )
    ensure_filter(
        gmail,
        {"from": "no-reply@accounts.google.com", "subject": "Critical security alert"},
        {"addLabelIds": [LID_SECURITY]},
        existing_filters,
        "Critical security alert → 🔒 Security",
    )

    print("\n✅ Done.")


if __name__ == "__main__":
    main()
