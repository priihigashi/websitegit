#!/usr/bin/env python3
"""One-off: Add a Google Calendar event for Thursday April 17 2026 — capture reel reminder."""

import os, json, base64
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

sa_b64 = os.getenv("GOOGLE_SA_KEY")
if not sa_b64:
    raise RuntimeError("GOOGLE_SA_KEY not set")

creds = Credentials.from_service_account_info(
    json.loads(base64.b64decode(sa_b64)),
    scopes=["https://www.googleapis.com/auth/calendar"],
)
cal = build("calendar", "v3", credentials=creds)

events = [
    {
        "summary": "Capture Instagram Reel 1 — Run Capture Pipeline",
        "description": (
            "Run Capture Pipeline v2 in GitHub Actions:\n\n"
            "URL: https://www.instagram.com/reel/DWy8SqnEel9/?igsh=eDAweXhrZ3VzZm84\n\n"
            "STEPS:\n"
            "1. GitHub app > oak-park-ai-hub > Actions > Capture Pipeline v2\n"
            "2. Paste the URL above\n"
            "3. Pick project type (book / sovereign / content)\n"
            "4. Run workflow\n"
        ),
        "start": {"dateTime": "2026-04-16T09:00:00", "timeZone": "America/New_York"},
        "end":   {"dateTime": "2026-04-16T09:30:00", "timeZone": "America/New_York"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
    },
    {
        "summary": "Capture Instagram Reel 2 — Run Capture Pipeline",
        "description": (
            "Run Capture Pipeline v2 in GitHub Actions:\n\n"
            "URL: https://www.instagram.com/reel/DXCcRDlEVOF/?igsh=NzNsd3BreDJlZTQ3\n\n"
            "STEPS:\n"
            "1. GitHub app > oak-park-ai-hub > Actions > Capture Pipeline v2\n"
            "2. Paste the URL above\n"
            "3. Pick project type (book / sovereign / content)\n"
            "4. Run workflow\n"
        ),
        "start": {"dateTime": "2026-04-16T09:30:00", "timeZone": "America/New_York"},
        "end":   {"dateTime": "2026-04-16T10:00:00", "timeZone": "America/New_York"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
    },
]

for i, event in enumerate(events, 1):
    result = cal.events().insert(calendarId="primary", body=event).execute()
    print(f"Calendar event {i} created: {result.get('htmlLink')}")
