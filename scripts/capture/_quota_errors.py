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
    """Email Priscila with the full quota/billing error + fix link. Non-fatal on failure."""
    if not _GMAIL_PASSWORD:
        print("  SKIP quota alert: PRI_OP_GMAIL_APP_PASSWORD not set")
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

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = _GMAIL_FROM
        msg["To"]      = _GMAIL_FROM
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(_GMAIL_FROM, _GMAIL_PASSWORD)
            smtp.send_message(msg)
        print(f"  Quota-alert email sent: {subject}")
    except Exception as e:
        print(f"  WARNING quota alert email failed (non-fatal): {e}")
