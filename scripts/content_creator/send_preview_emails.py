#!/usr/bin/env python3
"""Send preview emails only after reviewer + content audit have passed."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pytz

from email_preview import send_preview


ET = pytz.timezone("America/New_York")
SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB = "📋 Content Queue"
WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/content_creator_run"))


def _get_token() -> str:
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
        timeout=30,
    ).read())
    return resp["access_token"]


def _col_letter(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _header_map(tab_name: str) -> dict[str, int]:
    token = _get_token()
    rng = urllib.parse.quote(f"'{tab_name}'!1:1", safe="!:")
    req = urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{rng}",
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("values", [[]])
    return {h.strip().lower(): i for i, h in enumerate(rows[0])}


def write_queue_status(row_idx: int, status: str) -> None:
    hmap = _header_map(QUEUE_TAB)
    updates = []
    if "status" in hmap:
        updates.append({
            "range": f"'{QUEUE_TAB}'!{_col_letter(hmap['status'] + 1)}{row_idx}",
            "values": [[status]],
        })
    if "date status changed" in hmap:
        updates.append({
            "range": f"'{QUEUE_TAB}'!{_col_letter(hmap['date status changed'] + 1)}{row_idx}",
            "values": [[datetime.now(ET).strftime('%Y-%m-%d')]],
        })
    if not updates:
        return
    token = _get_token()
    payload = json.dumps({"valueInputOption": "USER_ENTERED", "data": updates}).encode()
    req = urllib.request.Request(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=30)


def load_results(path: Path | None = None) -> list[dict]:
    path = path or (WORK_DIR / "results.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def passing_audited_results(results: list[dict]) -> list[dict]:
    passing = []
    for result in results:
        audit = result.get("audit_result") or {}
        if audit.get("passed") is True:
            passing.append(result)
        else:
            print(
                "  Preview blocked: "
                f"{result.get('post_id', result.get('topic', 'unknown'))} "
                f"(audit_result.passed={audit.get('passed')!r})"
            )
    return passing


def main() -> int:
    results = load_results()
    if not results:
        print("  No results.json posts — no preview emails to send")
        return 0

    to_send = passing_audited_results(results)
    if not to_send:
        print("  No audit-passing posts — preview email blocked")
        return 0

    print(f"  Sending preview email for {len(to_send)}/{len(results)} audit-passing post(s)")
    ok = send_preview(to_send, datetime.now(ET).strftime("%Y-%m-%d"))
    if ok:
        for result in to_send:
            row_idx = result.get("queue_row_idx")
            if row_idx:
                try:
                    write_queue_status(int(row_idx), "Email Sent")
                except Exception as exc:
                    print(f"  Queue status update failed for row {row_idx}: {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
