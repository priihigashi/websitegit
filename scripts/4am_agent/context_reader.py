"""
context_reader.py - Reads the Claude Rules tab for recent lessons and rules.
Called by pattern_learner to give Claude awareness of new patterns, new drives,
and new project rules established since the last time the agent ran.

Every rule Priscila and Claude agree on gets written to the Claude Rules tab.
This module feeds those rules back into the 4AM agent so it self-learns
without needing a human to manually update any scripts.
"""
import os, json
import urllib.request, urllib.parse
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

SPREADSHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"


def _sheets():
    raw = os.environ["SHEETS_TOKEN"]
    td  = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        "https://oauth2.googleapis.com/token", data=data
    ).read())
    creds = Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )
    return build("sheets", "v4", credentials=creds)


def read_recent_rules(n=20):
    """Read last N rows from the Claude Rules tab."""
    try:
        result = _sheets().spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="📋 Claude Rules!A:C",
        ).execute()
        rows = result.get("values", [])
        if len(rows) <= 1:
            return []
        data_rows = rows[1:][-n:]
        return [
            {
                "date": r[0] if len(r) > 0 else "",
                "rule": r[1] if len(r) > 1 else "",
                "file": r[2] if len(r) > 2 else "",
            }
            for r in data_rows
        ]
    except Exception as e:
        print(f"[context_reader] Could not read Claude Rules tab: {e}")
        return []


def get_context_summary():
    """
    Returns a formatted string of recent Claude rules.
    Passed to pattern_learner so Claude knows what patterns and rules
    exist before deciding what to create or suggest.
    """
    rules = read_recent_rules(20)
    if not rules:
        return ""
    lines = [
        "RECENT CLAUDE RULES (from Claude Rules tab):",
        "These govern how Claude and Priscila work together.",
        "Before creating a new skill or task, check if a rule already addresses it.",
        "",
    ]
    for r in rules:
        if r["rule"]:
            lines.append(f"- [{r['date']}] {r['rule']}")
    return "\n".join(lines)
