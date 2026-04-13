"""
notifier.py -- Notifications for the 4AM agent.
  1. ntfy.sh push  — immediate phone alert (success + failure)
  2. HTML email    — rich success email via SMTP (success only)

NTFY_TOPIC secret controls which ntfy topic to publish to.
"""
import os, json, requests, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "oak-park-content-4am")
NTFY_BASE     = "https://ntfy.sh"
GITHUB_REPO   = "priihigashi/oak-park-ai-hub"
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
GMAIL_USER    = os.environ.get("PRI_OP_GMAIL_USER", "priscila@oakpark-construction.com")
GMAIL_PASS    = os.environ.get("PRI_OP_GMAIL_APP_PASSWORD", "")

SHEETS_BASE = "https://docs.google.com/spreadsheets/d/1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU/edit"
CALENDAR_URL = "https://calendar.google.com"

CREDIT_ERRORS = [
    "not-enough-usage-to-run-paid-actor",
    "x402-payment-required",
    "payment-required",
    "insufficient",
]


def _is_credit_error(error_str):
    e = error_str.lower()
    return any(k in e for k in CREDIT_ERRORS)


# ─── ntfy.sh ──────────────────────────────────────────────────────────────────

def send(title, message, priority="default", tags="robot"):
    resp = requests.post(
        f"{NTFY_BASE}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title":    title.encode("utf-8"),
            "Priority": priority,
            "Tags":     tags,
        },
        timeout=10,
    )
    return resp.status_code == 200


# ─── HTML email ───────────────────────────────────────────────────────────────

def _build_html_success(topics, rows_added, clips_found, run_date):
    """Build rich HTML email body for a successful 4AM run."""
    github_run_url = (
        f"https://github.com/{GITHUB_REPO}/actions/runs/{GITHUB_RUN_ID}"
        if GITHUB_RUN_ID else f"https://github.com/{GITHUB_REPO}/actions"
    )
    topic_items = "".join(
        f'<li style="margin:4px 0;color:#444;font-size:14px">{t}</li>'
        for t in topics
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:20px 0">
<tr><td>
<table width="600" align="center" cellpadding="0" cellspacing="0"
       style="margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,0.12)">

  <!-- Header -->
  <tr>
    <td style="background:#1a1a2e;padding:24px 28px">
      <h1 style="color:#ffffff;margin:0;font-size:22px;font-weight:700">
        &#x2705; 4AM Content Ready
      </h1>
      <p style="color:#8888aa;margin:6px 0 0;font-size:13px">
        {run_date} &nbsp;&bull;&nbsp; Oak Park Construction
      </p>
    </td>
  </tr>

  <!-- Pipeline results -->
  <tr>
    <td style="padding:24px 28px;border-left:4px solid #4caf50">
      <h2 style="color:#222;font-size:15px;margin:0 0 10px;text-transform:uppercase;
                 letter-spacing:0.5px">&#x1F3AC; Content Generated</h2>
      <p style="margin:0 0 4px;font-size:15px;color:#333">
        <strong>{rows_added}</strong> scripts added &nbsp;&bull;&nbsp;
        <strong>{clips_found}</strong> B-roll clips found
      </p>
      <ul style="margin:10px 0 0 0;padding-left:18px">
        {topic_items}
      </ul>
    </td>
  </tr>

  <!-- Quick links -->
  <tr>
    <td style="padding:20px 28px;background:#f8f8f8;border-top:1px solid #eeeeee">
      <h2 style="color:#222;font-size:14px;margin:0 0 12px;text-transform:uppercase;
                 letter-spacing:0.5px">&#x1F517; Quick Links</h2>
      <a href="{SHEETS_BASE}" target="_blank"
         style="display:inline-block;margin:4px 6px 4px 0;padding:9px 16px;
                background:#4caf50;color:#ffffff;border-radius:5px;
                text-decoration:none;font-size:13px;font-weight:600">
        &#x1F4CB; Content Queue
      </a>
      <a href="{CALENDAR_URL}" target="_blank"
         style="display:inline-block;margin:4px 6px 4px 0;padding:9px 16px;
                background:#2196f3;color:#ffffff;border-radius:5px;
                text-decoration:none;font-size:13px;font-weight:600">
        &#x1F4C5; Calendar
      </a>
      <a href="{github_run_url}" target="_blank"
         style="display:inline-block;margin:4px 6px 4px 0;padding:9px 16px;
                background:#24292e;color:#ffffff;border-radius:5px;
                text-decoration:none;font-size:13px;font-weight:600">
        &#x1F419; Run Log
      </a>
      <a href="{SHEETS_BASE}" target="_blank"
         style="display:inline-block;margin:4px 6px 4px 0;padding:9px 16px;
                background:#9c27b0;color:#ffffff;border-radius:5px;
                text-decoration:none;font-size:13px;font-weight:600">
        &#x1F4CA; Runs Log
      </a>
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="padding:14px 28px;background:#1a1a2e;text-align:center">
      <p style="color:#555566;font-size:11px;margin:0">
        Oak Park Construction &bull; 4AM Content Agent &bull; Auto-generated
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _dispatch_html_email(subject, html_body, to="priscila@oakpark-construction.com"):
    """Send HTML email directly via SMTP using Gmail App Password."""
    if not GMAIL_PASS:
        print("[notifier] No PRI_OP_GMAIL_APP_PASSWORD — skipping HTML email.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = to
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to, msg.as_string())
        print("[notifier] HTML email sent via SMTP: OK")
        return True
    except Exception as e:
        print(f"[notifier] HTML email SMTP error: {e}")
        return False


# ─── Public API ───────────────────────────────────────────────────────────────

def notify_run_complete(topics, rows_added, clips_found, error=None):
    if error and _is_credit_error(error):
        return send(
            title="Apify Credits Empty",
            message=(
                "4AM agent ran in fallback mode — Apify has no credits left.\n\n"
                "Action needed: go to console.apify.com/billing and top up.\n\n"
                "Agent still generated scripts using Claude directly."
            ),
            priority="high",
            tags="warning,credit_card",
        )

    if error:
        return send(
            title="4AM Agent Failed",
            message=f"Error: {error}\nCheck Runs Log tab for details.",
            priority="high",
            tags="warning",
        )

    # Success: ntfy push + HTML email
    topic_list = "\n".join(f"- {t}" for t in topics)
    ntfy_msg   = (
        f"{rows_added} scripts added to Content Queue\n"
        f"{clips_found} B-roll clips found\n\n"
        f"Topics:\n{topic_list}"
    )
    ntfy_ok = send(title="4AM Content Ready", message=ntfy_msg, tags="tada,robot")

    from datetime import datetime
    import pytz
    run_date  = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
    html_body = _build_html_success(topics, rows_added, clips_found, run_date)
    subject   = f"✅ 4AM Content Ready — {run_date} ({rows_added} scripts, {clips_found} clips)"
    _dispatch_html_email(subject, html_body)

    return ntfy_ok


def notify_new_skill(skill_name, pattern_summary):
    message = (
        f"Pattern detected in run logs.\n"
        f"Auto-created: skills/{skill_name}\n\n"
        f"Pattern: {pattern_summary}"
    )
    return send(title="New Skill Auto-Created", message=message, tags="brain,robot")


def notify_skill_task(task_title, description):
    message = (
        f"A pattern was found in run logs that needs a new skill.\n\n"
        f"{description}\n\n"
        f"Calendar task created: '{task_title}'"
    )
    return send(title="Skill Task Added to Calendar", message=message, tags="calendar,robot")
