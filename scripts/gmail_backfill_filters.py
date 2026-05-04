#!/usr/bin/env python3
"""
One-shot backfill: apply the new Gmail filters to messages already in inbox.

Gmail filters only fire on incoming mail — they do NOT retroactively label/archive
existing messages. This script scans the current inbox and applies the same rules
the filters would have applied.

Safe to re-run. Idempotent: only modifies messages that don't already have the
target label or inbox state.
"""
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_PATH = Path("/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json")

# Each rule: (gmail search query to find matches, label_name, skip_inbox, star)
RULES = [
    ('from:priscila@oakpark-construction.com subject:"Capture done"',       "🤖 Automation/Captures",       False, False),
    ('from:priscila@oakpark-construction.com subject:"CAPTURE FAILED"',     "🚨 Automation Errors",         False, False),
    ('from:priscila@oakpark-construction.com subject:"Daily Advancer"',     "🤖 Automation/Daily Digests",  False, False),
    ('from:priscila@oakpark-construction.com subject:"Calendar Optimized"', "🤖 Automation/Daily Digests",  False, False),
    ('from:priscila@oakpark-construction.com subject:"Content approvals pending"', "🤖 Automation/Pending Approval", False, True),
    ('from:priscila@oakpark-construction.com subject:"OPC content approvals pending"', "🤖 Automation/Pending Approval/OPC", False, True),
    ('from:priscila@oakpark-construction.com subject:"News content approvals pending"', "🤖 Automation/Pending Approval/News", False, True),
    ('from:priscila@oakpark-construction.com subject:(waiting for your approval)', "🤖 Automation/Pending Approval", False, True),
    ('from:priscila@oakpark-construction.com subject:"4AM Agent FAILED"',   "🤖 Automation/Notifications", False, False),
    ('from:priscila@oakpark-construction.com subject:"4AM Agent ran OK"',   "🤖 Automation/Notifications", False, False),
    ('from:priscila@oakpark-construction.com subject:"4AM Content Ready"',  "Reports/4AM Agent",            False, True),
    ('from:@xwf.google.com',                                                "google ads help",              True,  False),
    ('from:sailakshmiu@google.com',                                         "google ads help",              True,  False),
    ('from:info@tastylunchboxes.com',                                       "📬 Promos & News",             True,  False),
    ('from:info@freepik.com',                                               "📬 Promos & News",             True,  False),
    ('from:@mail.zapier.com OR from:no-reply@zapier.com OR from:notifications@zapier.com', "📬 Promos & News", True, False),
    ('from:no-reply@accounts.google.com subject:"Security alert"',          "🔒 Security",                  False, False),
    ('from:no-reply@accounts.google.com subject:"Critical security alert"', "🔒 Security",                  False, False),
]


def load_creds() -> Credentials:
    d = json.loads(TOKEN_PATH.read_text())
    creds = Credentials(
        token=d.get("token"),
        refresh_token=d.get("refresh_token"),
        token_uri=d.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=d.get("client_id"),
        client_secret=d.get("client_secret"),
        scopes=d.get("scopes"),
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def main():
    creds = load_creds()
    gmail = build("gmail", "v1", credentials=creds)
    labels = {l["name"]: l["id"] for l in gmail.users().labels().list(userId="me").execute().get("labels", [])}

    total_modified = 0
    for query, label_name, skip_inbox, star in RULES:
        lid = labels.get(label_name)
        if not lid:
            print(f"  skip (no label): {label_name}")
            continue

        full_q = f"in:inbox {query}"
        add = [lid]
        remove = []
        if skip_inbox:
            remove.append("INBOX")
        if star:
            add.append("STARRED")

        modified = 0
        page_token = None
        while True:
            resp = gmail.users().messages().list(
                userId="me", q=full_q, maxResults=500, pageToken=page_token
            ).execute()
            msgs = resp.get("messages", [])
            for m in msgs:
                gmail.users().messages().modify(
                    userId="me",
                    id=m["id"],
                    body={"addLabelIds": add, "removeLabelIds": remove},
                ).execute()
                modified += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        arrow = f"→ {label_name}" + (" (skip inbox)" if skip_inbox else "") + (" ⭐" if star else "")
        print(f"  {modified:4d}  {arrow}  [{query}]")
        total_modified += modified

    print(f"\nTotal messages modified: {total_modified}")


if __name__ == "__main__":
    main()
