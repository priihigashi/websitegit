"""Ads API Approval Watcher.

Runs on GitHub Actions every 6h. Scans Priscila's Gmail for the Google Ads
API Basic Access approval email. When found:
  1. Writes flag file .github/agent_state/ads_api_approved.json (so 4AM agent + next Claude session knows)
  2. Creates a Google Calendar event tomorrow 10am with next-steps checklist
  3. Logs what was found to GitHub Actions output

Dedupe: if flag file already exists, exits silently.

Email source: approval comes to mcfollingproperties@gmail.com, which must have
a forward rule to priscila@oakpark-construction.com. See CLAUDE.md.
"""

import os
import json
import sys
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

REPO_ROOT = Path(__file__).resolve().parent.parent
FLAG_FILE = REPO_ROOT / ".github" / "agent_state" / "ads_api_approved.json"

# Gmail search — match any Google-origin email mentioning Ads API / Basic Access
# Looks at the last 14 days so we don't miss anything if the watcher was paused
GMAIL_QUERY = (
    'from:(@google.com) '
    '(subject:("Google Ads API") OR '
    'subject:("developer token") OR '
    'subject:("Basic Access") OR '
    'subject:("API Center")) '
    'newer_than:14d'
)


def load_creds() -> Credentials:
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        print("ERROR: SHEETS_TOKEN secret not set.", file=sys.stderr)
        sys.exit(1)
    tok = json.loads(raw)
    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=tok.get("client_id"),
        client_secret=tok.get("client_secret"),
        scopes=tok.get("scopes"),
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def already_flagged() -> bool:
    return FLAG_FILE.exists()


def search_gmail(creds: Credentials):
    """Return (message_id, subject, snippet, from_addr) of first match, or None."""
    gmail = build("gmail", "v1", credentials=creds)
    results = gmail.users().messages().list(
        userId="me", q=GMAIL_QUERY, maxResults=5
    ).execute()
    msgs = results.get("messages", [])
    if not msgs:
        return None

    # Fetch full metadata for the most recent match
    msg_id = msgs[0]["id"]
    msg = gmail.users().messages().get(
        userId="me", id=msg_id, format="metadata",
        metadataHeaders=["Subject", "From", "Date"]
    ).execute()

    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return {
        "message_id": msg_id,
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", "(unknown sender)"),
        "date": headers.get("Date", "(no date)"),
        "snippet": msg.get("snippet", "")[:300],
        "thread_id": msg.get("threadId"),
    }


def write_flag(match: dict) -> None:
    FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "source_email": match,
        "next_steps": [
            "Update GOOGLE_ADS_DEVELOPER_TOKEN if new token was issued",
            "Run mutation scripts in scripts/ads_mutations/ (pause/budget/negkw)",
            "Link Google Ads <-> GA4 for conversion import",
            "Deploy PWA dashboard to Vercel",
            "Set up daily ads_report.yml workflow",
        ],
    }
    FLAG_FILE.write_text(json.dumps(payload, indent=2))
    print(f"Flag written: {FLAG_FILE}")


def create_calendar_event(creds: Credentials, match: dict) -> str | None:
    try:
        cal = build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"Calendar build failed (likely missing scope): {e}")
        return None

    # Tomorrow at 10am ET (14:00 UTC during EDT)
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(hours=1)

    body = {
        "summary": "Google Ads API Approved — Next Steps",
        "description": (
            f"Approval email detected.\n\n"
            f"FROM: {match['from']}\n"
            f"SUBJECT: {match['subject']}\n"
            f"DATE: {match['date']}\n\n"
            f"SNIPPET:\n{match['snippet']}\n\n"
            f"NEXT STEPS (invoke /ads-opc):\n"
            f"1. Verify new developer token in email body (if rotated)\n"
            f"2. Update GOOGLE_ADS_DEVELOPER_TOKEN secret if needed\n"
            f"3. Run: ~/bin/gh workflow run ads_report.yml (once built)\n"
            f"4. Deploy PWA dashboard to Vercel\n"
            f"5. Execute pre-written mutation scripts:\n"
            f"   - add_negative_keywords.py (DIY, jobs, cheap, etc.)\n"
            f"   - pause_campaign.py (if needed)\n"
            f"6. Link Google Ads <-> GA4 for conversion import\n"
        ),
        "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/New_York"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
                {"method": "email", "minutes": 60},
            ],
        },
    }
    try:
        ev = cal.events().insert(calendarId="primary", body=body).execute()
        print(f"Calendar event created: {ev.get('htmlLink')}")
        return ev.get("htmlLink")
    except Exception as e:
        print(f"Calendar event create failed: {e}")
        return None


def main() -> int:
    if already_flagged():
        print("Already flagged — exiting without re-notifying.")
        return 0

    creds = load_creds()
    match = search_gmail(creds)

    if not match:
        print("No approval email yet. Will check again next run.")
        return 0

    print("=" * 60)
    print("APPROVAL EMAIL DETECTED")
    print("=" * 60)
    print(f"FROM: {match['from']}")
    print(f"SUBJECT: {match['subject']}")
    print(f"DATE: {match['date']}")
    print(f"SNIPPET: {match['snippet']}")
    print("=" * 60)

    write_flag(match)
    cal_link = create_calendar_event(creds, match)

    # Write outputs for GitHub Actions
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"approved=true\n")
            f.write(f"subject={match['subject']}\n")
            if cal_link:
                f.write(f"calendar_link={cal_link}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
