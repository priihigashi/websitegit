"""
Pipeline Self-Heal Orchestrator
================================
One cycle = one task. Triggered every 2 hours by pipeline_self_heal.yml.

CYCLE STAGES
------------
 0. Check 3-day pause counter. If 36+ consecutive cycles completed since last
    ack, halt and email Priscila. Re-email every 24h until she resumes via
    workflow_dispatch with ack_pause=true.
 1. Authenticate (Sheets + Drive + GitHub).
 2. Read Self-Heal Queue. Pick highest-priority PENDING (or TARGET_ID).
 3. Verify bug still present (re-run affected workflow + check conclusion).
 4. Backup target file -> Drive Backups folder (timestamped copy).
 5. Pull current file content from GitHub.
 6. Ask Claude -> patch_a (proposed full file + rationale + risk).
 7. Ask OpenAI -> patch_b (proposed full file + rationale + risk).
 8. Compare patches:
      - If both agree on the fix region -> apply Claude's patch (closer to repo context).
      - If they disagree -> log both, mark NEEDS-REVIEW, escalate via email.
 9. Open feature branch self-heal/SH-XXX-<slug>, commit patch.
10. Trigger smoke-test workflow on the branch (the workflow named in queue row).
11. If green AND not dry_run -> fast-forward merge to main + delete branch.
12. If red -> re-prompt both AIs with the new error log, retry up to 3 attempts.
13. Write Fixing Log Google Doc with: links, before/after diff, smoke-test
    link, test artifact link (if a carousel was built), date/time stamps.
14. Update queue row (Status, Attempts, Last Result, Fix Log Link).
15. Update master checklist doc (PIPELINE FIX > Fixing Log > _CHECKLIST.md).
16. If queue empty -> write FINAL REPORT (red NO MORE ISSUES banner),
    run 1 example build per niche/format, ask OpenAI to audit the FINAL
    REPORT, then write the audit-confirmed FINAL.

3-DAY PAUSE LOGIC
-----------------
- Counter cell: queue tab cell N1 stores "consecutive_cycles_since_ack"
- Counter cell O1 stores ISO timestamp of last pause-email-sent
- After 36 cycles -> if O1 is empty OR more than 24h ago -> send email + update O1
- ack_pause=true -> reset N1 to 0 and clear O1

DUAL-AI BUDGET (per cycle, worst case)
--------------------------------------
- Claude: ~$0.10 (read code + propose patch + retry context)
- OpenAI: ~$0.40 (review patch + final-report audit)
- Total: ~$0.50/cycle. 12 cycles/day cap = $6/day, ~$180/mo worst case.
"""
from __future__ import annotations
import os
import sys
import json
import time
import base64
import smtplib
import datetime as dt
import subprocess
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

import requests
import gspread
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as gbuild
from googleapiclient.http import MediaInMemoryUpload
import anthropic
from openai import OpenAI
from github import Github

# ── CONSTANTS ───────────────────────────────────────────────────────────────
REPO_OWNER         = "priihigashi"
REPO_NAME          = "oak-park-ai-hub"
DEFAULT_BRANCH     = "main"
SPREADSHEET_ID     = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
QUEUE_TAB          = "🔧 Self-Heal Queue"
FIXING_LOG_FOLDER  = "1fs5VsZcmXJUFfXeu4hnStPqdePUP7gwV"
BACKUPS_FOLDER     = "1yiX5NB72IrW_FnSnOnCnsMzsM0NZJYyB"
PIPELINE_FIX_ROOT  = "1FHPkx8VA6c-Wmy6hI3uX_weSPwJPBp3z"
CHECKLIST_FILENAME = "_CHECKLIST.md"
NOTIFY_EMAIL       = "priscila@oakpark-construction.com"
BASELINE_TAG       = "self-heal-baseline-2026-05-03"
PAUSE_THRESHOLD    = 36  # 36 cycles * 2h = 72h = 3 days
PAUSE_REMIND_HOURS = 24  # re-email every 24h until acked

CLAUDE_MODEL = "claude-sonnet-4-5"  # Sonnet cheaper than Opus for patch generation
OPENAI_MODEL = "gpt-4o"

PRIORITY_ORDER = {"P0-CRITICAL": 0, "P1-HIGH": 1, "P2-MED": 2, "P3-LOW": 3, "USER-ONLY": 99}

# Loaded by main() at the start of every cycle from NONNEGOTIABLES.md
NONNEGOTIABLES_TEXT: str = ""
DETAILED_REPORT_TEXT: str = ""

# ── INIT ────────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(f"[{dt.datetime.utcnow().isoformat(timespec='seconds')}Z] {msg}", flush=True)

def fail(msg: str, code: int = 1) -> None:
    log(f"FATAL: {msg}")
    sys.exit(code)

def get_creds() -> Credentials:
    """Mirrors capture_pipeline._get_creds — manual refresh, no scope override."""
    import urllib.request, urllib.parse
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        fail("SHEETS_TOKEN env var missing")
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
    return Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
    )

def gh_client() -> Github:
    token = os.environ.get("GH_TOKEN")
    if not token:
        fail("GH_TOKEN missing")
    return Github(token)

def claude_client() -> anthropic.Anthropic:
    key = os.environ.get("CLAUDE_KEY_4_CONTENT")
    if not key:
        fail("CLAUDE_KEY_4_CONTENT missing")
    return anthropic.Anthropic(api_key=key)

def openai_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        fail("OPENAI_API_KEY missing")
    return OpenAI(api_key=key)

# ── QUEUE OPERATIONS ────────────────────────────────────────────────────────
def open_queue(creds: Credentials) -> gspread.Worksheet:
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(QUEUE_TAB)

def read_queue(ws: gspread.Worksheet) -> list[dict]:
    rows = ws.get_all_records()
    return rows

def update_queue_row(ws: gspread.Worksheet, task_id: str, **fields: Any) -> None:
    """Update specific columns for the row matching task_id (col A)."""
    cells = ws.col_values(1)  # column A = ID
    for idx, val in enumerate(cells, start=1):
        if val == task_id:
            row_num = idx
            break
    else:
        log(f"WARN: task {task_id} not found in queue")
        return
    headers = ws.row_values(1)
    for col_name, val in fields.items():
        if col_name not in headers:
            continue
        col_idx = headers.index(col_name) + 1
        ws.update_cell(row_num, col_idx, val)

def get_pause_counter(ws: gspread.Worksheet) -> tuple[int, Optional[str]]:
    """Read N1 (counter) and O1 (last-pause-email ISO)."""
    try:
        n1 = ws.acell("N1").value or "0"
        o1 = ws.acell("O1").value or ""
    except Exception:
        return 0, None
    try:
        return int(n1), (o1 or None)
    except ValueError:
        return 0, (o1 or None)

def set_pause_counter(ws: gspread.Worksheet, count: int, last_email: Optional[str]) -> None:
    ws.update_acell("N1", str(count))
    ws.update_acell("O1", last_email or "")

# ── PAUSE / NOTIFY ──────────────────────────────────────────────────────────
def send_email(subject: str, body_html: str) -> None:
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        log("WARN: GMAIL_APP_PASSWORD missing — skipping email")
        return
    msg = MIMEText(body_html, "html")
    msg["Subject"] = subject
    msg["From"]    = NOTIFY_EMAIL
    msg["To"]      = NOTIFY_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(NOTIFY_EMAIL, pw)
            s.send_message(msg)
        log(f"EMAIL SENT: {subject}")
    except Exception as e:
        log(f"WARN email failed: {e}")

def maybe_pause(ws: gspread.Worksheet) -> bool:
    """Return True if the cycle must halt due to 3-day pause."""
    if os.environ.get("ACK_PAUSE", "").lower() == "true":
        set_pause_counter(ws, 0, None)
        log("PAUSE ACKED — counter reset")
        return False
    count, last_email = get_pause_counter(ws)
    if count < PAUSE_THRESHOLD:
        return False

    now = dt.datetime.utcnow()
    last = None
    if last_email:
        try:
            last = dt.datetime.fromisoformat(last_email.replace("Z", ""))
        except ValueError:
            last = None
    hours_since = (now - last).total_seconds() / 3600 if last else 9999

    if hours_since >= PAUSE_REMIND_HOURS:
        body = f"""<h2>Pipeline Self-Heal — 3-day check-in</h2>
        <p>The self-heal bot has run {count} cycles since your last
        confirmation (~{count*2} hours / {count/12:.1f} days).</p>
        <p><b>To resume:</b> trigger the workflow manually with
        <code>ack_pause = true</code>:</p>
        <p><a href="https://github.com/{REPO_OWNER}/{REPO_NAME}/actions/workflows/pipeline_self_heal.yml">
        Open workflow → Run workflow → ack_pause = true → Run workflow</a></p>
        <p>You will receive this email every 24 hours until acknowledged.</p>
        <hr>
        <p><small>Run: <a href="{os.environ.get('GH_RUN_URL','')}">{os.environ.get('GH_RUN_ID','')}</a></small></p>
        """
        send_email("[Self-Heal] Confirm continuation (3-day check-in)", body)
        set_pause_counter(ws, count, now.isoformat(timespec="seconds") + "Z")
    log("PAUSED — exiting without work this cycle")
    return True

# ── TASK SELECTION ──────────────────────────────────────────────────────────
def pick_task(rows: list[dict], target_id: str = "", force: bool = False) -> Optional[dict]:
    if target_id:
        for r in rows:
            if r.get("ID") == target_id:
                return r
        return None
    candidates = []
    for r in rows:
        status = (r.get("Status") or "").upper()
        if status == "DONE":
            continue
        if status == "USER-ONLY":
            continue
        if status == "BLOCKED" and not force:
            continue
        try:
            attempts = int(r.get("Attempts") or 0)
        except ValueError:
            attempts = 0
        if attempts >= 3 and not force:
            continue
        prio = PRIORITY_ORDER.get(r.get("Priority") or "P3-LOW", 99)
        candidates.append((prio, attempts, r))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]

# ── BUG VERIFICATION ────────────────────────────────────────────────────────
def trigger_workflow(gh: Github, workflow_filename: str, ref: str = DEFAULT_BRANCH,
                     inputs: Optional[dict] = None) -> Optional[int]:
    """Dispatch a workflow_dispatch run. Returns run_id if locatable."""
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    try:
        wf = repo.get_workflow(workflow_filename)
        ok = wf.create_dispatch(ref=ref, inputs=inputs or {})
        if not ok:
            log(f"dispatch returned False for {workflow_filename}")
            return None
    except Exception as e:
        log(f"dispatch error {workflow_filename}: {e}")
        return None
    time.sleep(8)
    runs = wf.get_runs(branch=ref, event="workflow_dispatch")
    try:
        return runs[0].id
    except Exception:
        return None

def wait_for_run(gh: Github, run_id: int, timeout_s: int = 1500) -> str:
    """Poll until conclusion. Returns 'success' / 'failure' / 'timeout'."""
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = repo.get_workflow_run(run_id)
        if run.status == "completed":
            return run.conclusion or "unknown"
        time.sleep(15)
    return "timeout"

# ── VERIFICATION TIMEOUT HELPER (added 2026-05-03 — NN-S2 append-only) ──────
# Prior behavior: verify-bug step waited up to 900s for child workflow to
# complete, then treated timeout/failure/unknown identically. This allowed
# the orchestrator to hang silently when the very workflow being fixed is
# itself broken (chicken-and-egg).
#
# New behavior:
#   - Per-task timeout via the optional "Verification Timeout" sheet column,
#     default 600s (10 min). Read from task dict; falls back to 600.
#   - On timeout, the child run is CANCELLED via GitHub API so it doesn't
#     keep burning runner minutes.
#   - Verification result classified as one of:
#       confirmed_by_failure  — child workflow ran and failed (bug present)
#       confirmed_by_timeout  — child workflow hung past timeout (bug present, treat as confirmed)
#       not_reproduced        — child workflow succeeded (bug already fixed)
#       not_confirmed         — could not dispatch / no Affected Workflow / dry run

def _cancel_workflow_run(gh: Github, run_id: int) -> bool:
    """Best-effort cancel. Returns True on 202 Accepted, False otherwise."""
    try:
        repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
        run = repo.get_workflow_run(run_id)
        run.cancel()
        return True
    except Exception as e:
        log(f"  cancel run {run_id} failed (non-fatal): {e}")
        return False


def classify_verification(bug_status: str) -> str:
    """Map raw wait_for_run result to a verification outcome label."""
    if bug_status == "success":
        return "not_reproduced"
    if bug_status == "failure":
        return "confirmed_by_failure"
    if bug_status == "timeout":
        return "confirmed_by_timeout"
    return "not_confirmed"


def _read_task_timeout_s(task: dict, default: int = 600) -> int:
    """Read 'Verification Timeout' (seconds) from a task row; default 600."""
    raw = (task.get("Verification Timeout") or "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
        if 60 <= n <= 1500:
            return n
        return default
    except (TypeError, ValueError):
        return default



# ── BACKUPS ─────────────────────────────────────────────────────────────────
def backup_file_to_drive(creds: Credentials, gh: Github, path: str, task_id: str) -> str:
    """Copy current file content to Drive Backups folder. Return Drive file ID."""
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    contents = repo.get_contents(path, ref=DEFAULT_BRANCH)
    content_bytes = base64.b64decode(contents.content)
    drive = gbuild("drive", "v3", credentials=creds)
    ts = dt.datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")
    safe_path = path.replace("/", "_")
    backup_name = f"BACKUP_{task_id}_{safe_path}_{ts}"
    media = MediaInMemoryUpload(content_bytes, mimetype="text/plain", resumable=False)
    f = drive.files().create(
        body={"name": backup_name, "parents": [BACKUPS_FOLDER]},
        media_body=media,
        supportsAllDrives=True,
        fields="id,webViewLink",
    ).execute()
    log(f"BACKUP saved: {backup_name} ({f['id']})")
    return f["id"]

# ── AI PATCH GENERATION ─────────────────────────────────────────────────────
PATCH_SYSTEM_PROMPT = """You are a senior Python engineer maintaining a content pipeline at oak-park-ai-hub.

CONSTRAINTS (NON-NEGOTIABLE — read repo NONNEGOTIABLES.md before reasoning):
1. APPEND-ONLY DOCTRINE (NN-S2): Working scripts are NEVER deleted or rewritten
   from scratch. Edit only the section that must change. Preserve all other
   existing functions, imports, comments, and side-effect ordering verbatim.
2. NO MASS DELETIONS: If your patch removes more than 20 lines from the
   original file (counted as net deletion), REFUSE.
3. SIZE LIMIT: If the fix requires more than ~80 changed lines or affects
   3+ functions, REFUSE.
4. NO CROSS-FILE FIXES: If the bug cannot be fixed without touching another
   file, REFUSE and explain in the reason.
5. NO LABEL LEAKAGE (NN-S4): If your patch adds any of these strings to
   user-rendered output (carousel slide text, reel captions, briefs):
   "one should say", "the narrator says", "[INSERT", "{{", "TODO:",
   "PLACEHOLDER", "XXX", "Slide N:", "Hook:", "CTA:", "Body:" —
   that is a critical bug. REFUSE the patch.
6. PRESERVE COMMENTS AND DOCSTRINGS that document existing behavior. New
   comments may be added but old ones must not be removed unless they are
   describing the exact code being changed.
7. Output ONLY valid JSON with this schema:
   {
     "decision": "PATCH" | "REFUSE",
     "reason": "short explanation",
     "risk": "LOW" | "MED" | "HIGH",
     "new_file_content": "FULL FILE CONTENT (only when decision=PATCH)",
     "diff_summary": "1-3 sentences on what changed"
   }
"""

# Strings that must not appear in user-rendered output. Used by guard checks below
# AND injected into the system prompt above (NN-S4).
LABEL_LEAK_PATTERNS = [
    "one should say", "the narrator says", "[INSERT", "{{",
    "TODO:", "PLACEHOLDER", "XXX",
    "Slide 1:", "Slide 2:", "Slide 3:", "Slide 4:", "Slide 5:",
    "Hook:", "CTA:", "Body:",
]

def patch_violates_nonnegotiables(before: str, after: str) -> tuple[bool, str]:
    """Return (violated, reason). Used to reject patches before they ship."""
    before_lines = before.splitlines()
    after_lines  = after.splitlines()
    # NN-S2: net deletion limit
    net_delete = max(0, len(before_lines) - len(after_lines))
    if net_delete > 20:
        return True, f"NN-S2 violation: net deletion of {net_delete} lines (> 20)"
    # NN-S4: no label-leak strings introduced
    for pat in LABEL_LEAK_PATTERNS:
        if pat in after and pat not in before:
            return True, f"NN-S4 violation: introduced label-leak string {pat!r}"
    return False, ""


def _tolerant_json_loads(text: str, source_label: str = "AI") -> dict:
    """Parse AI output as JSON, tolerating common formatting quirks.

    Real-world failure cases this handles:
    - Literal newlines/tabs inside string fields (JSON-spec invalid but common
      from LLMs returning full-file content). Fix: strict=False.
    - Trailing junk after the JSON object. Fix: try strict, then strict=False,
      then locate first {...} block via regex bracket matching.
    - Wrapping ```json fences. Already stripped at call site, but double-safe.
    """
    import json as _j
    if text.startswith("```"):
        text = text.split("```", 2)[-1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    # 1. strict
    try:
        return _j.loads(text)
    except _j.JSONDecodeError:
        pass
    # 2. lenient (allows control chars in strings)
    try:
        return _j.loads(text, strict=False)
    except _j.JSONDecodeError:
        pass
    # 2.5. sanitize: replace ALL ASCII control chars (except \n \r \t) with spaces.
    # Some LLM responses contain literal \x07 (BEL) etc. that break even strict=False.
    import re as _re
    sanitized = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    try:
        return _j.loads(sanitized, strict=False)
    except _j.JSONDecodeError:
        pass
    # 3. last resort: extract first balanced {...} block
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == "\"":
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        return _j.loads(candidate, strict=False)
                    except _j.JSONDecodeError:
                        break
    # Log the actual content for debugging — first 800 chars + last 400 chars
    log(f"  [parse-fail] {source_label} returned (len={len(text)} chars):")
    log(f"  [parse-fail] HEAD: {text[:800]!r}")
    if len(text) > 1200:
        log(f"  [parse-fail] TAIL: {text[-400:]!r}")
    return {
        "decision": "REFUSE",
        "reason": f"{source_label} returned non-JSON output (after 3 parse strategies); see job log for snippet",
        "risk": "HIGH",
    }


def request_patch_claude(client: anthropic.Anthropic, task: dict,
                         file_path: str, file_content: str,
                         error_log: str = "") -> dict:
    nn = (NONNEGOTIABLES_TEXT[:8000] if NONNEGOTIABLES_TEXT else "(not loaded)")
    report = (DETAILED_REPORT_TEXT[:15000] if DETAILED_REPORT_TEXT else "(not loaded)")
    user = f"""REPO NON-NEGOTIABLES (read first, comply absolutely):
```
{nn}
```

LATEST DETAILED REPORT (read this — it has the current state, recent fixes, and queue context):
```
{report}
```

TASK ID: {task.get('ID')}
TITLE: {task.get('Title')}
DESCRIPTION: {task.get('Description')}
TARGET FILE: {file_path}
VERIFICATION METHOD: {task.get('Verification Method')}

CURRENT FILE CONTENT (verbatim — do not delete or restructure unrelated code):
```
{file_content[:50000]}
```

PRIOR ATTEMPT ERROR LOG (empty if first attempt):
```
{error_log[:6000]}
```

Produce a patch per the constraints. Output JSON ONLY.
"""
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=6000,
        system=PATCH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return _tolerant_json_loads(text, source_label="Claude")


def request_patch_openai_primary(client: OpenAI, task: dict,
                                  file_path: str, file_content: str,
                                  error_log: str = "") -> dict:
    """OpenAI as primary patch generator (fallback when Claude is unavailable)."""
    nn = (NONNEGOTIABLES_TEXT[:8000] if NONNEGOTIABLES_TEXT else "(not loaded)")
    report = (DETAILED_REPORT_TEXT[:15000] if DETAILED_REPORT_TEXT else "(not loaded)")
    user = f"""REPO NON-NEGOTIABLES (read first, comply absolutely):
```
{nn}
```

LATEST DETAILED REPORT (read this — it has the current state, recent fixes, and queue context):
```
{report}
```

TASK ID: {task.get('ID')}
TITLE: {task.get('Title')}
DESCRIPTION: {task.get('Description')}
TARGET FILE: {file_path}
VERIFICATION METHOD: {task.get('Verification Method')}

CURRENT FILE CONTENT (verbatim — do not delete or restructure unrelated code):
```
{file_content[:50000]}
```

PRIOR ATTEMPT ERROR LOG (empty if first attempt):
```
{error_log[:6000]}
```

Produce a patch per the constraints. Output JSON ONLY.
"""
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": PATCH_SYSTEM_PROMPT},
            {"role": "user",   "content": user},
        ],
        response_format={"type": "json_object"},
        max_tokens=6000,
    )
    text = resp.choices[0].message.content.strip()
    return _tolerant_json_loads(text, source_label="OpenAI")


def request_patch_openai(client: OpenAI, task: dict,
                          file_path: str, file_content: str,
                          claude_patch: dict, error_log: str = "") -> dict:
    user = f"""You are reviewing a fix proposed by another AI.

TASK: {task.get('Title')}
TARGET FILE: {file_path}
DESCRIPTION: {task.get('Description')}

PROPOSED PATCH (from Claude):
- decision: {claude_patch.get('decision')}
- risk: {claude_patch.get('risk')}
- diff_summary: {claude_patch.get('diff_summary')}
- new_file_content (snippet): {(claude_patch.get('new_file_content') or '')[:6000]}

ORIGINAL FILE CONTENT (full, for context):
```
{file_content[:50000]}
```

PRIOR ERROR LOG:
```
{error_log[:4000]}
```

Decide if Claude's fix is safe and correct. Output JSON ONLY:
{{
  "agreement": "AGREE" | "DISAGREE" | "PARTIAL",
  "concerns": "list of concerns or 'none'",
  "alternative_decision": "PATCH" | "REFUSE" | "USE_CLAUDE_PATCH",
  "alternative_new_file_content": "full file if you propose your own patch, else empty",
  "diff_summary": "what your alternative changes (or 'same as Claude')"
}}
"""
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": "You are a code reviewer. Output ONLY valid JSON."},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        max_tokens=4000,
    )
    text = resp.choices[0].message.content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"agreement": "DISAGREE", "concerns": f"OpenAI returned non-JSON: {e}",
                "alternative_decision": "REFUSE"}

# ── COMMIT + PR + MERGE ─────────────────────────────────────────────────────
def commit_patch(gh: Github, branch: str, base: str,
                  path: str, content: str, message: str) -> str:
    """Create branch off base, commit one-file change. Return commit SHA."""
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    base_ref = repo.get_branch(base)
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_ref.commit.sha)
    except Exception:
        pass  # branch may exist from a prior attempt
    file_obj = repo.get_contents(path, ref=branch)
    result = repo.update_file(path=path, message=message, content=content,
                              sha=file_obj.sha, branch=branch)
    return result["commit"].sha

def merge_branch_to_main(gh: Github, branch: str, message: str) -> bool:
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    try:
        repo.merge(base=DEFAULT_BRANCH, head=branch, commit_message=message)
        try:
            repo.get_git_ref(f"heads/{branch}").delete()
        except Exception:
            pass
        return True
    except Exception as e:
        log(f"merge failed: {e}")
        return False

# ── FIXING LOG DOC ──────────────────────────────────────────────────────────
def write_fixing_log_doc(creds: Credentials, task: dict, before: str, after: str,
                         claude_patch: dict, openai_review: dict,
                         smoke_run_id: Optional[int], smoke_run_url: str,
                         backup_id: str, commit_sha: str,
                         test_artifact_url: Optional[str], outcome: str) -> str:
    """Create a Google Doc in Fixing Log folder. Return Doc ID + webViewLink."""
    drive = gbuild("drive", "v3", credentials=creds)
    docs  = gbuild("docs", "v1", credentials=creds)
    ts = dt.datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    title = f"FIX_{task.get('ID')}_{ts}_{outcome}"
    doc = drive.files().create(
        body={"name": title, "parents": [FIXING_LOG_FOLDER],
              "mimeType": "application/vnd.google-apps.document"},
        supportsAllDrives=True, fields="id,webViewLink",
    ).execute()
    doc_id = doc["id"]

    # Build the report text. Markdown-flavored but rendered as plain in Docs
    # since Docs API doesn't render markdown. We use simple paragraphs.
    body_lines = []
    body_lines.append(f"FIXING LOG — {task.get('ID')}")
    body_lines.append(f"{ts} UTC — Outcome: {outcome}")
    body_lines.append("")
    body_lines.append("RESOURCES (TOP)")
    body_lines.append(f"- Task ID: {task.get('ID')}")
    body_lines.append(f"- Title: {task.get('Title')}")
    body_lines.append(f"- Priority: {task.get('Priority')}")
    body_lines.append(f"- Target File: {task.get('Target File')}")
    body_lines.append(f"- Affected Workflow: {task.get('Affected Workflow')}")
    body_lines.append(f"- Self-Heal Run: {os.environ.get('GH_RUN_URL','')}")
    body_lines.append(f"- Smoke Test Run: {smoke_run_url}")
    body_lines.append(f"- Backup File ID: https://drive.google.com/file/d/{backup_id}/view")
    body_lines.append(f"- Commit SHA: https://github.com/{REPO_OWNER}/{REPO_NAME}/commit/{commit_sha}")
    if test_artifact_url:
        body_lines.append(f"- Test Carousel Built: {test_artifact_url}")
    body_lines.append("")
    body_lines.append("WHAT WAS BROKEN")
    body_lines.append(task.get('Description', ''))
    body_lines.append("")
    body_lines.append("CLAUDE'S DIAGNOSIS")
    body_lines.append(f"Decision: {claude_patch.get('decision')}")
    body_lines.append(f"Risk: {claude_patch.get('risk')}")
    body_lines.append(f"Reason: {claude_patch.get('reason')}")
    body_lines.append(f"Summary: {claude_patch.get('diff_summary')}")
    body_lines.append("")
    body_lines.append("OPENAI REVIEW")
    body_lines.append(f"Agreement: {openai_review.get('agreement')}")
    body_lines.append(f"Concerns: {openai_review.get('concerns')}")
    body_lines.append(f"Alternative: {openai_review.get('alternative_decision')}")
    body_lines.append("")
    body_lines.append("BEFORE (snippet, first 80 lines of relevant section)")
    body_lines.append(_snippet(before))
    body_lines.append("")
    body_lines.append("AFTER (snippet, first 80 lines of relevant section)")
    body_lines.append(_snippet(after))
    body_lines.append("")
    body_lines.append("OUTCOME")
    body_lines.append(outcome)
    body_lines.append("")
    body_lines.append(f"Generated by pipeline_self_heal.yml — run {os.environ.get('GH_RUN_ID','')}")

    full_text = "\n".join(body_lines)
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": full_text}}]},
    ).execute()
    return doc["webViewLink"]

def _snippet(content: str, max_lines: int = 80) -> str:
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines)-max_lines} more lines)"

# ── CHECKLIST DOC ───────────────────────────────────────────────────────────
def update_checklist(creds: Credentials, task: dict, fix_doc_url: str, outcome: str) -> None:
    """Append a dated entry under the task's bullet in _CHECKLIST.md."""
    drive = gbuild("drive", "v3", credentials=creds)
    q = (f"name = '{CHECKLIST_FILENAME}' and "
         f"'{FIXING_LOG_FOLDER}' in parents and trashed=false")
    res = drive.files().list(q=q, supportsAllDrives=True,
                             includeItemsFromAllDrives=True,
                             fields="files(id,name)").execute()
    items = res.get("files", [])
    ts = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    line = f"\n  - {ts} — {outcome} — {task.get('Title')} — {fix_doc_url}\n"

    if not items:
        # Create checklist
        header = f"# Self-Heal Checklist\n\nStarted: {ts}\n\n## Tasks\n\n"
        body = header + f"- [{task.get('ID')}] {task.get('Title')}{line}"
        media = MediaInMemoryUpload(body.encode("utf-8"), mimetype="text/markdown")
        drive.files().create(
            body={"name": CHECKLIST_FILENAME, "parents": [FIXING_LOG_FOLDER]},
            media_body=media, supportsAllDrives=True, fields="id"
        ).execute()
        return

    file_id = items[0]["id"]
    existing = drive.files().get_media(fileId=file_id, supportsAllDrives=True).execute().decode("utf-8")
    bullet = f"- [{task.get('ID')}]"
    if bullet in existing:
        # Append a sub-bullet under the existing entry
        new_text = existing.replace(bullet + " " + (task.get('Title') or ''),
                                    bullet + " " + (task.get('Title') or '') + line, 1)
        if new_text == existing:
            new_text = existing + line
    else:
        new_text = existing + f"- [{task.get('ID')}] {task.get('Title')}{line}"
    media = MediaInMemoryUpload(new_text.encode("utf-8"), mimetype="text/markdown")
    drive.files().update(fileId=file_id, media_body=media,
                         supportsAllDrives=True).execute()

# ── FINAL REPORT (when queue is done) ───────────────────────────────────────
def queue_is_done(rows: list[dict]) -> bool:
    for r in rows:
        s = (r.get("Status") or "").upper()
        if s in ("PENDING", "NEEDS-REVIEW", ""):
            return False
    return True

def write_final_report(creds: Credentials, gh: Github, rows: list[dict]) -> str:
    docs = gbuild("docs", "v1", credentials=creds)
    drive = gbuild("drive", "v3", credentials=creds)
    ts = dt.datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    title = f"FINAL_REPORT_self_heal_complete_{ts}"
    doc = drive.files().create(
        body={"name": title, "parents": [FIXING_LOG_FOLDER],
              "mimeType": "application/vnd.google-apps.document"},
        supportsAllDrives=True, fields="id,webViewLink",
    ).execute()

    body = []
    body.append("NO MORE ISSUES")
    body.append("")
    body.append("All queue items are DONE or USER-ONLY. Self-heal cycle complete.")
    body.append(f"Generated: {ts} UTC")
    body.append("")
    body.append("PER-TASK STATUS")
    for r in rows:
        body.append(f"- {r.get('ID')} | {r.get('Status')} | {r.get('Title')} | {r.get('Fix Log Link') or ''}")
    body.append("")
    body.append("Next: example builds will be triggered for each niche / format.")
    full_text = "\n".join(body)
    docs.documents().batchUpdate(
        documentId=doc["id"],
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": full_text}}]},
    ).execute()
    # Color the banner red (first paragraph)
    docs.documents().batchUpdate(
        documentId=doc["id"],
        body={"requests": [{"updateTextStyle": {
            "range": {"startIndex": 1, "endIndex": 16},
            "textStyle": {"bold": True,
                          "foregroundColor": {"color": {"rgbColor": {"red": 1, "green": 0, "blue": 0}}},
                          "fontSize": {"magnitude": 24, "unit": "PT"}},
            "fields": "bold,foregroundColor,fontSize"}}]},
    ).execute()
    return doc["webViewLink"]

# ── QUEUE INTEGRITY GUARD (SH-045 — added 2026-05-03) ───────────────────────
# Why: prior queue history accumulated 10 duplicate SH IDs (SH-032×2,
# SH-033×3, SH-034×3, SH-035-040×2, SH-042×2). update_queue_row() returns
# the FIRST matching row in column A, while pick_task() returns rows by
# priority ordering — so the bot can pick a task from row 46 but write its
# status update to row 36 of the same ID. Silent state corruption.
#
# Behavior:
#   - Scan column A (ID) of all PENDING/IN-PROGRESS/NEEDS-REVIEW rows.
#   - If any ID appears more than once, log each duplicate with its row
#     numbers and email Priscila with the full duplicate list.
#   - Return False so main() halts BEFORE pick_task() and BEFORE any
#     queue row is touched.
#   - Renumbering is NOT auto-performed — Priscila resolves manually.

def preflight_queue_integrity(rows: list[dict]) -> tuple[bool, dict]:
    """Return (ok, info). ok=False means halt cycle. info has duplicate details."""
    id_to_rows = {}
    # rows is a list of dicts coming from read_queue(); reconstruct row numbers
    # by enumerating in order. Sheet header is row 1, so first data row = row 2.
    for idx, r in enumerate(rows, start=2):
        rid = (r.get("ID") or "").strip()
        if not rid:
            continue
        id_to_rows.setdefault(rid, []).append(idx)
    dups = {rid: row_nums for rid, row_nums in id_to_rows.items() if len(row_nums) > 1}
    info = {
        "total_rows": len(rows),
        "unique_ids": len(id_to_rows),
        "duplicate_count": len(dups),
        "duplicates": dups,
    }
    return (len(dups) == 0), info


def emit_queue_integrity_alert(info: dict) -> None:
    """Log + email when duplicates detected."""
    dups = info.get("duplicates", {})
    log("=" * 60)
    log(f"QUEUE INTEGRITY GUARD: HALTED — {len(dups)} duplicate ID(s) found")
    log(f"  Total rows scanned: {info.get('total_rows')}")
    log(f"  Unique IDs:         {info.get('unique_ids')}")
    log("  Duplicate detail:")
    for rid, row_nums in sorted(dups.items()):
        log(f"    {rid:8s} appears in rows {row_nums}")
    log("=" * 60)
    log("Halting cycle. No task selected, no rows updated, no patches generated.")
    log("Action required: manually renumber duplicate rows in the queue, then re-run.")

    # Build email body
    body_lines = [
        "<h2>Self-Heal cycle HALTED — queue integrity violation</h2>",
        f"<p>Found <b>{len(dups)} duplicate SH ID(s)</b> in the active queue. "
        "The orchestrator refused to proceed because <code>update_queue_row()</code> "
        "would silently write to the wrong row.</p>",
        f"<p>Total rows scanned: <b>{info.get('total_rows')}</b><br>"
        f"Unique IDs: <b>{info.get('unique_ids')}</b><br>"
        f"Duplicates: <b>{len(dups)}</b></p>",
        "<h3>Duplicate detail</h3>",
        "<ul>",
    ]
    for rid, row_nums in sorted(dups.items()):
        body_lines.append(f"  <li><code>{rid}</code> appears in rows {row_nums}</li>")
    body_lines.append("</ul>")
    body_lines.append(
        "<p><b>Required action:</b> open the 🔧 Self-Heal Queue tab and renumber "
        "duplicate rows so every ID is unique. Then trigger the workflow again. "
        "The orchestrator will not run another cycle until the queue is clean.</p>"
    )
    body_lines.append(
        f'<p><a href="https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=372065888">'
        "Open the Self-Heal Queue tab</a></p>"
    )
    try:
        send_email(
            "[Self-Heal] HALTED — duplicate SH IDs in queue",
            "\n".join(body_lines),
        )
        log("Queue-integrity alert email sent.")
    except Exception as e:
        log(f"WARN: queue-integrity alert email failed (non-fatal): {e}")


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    creds = get_creds()
    gh = gh_client()
    cl = claude_client()
    oa = openai_client()
    ws = open_queue(creds)

    if maybe_pause(ws):
        return

    # NN-S5: read NONNEGOTIABLES.md as the FIRST action of every cycle.
    # This text gets injected into every patch-generation prompt so both
    # Claude and OpenAI see the live rules, not a stale snapshot.
    global NONNEGOTIABLES_TEXT, DETAILED_REPORT_TEXT
    NONNEGOTIABLES_TEXT = ""
    DETAILED_REPORT_TEXT = ""
    try:
        repo_for_nn = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
        nn_obj = repo_for_nn.get_contents("NONNEGOTIABLES.md", ref=DEFAULT_BRANCH)
        NONNEGOTIABLES_TEXT = base64.b64decode(nn_obj.content).decode("utf-8", "replace")
        log(f"NONNEGOTIABLES.md loaded ({len(NONNEGOTIABLES_TEXT)} chars)")
    except Exception as e:
        log(f"WARN: could not load NONNEGOTIABLES.md ({e}); proceeding with embedded rules only")

    # ─────────────────────────────────────────────────────────────
    # Read the latest detailed REPORT from the PIPELINE FIX folder
    # so the bot knows the current state, recent fixes, and queue
    # context. Append-only — falls through silently if not available.
    # Folder: 1FHPkx8VA6c-Wmy6hI3uX_weSPwJPBp3z (PIPELINE FIX root)
    # ─────────────────────────────────────────────────────────────
    DETAILED_REPORT_TEXT = ""
    try:
        import urllib.request as _ur, urllib.parse as _up, json as _j
        # Get OAuth token for Drive
        _raw = os.environ.get("SHEETS_TOKEN", "")
        if _raw:
            _td = _j.loads(_raw)
            _data = _up.urlencode({
                "client_id": _td["client_id"],
                "client_secret": _td["client_secret"],
                "refresh_token": _td["refresh_token"],
                "grant_type": "refresh_token",
            }).encode()
            _resp = _j.loads(_ur.urlopen(
                _ur.Request("https://oauth2.googleapis.com/token", data=_data),
                timeout=10,
            ).read())
            _tok = _resp.get("access_token", "")
            if _tok:
                # List REPORT_*.md files in PIPELINE FIX folder, newest first
                _PIPELINE_FIX_FOLDER = "1FHPkx8VA6c-Wmy6hI3uX_weSPwJPBp3z"
                _q = (f"\'{_PIPELINE_FIX_FOLDER}\' in parents and "
                      f"name contains \'REPORT_\' and "
                      f"(mimeType = \'text/markdown\' or mimeType = \'text/plain\') and "
                      f"trashed = false")
                _list_url = (
                    f"https://www.googleapis.com/drive/v3/files?"
                    f"q={_up.quote(_q)}&"
                    f"orderBy=modifiedTime+desc&"
                    f"pageSize=5&"
                    f"fields=files(id,name,modifiedTime,mimeType)&"
                    f"supportsAllDrives=true&includeItemsFromAllDrives=true"
                )
                _list_req = _ur.Request(_list_url, headers={"Authorization": f"Bearer {_tok}"})
                _list_resp = _j.loads(_ur.urlopen(_list_req, timeout=10).read())
                _files = _list_resp.get("files", [])
                if _files:
                    _latest = _files[0]
                    _file_id = _latest["id"]
                    _name = _latest["name"]
                    # Download content
                    _dl_url = (f"https://www.googleapis.com/drive/v3/files/{_file_id}"
                               f"?alt=media&supportsAllDrives=true")
                    _dl_req = _ur.Request(_dl_url, headers={"Authorization": f"Bearer {_tok}"})
                    DETAILED_REPORT_TEXT = _ur.urlopen(_dl_req, timeout=15).read().decode("utf-8", "replace")
                    # Cap at 30k chars to control prompt size
                    if len(DETAILED_REPORT_TEXT) > 30000:
                        DETAILED_REPORT_TEXT = DETAILED_REPORT_TEXT[:30000] + "\n\n[truncated at 30k chars]"
                    log(f"REPORT loaded: '{_name}' ({len(DETAILED_REPORT_TEXT)} chars)")
                else:
                    log("No REPORT_*.md files found in PIPELINE FIX folder")
    except Exception as e:
        log(f"WARN: could not load detailed REPORT ({e}); proceeding without it")

    rows = read_queue(ws)

    # SH-045: queue integrity guard. Halt cycle BEFORE pick_task if duplicates exist.
    _qi_ok, _qi_info = preflight_queue_integrity(rows)
    if not _qi_ok:
        emit_queue_integrity_alert(_qi_info)
        return

    if queue_is_done(rows):
        url = write_final_report(creds, gh, rows)
        log(f"FINAL report: {url}")
        send_email("[Self-Heal] NO MORE ISSUES — final report ready",
                   f'<h1 style="color:red">NO MORE ISSUES</h1>'
                   f'<p>All tasks complete. <a href="{url}">View final report</a></p>')
        return

    target = os.environ.get("TARGET_ID", "").strip()
    force  = os.environ.get("FORCE_RETRY", "").lower() == "true"
    dry    = os.environ.get("DRY_RUN", "").lower() == "true"
    task = pick_task(rows, target_id=target, force=force)
    if not task:
        log("No actionable tasks. Exiting.")
        return

    task_id = task["ID"]
    log(f"PICKED {task_id} — {task.get('Title')}")
    update_queue_row(ws, task_id, Status="IN-PROGRESS",
                     **{"Last Attempt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"})

    # Verify bug still present (best effort: trigger affected workflow)
    # Skipped during dry_run to keep test cycles fast.
    # 2026-05-03 patch: per-task verification_timeout_s, classified outcomes,
    # auto-cancel child run on timeout. Does NOT change merge/approval logic.
    aw = (task.get("Affected Workflow") or "").strip()
    bug_status = "unknown"
    verify_outcome = "not_confirmed"
    verify_timeout_s = _read_task_timeout_s(task, default=600)
    if not dry and aw and aw != "n/a":
        run_id = trigger_workflow(gh, aw)
        if run_id:
            log(f"verify-bug: dispatched {aw} run {run_id}, timeout={verify_timeout_s}s")
            bug_status = wait_for_run(gh, run_id, timeout_s=verify_timeout_s)
            verify_outcome = classify_verification(bug_status)
            if bug_status == "timeout":
                cancelled = _cancel_workflow_run(gh, run_id)
                log(f"verify-bug: TIMEOUT after {verify_timeout_s}s — child run cancel={cancelled}")
            log(f"verify-bug: run {run_id} status={bug_status} outcome={verify_outcome}")
            if verify_outcome == "not_reproduced":
                update_queue_row(ws, task_id, Status="DONE",
                                 **{"Last Result": "ALREADY_FIXED — verification: not_reproduced"})
                log("Bug not reproducing — marking DONE")
                _bump_pause_counter(ws)
                return
            # confirmed_by_failure or confirmed_by_timeout → fall through to patch
            # not_confirmed → fall through too (best-effort, may have dispatch issue)
    elif dry:
        log("DRY RUN — skipping bug-verification dispatch to save time")
        verify_outcome = "skipped_dry_run"
    log(f"verify-bug: outcome={verify_outcome}")

    # Pull file content
    path = (task.get("Target File") or "").strip()
    if not path or path == "external services":
        log("No target file — marking USER-ONLY")
        update_queue_row(ws, task_id, Status="USER-ONLY")
        return
    repo = gh.get_repo(f"{REPO_OWNER}/{REPO_NAME}")
    try:
        file_obj = repo.get_contents(path, ref=DEFAULT_BRANCH)
        before = base64.b64decode(file_obj.content).decode("utf-8", "replace")
    except Exception as e:
        log(f"cannot read {path}: {e}")
        update_queue_row(ws, task_id, Status="BLOCKED",
                         **{"Last Result": f"FILE_READ_FAIL: {e}"})
        return

    # Backup
    backup_id = backup_file_to_drive(creds, gh, path, task_id)

    # Generate patch
    error_log = ""
    smoke_run_id = None
    smoke_run_url = ""
    final_outcome = "UNKNOWN"
    test_artifact_url = None
    after = before
    commit_sha = ""

    for attempt in range(1, 4):
        log(f"=== Attempt {attempt}/3 ===")
        # Try Claude first; if it's broke (credit / auth) fall back to OpenAI as primary.
        try:
            claude_patch = request_patch_claude(cl, task, path, before, error_log)
        except anthropic.BadRequestError as e:
            log(f"Claude API error ({e}); falling back to OpenAI as primary")
            claude_patch = request_patch_openai_primary(oa, task, path, before, error_log)
        except anthropic.AuthenticationError as e:
            log(f"Claude AUTH error ({e}); falling back to OpenAI as primary")
            claude_patch = request_patch_openai_primary(oa, task, path, before, error_log)
        if claude_patch.get("decision") == "REFUSE":
            log(f"Claude REFUSED: {claude_patch.get('reason')}")
            update_queue_row(ws, task_id, Status="NEEDS-REVIEW",
                             Attempts=attempt,
                             **{"Last Result": f"CLAUDE_REFUSED: {claude_patch.get('reason','')[:200]}"})
            _write_log_and_finish(creds, task, before, before, claude_patch,
                                  {"agreement": "n/a"}, None, "", backup_id, "",
                                  None, "REFUSED")
            return

        oa_review = request_patch_openai(oa, task, path, before, claude_patch, error_log)
        agreement = oa_review.get("agreement")
        log(f"OpenAI agreement: {agreement}")

        if agreement == "DISAGREE" and attempt == 1:
            error_log = f"OpenAI concerns: {oa_review.get('concerns')}"
            continue  # retry with concerns folded in

        # Apply Claude's patch (preferred) unless OpenAI proposed an alternative explicitly
        if oa_review.get("alternative_decision") == "PATCH" and oa_review.get("alternative_new_file_content"):
            new_content = oa_review["alternative_new_file_content"]
            log("Using OpenAI alternative patch")
        else:
            new_content = claude_patch.get("new_file_content") or ""

        if not new_content:
            error_log = "no patch content"
            continue

        # NN-S2 / NN-S4 guard: reject patches that violate the rules.
        violated, reason = patch_violates_nonnegotiables(before, new_content)
        if violated:
            log(f"REJECTED by non-negotiables guard: {reason}")
            error_log = f"NON-NEGOTIABLES VIOLATION: {reason}"
            continue

        if dry:
            log("DRY RUN — not committing")
            after = new_content
            final_outcome = "DRY_RUN"
            break

        branch = f"self-heal/{task_id.lower()}-attempt-{attempt}"
        commit_sha = commit_patch(gh, branch, DEFAULT_BRANCH, path, new_content,
                                   f"self-heal {task_id}: {task.get('Title','')[:60]}")
        log(f"committed to {branch} @ {commit_sha[:8]}")
        after = new_content

        # Smoke test on branch
        if aw and aw != "n/a":
            smoke_run_id = trigger_workflow(gh, aw, ref=branch)
            if smoke_run_id:
                smoke_run_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/actions/runs/{smoke_run_id}"
                conclusion = wait_for_run(gh, smoke_run_id)
                log(f"smoke {smoke_run_id}: {conclusion}")
                if conclusion == "success":
                    if merge_branch_to_main(gh, branch, f"merge self-heal {task_id}"):
                        final_outcome = "FIXED"
                        break
                    else:
                        final_outcome = "MERGE_FAILED"
                        break
                else:
                    error_log = f"smoke test {conclusion} on attempt {attempt}"
                    continue
        else:
            final_outcome = "PATCHED_NO_SMOKE"
            break

    # Done — write the log + update queue
    fix_url = write_fixing_log_doc(creds, task, before, after,
                                    claude_patch, oa_review, smoke_run_id,
                                    smoke_run_url, backup_id, commit_sha,
                                    test_artifact_url, final_outcome)
    update_queue_row(ws, task_id,
                     Status=("DONE" if final_outcome == "FIXED" else "NEEDS-REVIEW"),
                     Attempts=attempt,
                     **{"Last Result": final_outcome, "Fix Log Link": fix_url})
    update_checklist(creds, task, fix_url, final_outcome)
    _bump_pause_counter(ws)
    log(f"=== cycle done: {final_outcome} ===")

def _bump_pause_counter(ws: gspread.Worksheet) -> None:
    count, last = get_pause_counter(ws)
    set_pause_counter(ws, count + 1, last)

def _write_log_and_finish(creds, task, before, after, cp, oa,
                          smoke_run_id, smoke_run_url, backup_id, commit_sha,
                          test_artifact_url, outcome):
    fix_url = write_fixing_log_doc(creds, task, before, after, cp, oa,
                                    smoke_run_id, smoke_run_url, backup_id,
                                    commit_sha, test_artifact_url, outcome)
    return fix_url

if __name__ == "__main__":
    main()
