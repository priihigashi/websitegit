"""
credit_monitor.py — Pre-flight API credit and key health check.

Checks: Anthropic, OpenAI, Gemini, Pexels (optional).
Logs credit failures to Credit Blocks tab in Pipeline Fix Tracker.
Sends email alert when primary content pipeline providers are all degraded.

Exit codes:
  0 — all primary providers OK (pipeline can proceed)
  1 — primary provider degraded (warning, pipeline may still proceed with fallbacks)
  2 — all primary providers down (pipeline should abort)

Usage:
  python scripts/self_heal/credit_monitor.py
  python scripts/self_heal/credit_monitor.py --primary-only
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ANTHROPIC_KEY = os.environ.get("CLAUDE_KEY_4_CONTENT", "") or os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY    = os.environ.get("GEMINI_API_KEY", "")
PEXELS_KEY    = os.environ.get("PEXELS_API_KEY", "")

SHEETS_TOKEN  = os.environ.get("SHEETS_TOKEN", "")
GHA_RUN_ID    = os.environ.get("GH_RUN_ID", os.environ.get("GITHUB_RUN_ID", ""))
GHA_RUN_URL   = os.environ.get("GH_RUN_URL", "")
NOTIFY_EMAIL  = "priscila@oakpark-construction.com"
GMAIL_PASS    = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")

TRACKER_SS    = "1yh9C7KU9OlqCdHNDI9mbZ6ldqLA3bAR3uENXUh37bkQ"
CB_TAB        = "Credit Blocks"


def _check_anthropic() -> tuple[str, str]:
    """Returns (status, detail). status: ok/low_credit/auth/rate_limit/no_key/error."""
    if not ANTHROPIC_KEY:
        return "no_key", "CLAUDE_KEY_4_CONTENT not set"
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply OK."}],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=20)
        return "ok", "Haiku test call succeeded"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        low = body.lower()
        if "credit balance is too low" in low or "purchase credits" in low or "billing" in low:
            return "low_credit", body[:120]
        if e.code in (401, 403):
            return "auth", f"HTTP {e.code}: {body[:80]}"
        if e.code == 429:
            return "rate_limit", f"HTTP 429: {body[:80]}"
        return "error", f"HTTP {e.code}: {body[:80]}"
    except Exception as exc:
        return "error", str(exc)[:120]


def _check_openai() -> tuple[str, str]:
    if not OPENAI_KEY:
        return "no_key", "OPENAI_API_KEY not set"
    payload = json.dumps({
        "model": "gpt-4o-mini", "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply OK."}],
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=20)
        return "ok", "gpt-4o-mini test call succeeded"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        low = body.lower()
        if "insufficient_quota" in low or "billing" in low:
            return "low_credit", body[:120]
        if e.code in (401, 403):
            return "auth", f"HTTP {e.code}: {body[:80]}"
        if e.code == 429:
            return "rate_limit", f"HTTP 429: {body[:80]}"
        return "error", f"HTTP {e.code}: {body[:80]}"
    except Exception as exc:
        return "error", str(exc)[:120]


def _check_gemini() -> tuple[str, str]:
    if not GEMINI_KEY:
        return "no_key", "GEMINI_API_KEY not set"
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-1.5-flash-latest:generateContent?key={GEMINI_KEY}")
        payload = json.dumps({"contents": [{"parts": [{"text": "Reply OK."}]}],
                               "generationConfig": {"maxOutputTokens": 8}}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
        return "ok", "Gemini Flash test call succeeded"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        low = body.lower()
        if "quota" in low or "billing" in low:
            return "low_credit", body[:120]
        if e.code in (400, 401, 403):
            return "auth", f"HTTP {e.code}: {body[:80]}"
        if e.code == 429:
            return "rate_limit", f"HTTP 429: {body[:80]}"
        return "error", f"HTTP {e.code}: {body[:80]}"
    except Exception as exc:
        return "error", str(exc)[:120]


def _log_credit_failure(api: str, error: str, workflow: str = "credit_monitor.yml") -> None:
    """Append a row to Credit Blocks tab via Sheets API."""
    if not SHEETS_TOKEN:
        print(f"  [credit_monitor] SHEETS_TOKEN missing — cannot log to Credit Blocks")
        return
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        data = json.loads(SHEETS_TOKEN)
        creds = Credentials(
            token=data.get("token") or data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes") or ["https://www.googleapis.com/auth/spreadsheets"],
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        row = [
            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            workflow,
            GHA_RUN_ID or "—",
            "credit_monitor/pre-flight",
            error[:200],
            GHA_RUN_URL or "—",
            "",
            api,
        ]
        body = json.dumps({"values": [row]}).encode()
        import urllib.parse as _up
        _range = _up.quote(f"{CB_TAB}!A1", safe="")
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{TRACKER_SS}"
               f"/values/{_range}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
        req = urllib.request.Request(url, data=body, method="POST",
            headers={"Authorization": f"Bearer {creds.token}",
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        print(f"  [credit_monitor] Logged {api} credit failure to Credit Blocks")
    except Exception as exc:
        print(f"  [credit_monitor] Credit Blocks log failed (non-fatal): {exc}")


def _send_alert(subject: str, body: str) -> None:
    """Send email via SMTP using PRI_OP_GMAIL_APP_PASSWORD."""
    if not GMAIL_PASS:
        print(f"  [credit_monitor] No GMAIL_PASS — alert not sent: {subject}")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = NOTIFY_EMAIL
        msg["To"] = NOTIFY_EMAIL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(NOTIFY_EMAIL, GMAIL_PASS)
            smtp.send_message(msg)
        print(f"  [credit_monitor] Alert sent: {subject}")
    except Exception as exc:
        print(f"  [credit_monitor] Alert send failed (non-fatal): {exc}")


def main() -> int:
    primary_only = "--primary-only" in sys.argv
    print(f"[credit_monitor] {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} — checking API health")

    results: dict[str, tuple[str, str]] = {}

    print("  Checking Anthropic...", end=" ", flush=True)
    results["Anthropic"] = _check_anthropic()
    print(results["Anthropic"][0])

    print("  Checking OpenAI...", end=" ", flush=True)
    results["OpenAI"] = _check_openai()
    print(results["OpenAI"][0])

    if not primary_only:
        print("  Checking Gemini...", end=" ", flush=True)
        results["Gemini"] = _check_gemini()
        print(results["Gemini"][0])

    # Summarize
    degraded = [api for api, (status, _) in results.items()
                if status in ("low_credit", "auth", "error")]
    no_key   = [api for api, (status, _) in results.items() if status == "no_key"]
    ok_apis  = [api for api, (status, _) in results.items() if status in ("ok", "rate_limit")]

    print(f"\n[credit_monitor] Summary: OK={ok_apis}, degraded={degraded}, no_key={no_key}")

    # Log credit failures
    for api in degraded:
        status, detail = results[api]
        if status in ("low_credit", "auth"):
            _log_credit_failure(api, f"{status}: {detail}")

    # Determine exit code and whether to alert
    # Primary providers for content pipeline: Anthropic + OpenAI
    primary_degraded = [a for a in ("Anthropic", "OpenAI") if a in degraded]
    primary_no_key   = [a for a in ("Anthropic", "OpenAI") if a in no_key]
    primary_ok       = [a for a in ("Anthropic", "OpenAI") if a in ok_apis]

    if not primary_ok:
        # All primary providers down — pipeline will fail
        alert_body = (
            f"CRITICAL: All primary AI providers are down. Content pipeline will fail.\n\n"
            + "\n".join(f"  {api}: {status} — {detail}"
                        for api, (status, detail) in results.items())
            + f"\n\nRun: {GHA_RUN_URL or '(local)'}"
        )
        _send_alert("🚨 Credit Monitor: ALL PRIMARY PROVIDERS DOWN", alert_body)
        return 2

    if primary_degraded:
        # Some degraded, at least one OK — warn but allow pipeline to proceed
        alert_body = (
            f"WARNING: Some AI providers are degraded. Fallbacks will be used.\n\n"
            + "\n".join(f"  {api}: {status} — {detail}"
                        for api, (status, detail) in results.items()
                        if api in primary_degraded)
            + f"\n\nOK providers: {', '.join(primary_ok)}\n"
            + f"Run: {GHA_RUN_URL or '(local)'}"
        )
        _send_alert("⚠️ Credit Monitor: Provider degraded — fallback active", alert_body)
        return 1

    print("[credit_monitor] All primary providers OK — pipeline can proceed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
