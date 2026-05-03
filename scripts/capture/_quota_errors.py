"""
Quota / billing / auth error detection for the capture pipeline.

Classifies exception text or stderr from every paid API we depend on
(OpenAI, Apify, Anthropic, Gemini, Pexels) and returns a structured
error dict so the pipeline can:
  1. Write a clear one-line reason to the Capture Queue sheet (not "⚠️ Pipeline failed")
  2. Email Priscila with the exact service + fix link
  3. Decide whether a fallback path should be tried

Shared module — imported by capture_pipeline.py and capture_queue_processor.py.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from typing import Optional

_GMAIL_FROM     = os.environ.get("GMAIL_FROM", "priscila@oakpark-construction.com")
_GMAIL_PASSWORD = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")


# Order matters — first match wins. Most specific patterns first.
_PATTERNS = [
    # OpenAI
    {"service": "OpenAI",    "type": "quota_exceeded",   "patterns": ["insufficient_quota", "exceeded your current quota"],
     "fix_url": "https://platform.openai.com/account/billing",
     "fix_action": "Add credits or enable auto-recharge"},
    {"service": "OpenAI",    "type": "rate_limit",        "patterns": ["rate_limit_exceeded", "RateLimitError"],
     "fix_url": "https://platform.openai.com/account/limits",
     "fix_action": "Wait or upgrade tier"},
    {"service": "OpenAI",    "type": "invalid_api_key",   "patterns": ["invalid_api_key", "Incorrect API key provided"],
     "fix_url": "https://platform.openai.com/account/api-keys",
     "fix_action": "Regenerate OPENAI_API_KEY secret"},
    # Apify
    {"service": "Apify",     "type": "quota_exceeded",   "patterns": ["monthly usage hard limit", "usage-hard-limit-exceeded", "platform-feature-disabled"],
     "fix_url": "https://console.apify.com/billing",
     "fix_action": "Increase monthly limit or wait for reset"},
    {"service": "Apify",     "type": "auth_failed",       "patterns": ["invalid-token", "User not authenticated"],
     "fix_url": "https://console.apify.com/account/integrations",
     "fix_action": "Regenerate APIFY_API_KEY secret"},
    # Anthropic / Claude
    {"service": "Anthropic", "type": "quota_exceeded",   "patterns": ["credit balance is too low", "insufficient_credit"],
     "fix_url": "https://console.anthropic.com/settings/billing",
     "fix_action": "Top up Anthropic credits"},
    {"service": "Anthropic", "type": "rate_limit",        "patterns": ["rate_limit_error", "overloaded_error"],
     "fix_url": "https://console.anthropic.com/settings/limits",
     "fix_action": "Wait or upgrade Claude tier"},
    # Google / Gemini
    {"service": "Gemini",    "type": "quota_exceeded",   "patterns": ["RESOURCE_EXHAUSTED", "quota exceeded", "Quota exceeded"],
     "fix_url": "https://console.cloud.google.com/apis/api/generativelanguage.googleapis.com/quotas",
     "fix_action": "Enable billing or request quota increase"},
    # Stock tiers
    {"service": "Pexels",    "type": "rate_limit",        "patterns": ["Pexels API rate"],
     "fix_url": "https://www.pexels.com/api/",
     "fix_action": "Wait for Pexels rate-limit reset"},
    # Generic HTTP hints (last resort — only if nothing else matched)
    {"service": "Unknown",   "type": "payment_required",  "patterns": ["402 Payment Required"],
     "fix_url": "",
     "fix_action": "Billing issue — investigate"},
]


def classify_error(error_text: str) -> Optional[dict]:
    """Return a classified dict when text matches a known quota/auth pattern, else None."""
    if not error_text:
        return None
    lower = error_text.lower()
    for rule in _PATTERNS:
        for p in rule["patterns"]:
            if p.lower() in lower:
                return {
                    "service":    rule["service"],
                    "type":       rule["type"],
                    "fix_url":    rule["fix_url"],
                    "fix_action": rule["fix_action"],
                    "snippet":    error_text.strip()[-600:],
                }
    return None


def short_sheet_message(classified: dict, url: str = "") -> str:
    """One-line message for the Capture Queue sheet cell."""
    tag = f"🔴 {classified['service']}: {classified['type']}"
    action = classified.get("fix_action", "").strip()
    return f"{tag} — {action}" if action else tag


def send_quota_alert_email(classified: dict, context: str = "", url: str = ""):
    """Email Priscila with the full quota/billing error + fix link. Non-fatal on failure.

    THROTTLED (added 2026-05-03 — see THROTTLE/DEDUP block below):
      - Max 1 email per (service, error_type) per 24 hours.
      - All suppressed failures are counted and reported in the daily reminder.
    """
    if not _GMAIL_PASSWORD:
        print("  SKIP quota alert: PRI_OP_GMAIL_APP_PASSWORD not set")
        return

    # NEW: throttle gate
    try:
        should_send, suppressed_count = should_send_quota_alert(classified)
    except Exception as e:
        # If the throttle layer breaks, fail open — better to send than not.
        print(f"  WARNING quota throttle layer error (failing open): {e}")
        should_send, suppressed_count = True, 0

    if not should_send:
        try:
            record_quota_alert_suppressed(classified)
        except Exception:
            pass
        print(f"  SUPPRESSED quota alert (already sent within 24h): {classified.get('service')} {classified.get('type')}")
        return

    subject = f"⚠️ {classified['service']} {classified['type'].upper()} — capture pipeline blocked"
    body = (
        f"The capture pipeline detected an API quota/billing/auth error.\n\n"
        f"SERVICE:    {classified['service']}\n"
        f"ERROR TYPE: {classified['type']}\n"
        f"FIX:        {classified['fix_action']}\n"
        f"LINK:       {classified['fix_url']}\n"
    )
    if url:
        body += f"\nURL THAT TRIGGERED IT: {url}\n"
    if context:
        body += f"\nCONTEXT: {context}\n"
    body += f"\n--- RAW ERROR SNIPPET ---\n{classified['snippet']}\n"
    if suppressed_count > 0:
        body += (
            f"\n--- DAILY REMINDER ---\n"
            f"This is the 24-hour reminder. Since the last email,\n"
            f"{suppressed_count} additional failure(s) were suppressed.\n"
            f"Reply by topping up credits — you will get an 'all-clear'\n"
            f"email once the pipeline runs successfully again.\n"
        )
    else:
        body += (
            f"\n--- INBOX PROTECTION ---\n"
            f"You will only receive ONE email per 24 hours per error type.\n"
            f"Once you fix this, you'll get a single 'all-clear' email,\n"
            f"then nothing further.\n"
        )

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = _GMAIL_FROM
        msg["To"]      = _GMAIL_FROM
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(_GMAIL_FROM, _GMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"  Quota-alert email sent: {subject}")
        try:
            record_quota_alert_sent(classified)
        except Exception:
            pass
    except Exception as e:
        print(f"  WARNING quota alert email failed (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────────────
# THROTTLE / DEDUP — added 2026-05-03 to prevent inbox flooding (NN-S2 append-only)
# ─────────────────────────────────────────────────────────────────────────
# Problem fixed: prior version sent one email per failed API call. A 30-min
# pipeline run with 54 retries produced 54 inbox emails and overwhelmed Priscila.
#
# New rules:
#   - Per (service, error_type), max ONE alert email per 24 hours.
#   - State persisted in a small JSON file in /tmp (cycle-local) AND mirrored
#     to a sentinel file path injected via env QUOTA_ALERT_STATE_PATH (Drive
#     mount or workspace path) so state survives across cycles.
#   - When a (service, error_type) goes 1+ hour without recurrence after the
#     last alert, the next successful run sends an "all clear" email and
#     resets the counter. Implemented via mark_quota_cleared().
#   - Each suppressed alert increments a counter so the daily-summary
#     mode can report "63 additional failures suppressed."
# ─────────────────────────────────────────────────────────────────────────

import json as _qa_json
import time as _qa_time
from pathlib import Path as _qa_Path

_QA_STATE_PATH = _qa_Path(os.environ.get(
    "QUOTA_ALERT_STATE_PATH",
    "/tmp/_quota_alert_state.json"
))
_QA_DAILY_REMIND_HOURS = 24
_QA_CLEAR_AFTER_HOURS = 1


def _qa_load_state() -> dict:
    try:
        if _QA_STATE_PATH.exists():
            return _qa_json.loads(_QA_STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _qa_save_state(state: dict) -> None:
    try:
        _QA_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _QA_STATE_PATH.write_text(_qa_json.dumps(state, indent=2))
    except Exception as e:
        print(f"  WARNING quota-alert state save failed (non-fatal): {e}")


def _qa_key(classified: dict) -> str:
    return f"{classified.get('service','?')}::{classified.get('type','?')}"


def should_send_quota_alert(classified: dict) -> tuple[bool, int]:
    """Decide whether to actually send the email.

    Returns (should_send, suppressed_since_last_send).

    Logic:
      - First-ever alert for this (service, type)            → SEND
      - Last alert >= 24h ago                                  → SEND (daily reminder)
      - Last alert <  24h ago                                  → SUPPRESS, increment counter
    """
    state = _qa_load_state()
    key = _qa_key(classified)
    rec = state.get(key, {})
    now = _qa_time.time()
    last_sent = float(rec.get("last_sent_ts", 0.0))
    suppressed = int(rec.get("suppressed_since_send", 0))
    last_seen = float(rec.get("last_seen_ts", 0.0))

    if last_sent <= 0:
        return True, 0

    age_h = (now - last_sent) / 3600.0
    if age_h >= _QA_DAILY_REMIND_HOURS:
        return True, suppressed
    return False, suppressed


def record_quota_alert_sent(classified: dict) -> None:
    state = _qa_load_state()
    key = _qa_key(classified)
    rec = state.get(key, {})
    rec["last_sent_ts"] = _qa_time.time()
    rec["last_seen_ts"] = _qa_time.time()
    rec["suppressed_since_send"] = 0
    rec["service"] = classified.get("service")
    rec["type"]    = classified.get("type")
    state[key] = rec
    _qa_save_state(state)


def record_quota_alert_suppressed(classified: dict) -> None:
    state = _qa_load_state()
    key = _qa_key(classified)
    rec = state.get(key, {})
    rec["last_seen_ts"] = _qa_time.time()
    rec["suppressed_since_send"] = int(rec.get("suppressed_since_send", 0)) + 1
    state[key] = rec
    _qa_save_state(state)


def mark_quota_cleared(service: str, error_type: str) -> bool:
    """Send an 'all-clear' email when an issue stops occurring.

    Call this at the start of a successful pipeline run for any service+type
    that was previously failing. Returns True if an all-clear email was sent.
    """
    state = _qa_load_state()
    key = f"{service}::{error_type}"
    rec = state.get(key)
    if not rec:
        return False  # nothing to clear
    last_seen = float(rec.get("last_seen_ts", 0.0))
    last_sent = float(rec.get("last_sent_ts", 0.0))
    age_since_seen_h = (_qa_time.time() - last_seen) / 3600.0
    if last_sent <= 0:
        return False  # we never alerted in the first place
    if age_since_seen_h < _QA_CLEAR_AFTER_HOURS:
        return False  # still recurring within the last hour, premature
    # Send the all-clear
    suppressed = int(rec.get("suppressed_since_send", 0))
    if not _GMAIL_PASSWORD:
        del state[key]
        _qa_save_state(state)
        return False
    subject = f"✅ {service} {error_type.upper()} — RESOLVED (capture pipeline running again)"
    body = (
        f"The capture pipeline ran successfully again.\n\n"
        f"SERVICE:    {service}\n"
        f"ERROR TYPE: {error_type}\n"
        f"STATUS:     RESOLVED — no action needed.\n"
        f"\n"
        f"While this was failing, {suppressed} additional alert(s) were\n"
        f"suppressed to keep your inbox clean.\n"
        f"\n"
        f"You can archive this email — it is the close-loop confirmation.\n"
    )
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = _GMAIL_FROM
        msg["To"]      = _GMAIL_FROM
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(_GMAIL_FROM, _GMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"  All-clear email sent: {subject}")
    except Exception as e:
        print(f"  WARNING all-clear email failed (non-fatal): {e}")
    # Clear state for this key — next failure will count as fresh
    del state[key]
    _qa_save_state(state)
    return True
