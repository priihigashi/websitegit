#!/usr/bin/env python3
"""
unrouted_digest.py
==================
Weekly digest of capture pipeline rows the auto-detect could not classify.

Reads Ideas & Inbox > 📥 Inspiration Library tab.
Filters Status = "Not Identified" within the last 7 days.
Sends a single email summarizing each item with the URL, Drive folder link,
and the detection reason from "My Raw Notes".

If no rows match → no email sent (no spam).
"""

import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

SHEET_ID = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
TAB = "📥 Inspiration Library"
TO_EMAIL = "priscila@oakpark-construction.com"
FROM_EMAIL = "priscila@oakpark-construction.com"


def _access_token() -> str:
    raw = os.getenv("SHEETS_TOKEN", "")
    if not raw:
        raise RuntimeError("SHEETS_TOKEN env var not set")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    ).read())
    return resp["access_token"]


def _read_tab(token: str) -> list:
    enc = urllib.parse.quote(f"'{TAB}'!A:Z", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    rows = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    ).read()).get("values", [])
    return rows


def _build_html(items: list) -> str:
    rows_html = ""
    for it in items:
        rows_html += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #eee;font-family:monospace;font-size:11px">{it['date']}</td>
          <td style="padding:8px;border-bottom:1px solid #eee"><a href="{it['url']}">{it['url'][:80]}</a></td>
          <td style="padding:8px;border-bottom:1px solid #eee">{it['drive_link']}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;font-size:11px;color:#666">{it['reason']}</td>
        </tr>
        """
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333">
    <h2>Unrouted Captures — Last 7 Days</h2>
    <p>{len(items)} capture(s) the auto-detect could not classify. Open each, decide the niche, and update the Status column.</p>
    <table style="border-collapse:collapse;width:100%;background:#fafafa">
      <thead style="background:#1c1409;color:#f0e8d6">
        <tr><th style="padding:8px;text-align:left">Date</th>
            <th style="padding:8px;text-align:left">URL</th>
            <th style="padding:8px;text-align:left">Drive folder</th>
            <th style="padding:8px;text-align:left">Detection reason</th></tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    <p style="margin-top:20px"><a href="https://docs.google.com/spreadsheets/d/{SHEET_ID}">Open Inspiration Library</a></p>
    </body></html>
    """


def main():
    token = _access_token()
    rows = _read_tab(token)
    if not rows:
        print("No data in tab — exit")
        return
    headers = [h.strip() for h in rows[0]]
    h = {name.lower(): i for i, name in enumerate(headers)}

    date_col = h.get("date added")
    url_col = h.get("url")
    status_col = h.get("status")
    drive_col = h.get("drive folder path") or h.get("content hub link")
    notes_col = h.get("my raw notes")

    if status_col is None:
        print("ERROR: Status column not found")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    items = []
    for r in rows[1:]:
        if len(r) <= status_col:
            continue
        if (r[status_col] or "").strip().lower() != "not identified":
            continue
        date_str = r[date_col] if date_col is not None and len(r) > date_col else ""
        try:
            dt = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt < cutoff:
                continue
        except (ValueError, AttributeError):
            pass  # date unparseable — include it anyway
        items.append({
            "date": date_str,
            "url": r[url_col] if url_col is not None and len(r) > url_col else "",
            "drive_link": (r[drive_col] if drive_col is not None and len(r) > drive_col else ""),
            "reason": (r[notes_col] if notes_col is not None and len(r) > notes_col else "")[:300],
        })

    if not items:
        print("No unrouted captures in the last 7 days — no email sent")
        return

    html = _build_html(items)
    msg = MIMEText(html, "html")
    msg["Subject"] = f"Unrouted Captures — {len(items)} item(s) need triage"
    msg["From"] = FROM_EMAIL
    msg["To"] = TO_EMAIL

    pwd = os.getenv("PRI_OP_GMAIL_APP_PASSWORD", "")
    if not pwd:
        print("ERROR: PRI_OP_GMAIL_APP_PASSWORD not set")
        sys.exit(1)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(FROM_EMAIL, pwd)
        s.send_message(msg)
    print(f"Sent digest with {len(items)} items to {TO_EMAIL}")


if __name__ == "__main__":
    main()
