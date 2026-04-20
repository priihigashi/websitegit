"""
self_healer.py — End-of-run auto-fix for failed modules.
Runs last in main.py after everything else.

Flow per failure:
  transient (timeout/network) → log, notify, retry tomorrow
  script error (SyntaxError etc) → Haiku writes minimal fix → GitHub PR if confident
  config error (auth/missing key) → Calendar task with exact steps
  unknown → YouTube research loop + Calendar task

Dedup state files (W1/W2 fix):
  healed_modules.json    — tracks Calendar tasks created per module (skip if <7 days)
  researched_modules.json — tracks research triggers per module (skip if <7 days)
"""
import os, json, base64, requests
import pytz
from datetime import datetime
from googleapiclient.discovery import build
from google.oauth2 import service_account
import anthropic

FAILURES_FILE    = ".github/agent_state/module_failures.json"
HEALED_FILE      = ".github/agent_state/healed_modules.json"
RESEARCHED_FILE  = ".github/agent_state/researched_modules.json"
GITHUB_REPO      = "priihigashi/oak-park-ai-hub"
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
et               = pytz.timezone("America/New_York")
client           = anthropic.Anthropic(api_key=os.environ["CLAUDE_KEY_4_CONTENT"])

SCOPES = ["https://www.googleapis.com/auth/calendar"]

TRANSIENT = ["timeout", "connection", "503", "502", "rate limit", "429", "network", "socket"]
CONFIG    = ["not found", "missing secret", "401", "403", "credential", "permission", "forbidden"]
SCRIPT    = ["syntaxerror", "attributeerror", "keyerror", "typeerror", "nameerror",
             "indexerror", "valueerror", "jsondecodeerror", "json.loads"]
CONTENT   = ["carousel", "generate_carousel", "slide", "haiku", "content generation",
             "no topics", "zero carousels", "empty slides", "brief", "topic_picker"]
ART       = ["render_pngs", "export_variants", "playwright", "puppeteer", "ffmpeg",
             "ideogram", "seedream", "image generation", "png", "motion", "gif"]

DEDUP_DAYS = 7


def _creds():
    return service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SA_KEY"]), scopes=SCOPES
    )


def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


# ─── GitHub state helpers (W1/W2 fix) ─────────────────────────────────────────

def _load_from_github(file_path):
    """Load JSON state file from GitHub. Returns {} if not found."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    r = requests.get(url, headers=_gh_headers())
    if r.status_code == 200:
        try:
            return json.loads(base64.b64decode(r.json()["content"]).decode())
        except Exception:
            return {}
    return {}


def _push_to_github(file_path, data):
    """Push JSON state file to GitHub."""
    url      = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{file_path}"
    b64      = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    existing = requests.get(url, headers=_gh_headers())
    payload  = {
        "message": f"agent: update {file_path.split('/')[-1]} [{datetime.now(et).strftime('%Y-%m-%d')}]",
        "content": b64,
    }
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]
    r = requests.put(url, headers=_gh_headers(), json=payload)
    if r.status_code not in (200, 201):
        print(f"[self_healer] WARNING: push failed for {file_path}: {r.status_code}")


def _days_since(date_str):
    """Return days since a YYYY-MM-DD string, or 999 if unparseable."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=et)
        return (datetime.now(et) - d).days
    except Exception:
        return 999


# ─── Categorization ───────────────────────────────────────────────────────────

def _categorize(error, tb):
    s = (error + tb).lower()
    if any(p in s for p in TRANSIENT): return "transient"
    if any(p in s for p in CONFIG):    return "config"
    if any(p in s for p in ART):       return "art"
    if any(p in s for p in CONTENT):   return "content"
    if any(p in s for p in SCRIPT):    return "script"
    return "unknown"


def _haiku_fix(module_name, error, tb):
    """Ask Haiku for a minimal fix. Returns fix dict or confidence=0."""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": f"""Python module failed in GitHub Actions.
Module: {module_name}
Error: {error}
Traceback: {tb[:1500]}

Write the MINIMAL fix (1-5 lines). Do not rewrite the module.
Return JSON only:
{{"confidence": 0-100, "fix_description": "one sentence",
  "file_to_edit": "scripts/4am_agent/{module_name}.py",
  "old_code": "exact string to replace", "new_code": "replacement"}}
If confidence < 70, return {{"confidence": 0, "fix_description": "cannot auto-fix",
  "file_to_edit": null, "old_code": null, "new_code": null}}"""}],
    )
    text = resp.content[0].text.strip()
    if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:   text = text.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(text)
    except Exception:
        return {"confidence": 0}


_SHEET_IDS = {
    "inspiration_library": ("1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU", "📥 Inspiration Library"),
    "content_queue":       ("1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU", "📋 Content Queue"),
    "capture_queue":       ("1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU", "📲 Capture Queue"),
}

_GOLD_PATTERN = """
GOLD STANDARD — header-name lookup (use this pattern, never hardcode indices):
    headers = lib.row_values(1)                          # read row 1
    col_pos = {h.strip().lower(): i for i, h in enumerate(headers)}
    def _set_col(row, name, value):
        idx = col_pos.get(name.strip().lower())
        if idx is not None:
            while len(row) <= idx: row.append("")
            row[idx] = str(value) if value is not None else ""
    base_row = []
    _set_col(base_row, "url", url_value)
    _set_col(base_row, "status", "CAPTURED")
    # etc — always by column NAME, never by index number
"""


def _fetch_live_headers(sheet_key: str) -> str:
    """Fetch live header row from a known sheet. Returns formatted string for LLM context."""
    entry = _SHEET_IDS.get(sheet_key)
    if not entry:
        return ""
    sheet_id, tab = entry
    try:
        from googleapiclient.discovery import build as _build
        svc = _build("sheets", "v4", credentials=service_account.Credentials.from_service_account_info(
            json.loads(os.environ["GOOGLE_SA_KEY"]),
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        ))
        result = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{tab}'!A1:AC1"
        ).execute()
        hdrs = result.get("values", [[]])[0]
        lines = [f"  {chr(65+i)}({i}): {h}" for i, h in enumerate(hdrs)]
        return f"Live headers for {tab}:\n" + "\n".join(lines)
    except Exception as e:
        return f"(could not fetch live headers: {e})"


def _is_schema_error(error: str, tb: str) -> bool:
    s = (error + tb).lower()
    return any(k in s for k in ["indexerror", "list index out of range", "row[", "col[", "column"])


def _schema_aware_haiku_fix(module_name, error, tb):
    """Enhanced Haiku fix that includes live sheet headers + gold pattern when schema error detected."""
    sheet_key = next((k for k in _SHEET_IDS if k in module_name.lower()
                      or k.replace("_", "") in tb.lower()), None)
    if not sheet_key:
        # Try to detect from traceback
        if "inspiration" in tb.lower():
            sheet_key = "inspiration_library"
        elif "capture_queue" in tb.lower():
            sheet_key = "capture_queue"
        elif "content_queue" in tb.lower():
            sheet_key = "content_queue"

    live_headers = _fetch_live_headers(sheet_key) if sheet_key else "(sheet key unknown)"

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": f"""Python module failed in GitHub Actions — likely a schema/column mismatch.

Module: {module_name}
Error: {error}
Traceback: {tb[:1500]}

{live_headers}

{_GOLD_PATTERN}

The fix MUST use the gold-standard header-name lookup pattern (never hardcode column indices).
Write the MINIMAL fix (1-10 lines). Do not rewrite the module.
Return JSON only:
{{"confidence": 0-100, "fix_description": "one sentence",
  "file_to_edit": "scripts/4am_agent/{module_name}.py",
  "old_code": "exact string to replace", "new_code": "replacement"}}
If confidence < 70, return {{"confidence": 0, "fix_description": "cannot auto-fix",
  "file_to_edit": null, "old_code": null, "new_code": null}}"""}],
    )
    text = resp.content[0].text.strip()
    if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:   text = text.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(text)
    except Exception:
        return {"confidence": 0}


def _create_pr(module_name, fix, error):
    base = f"https://api.github.com/repos/{GITHUB_REPO}"
    try:
        main_sha = requests.get(f"{base}/git/ref/heads/main", headers=_gh_headers()).json()["object"]["sha"]
        fp       = fix["file_to_edit"]
        fr       = requests.get(f"{base}/contents/{fp}", headers=_gh_headers()).json()
        current  = base64.b64decode(fr["content"]).decode()
        if fix["old_code"] not in current:
            print(f"[self_healer] old_code not found in {fp} — skipping PR")
            return False
        new_content = current.replace(fix["old_code"], fix["new_code"], 1)
        branch = f"auto-fix/{module_name}_{datetime.now(et).strftime('%Y%m%d_%H%M')}"
        requests.post(f"{base}/git/refs", headers=_gh_headers(),
                      json={"ref": f"refs/heads/{branch}", "sha": main_sha})
        requests.put(f"{base}/contents/{fp}", headers=_gh_headers(), json={
            "message": f"auto-fix: {fix['fix_description']}",
            "content": base64.b64encode(new_content.encode()).decode(),
            "sha": fr["sha"], "branch": branch,
        })
        pr = requests.post(f"{base}/pulls", headers=_gh_headers(), json={
            "title": f"🔧 Auto-fix: {module_name} — {fix['fix_description']}",
            "body":  f"**Error:** `{error}`\n\n**Fix:** {fix['fix_description']}\n\n_Auto-generated by self_healer.py_",
            "head": branch, "base": "main",
        }).json()
        print(f"[self_healer] PR created: {pr.get('html_url', 'unknown')}")
        return True
    except Exception as e:
        print(f"[self_healer] PR failed: {e}")
        return False


def _calendar_task(calendar_svc, title, desc):
    date_str = datetime.now(et).strftime("%Y-%m-%d")
    try:
        calendar_svc.events().insert(
            calendarId="primary",
            body={"summary": title, "description": desc,
                  "start": {"date": date_str}, "end": {"date": date_str}, "colorId": "11"},
        ).execute()
    except Exception as e:
        print(f"[self_healer] Calendar failed: {e}")


def _trigger_research(module_name, error, category="unknown"):
    """Trigger video-research.yml — self-learning research loop."""
    tool = module_name.replace("_", " ")
    # Build targeted queries per failure category
    if category == "content":
        queries = (
            f"Claude Haiku carousel content generation Python 2025 {error[:40]},"
            f"fix empty slides content generation LLM output parsing,"
            f"topic picker inspiration library no topics scored"
        )
        topic = f"content generation fix — {tool}"
    elif category == "art":
        queries = (
            f"Playwright PNG render GitHub Actions {error[:40]} fix 2025,"
            f"ffmpeg motion video carousel Python automation fix,"
            f"html to image node playwright headless render failure"
        )
        topic = f"art render fix — {tool}"
    else:
        queries = (
            f"how to implement {tool} with Claude code Python 2025,"
            f"{tool} {error[:40]} fix Python"
        )
        topic = f"fix {tool} failure"
    try:
        r = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/video-research.yml/dispatches",
            headers=_gh_headers(),
            json={"ref": "main", "inputs": {
                "topic": topic,
                "queries": queries,
                "max_per_query": "3",
            }},
        )
        ok = r.status_code == 204
        print(f"[self_healer] Research {'triggered' if ok else 'FAILED'} for {tool}")
        return ok
    except Exception as e:
        print(f"[self_healer] Research trigger error: {e}")
        return False


def _fetch_workflow_logs(run_id):
    """Fetch last 2000 chars of GitHub Actions log for a run ID. Non-fatal."""
    if not run_id or not GITHUB_TOKEN:
        return ""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/logs"
        r = requests.get(url, headers=_gh_headers(), allow_redirects=True, timeout=15)
        if r.status_code == 200 and r.content:
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                for name in z.namelist():
                    if name.endswith(".txt"):
                        text = z.read(name).decode("utf-8", errors="replace")
                        return text[-2000:]
    except Exception as e:
        print(f"[self_healer] Log fetch failed: {e}")
    return ""


def _autonomous_solve(module_name, error, tb, run_id=None):
    """Autonomous multi-step solver: read workflow logs + transcript artifacts,
    ask Claude Sonnet to analyze everything together and propose an actionable fix.
    Returns a fix dict (same shape as _haiku_fix) or None on failure.

    Used as an escalation step BEFORE creating a calendar task.
    Implements the 4AM agent autonomous problem-solving pattern:
    - Read actual pipeline artifacts (logs, transcripts)
    - Analyze the full context, not just the traceback
    - Propose a specific fix or root cause
    - Only fall back to calendar task if Claude cannot determine a fix
    """
    print(f"[self_healer] Autonomous solve: reading pipeline context for {module_name}...")

    # 1. Gather workflow logs if a run_id is available
    wf_logs = _fetch_workflow_logs(run_id) if run_id else ""

    # 2. Look for any SRT/transcript artifacts from the last capture run
    transcript_snippet = ""
    for candidate in ["/tmp/capture_artifact", "/tmp"]:
        import glob as _glob
        for ext in ("*.srt", "*.txt"):
            hits = _glob.glob(f"{candidate}/{ext}")
            if hits:
                try:
                    with open(hits[0], encoding="utf-8", errors="replace") as fh:
                        transcript_snippet = fh.read()[:1000]
                    break
                except Exception:
                    pass
        if transcript_snippet:
            break

    # 3. Read the failing script for context
    script_snippet = ""
    script_path = f"scripts/4am_agent/{module_name}.py"
    try:
        sr = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{script_path}",
            headers=_gh_headers(),
        )
        if sr.status_code == 200:
            import base64 as _b64
            raw = _b64.b64decode(sr.json()["content"]).decode("utf-8", errors="replace")
            script_snippet = raw[:2000]
    except Exception:
        pass

    # 4. Ask Claude Sonnet to analyze everything and propose a fix
    context_parts = [
        f"Module: {module_name}",
        f"Error: {error}",
        f"Traceback:\n{tb[:1500]}",
    ]
    if script_snippet:
        context_parts.append(f"Script (first 2000 chars):\n{script_snippet}")
    if wf_logs:
        context_parts.append(f"Workflow logs (last 2000 chars):\n{wf_logs}")
    if transcript_snippet:
        context_parts.append(f"Transcript artifact (first 1000 chars):\n{transcript_snippet}")

    prompt = (
        "\n\n---\n".join(context_parts) +
        "\n\n---\n"
        "You are the 4AM autonomous agent. Analyze the failure above.\n"
        "Step 1: Identify the root cause (not just the error message).\n"
        "Step 2: Check if the transcript/logs reveal additional context.\n"
        "Step 3: Propose the MINIMAL fix (1-10 lines of Python).\n"
        "Step 4: If fix is a config/auth issue (wrong secret, missing env var), describe exact steps.\n\n"
        "Return JSON only:\n"
        '{"confidence": 0-100, "root_cause": "one sentence", "fix_description": "one sentence",\n'
        ' "file_to_edit": "path or null", "old_code": "exact string or null", "new_code": "replacement or null",\n'
        ' "is_config_issue": true/false, "config_steps": "steps if config issue or null"}\n'
        "confidence >= 80 means you are certain the fix is correct."
    )

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        result = json.loads(text)
        print(f"[self_healer] Autonomous solve: confidence={result.get('confidence',0)} — {result.get('root_cause','?')}")
        return result
    except Exception as e:
        print(f"[self_healer] Autonomous solve failed: {e}")
        return None


def run():
    """Main entry — reads failures and heals what it can. (C3: run_results param removed)"""
    if not os.path.exists(FAILURES_FILE):
        print("[self_healer] No failures file — nothing to heal.")
        return {"prs_created": 0, "researched": 0, "calendar_tasks": 0}

    with open(FAILURES_FILE) as f:
        failures = json.load(f)

    if not failures:
        print("[self_healer] No failures. All clear.")
        return {"prs_created": 0, "researched": 0, "calendar_tasks": 0}

    calendar_svc = build("calendar", "v3", credentials=_creds())

    # Load dedup state from GitHub (W1/W2 fix)
    today    = datetime.now(et).strftime("%Y-%m-%d")
    healed   = _load_from_github(HEALED_FILE)
    researched = _load_from_github(RESEARCHED_FILE)

    prs = cal_tasks = researched_count = 0

    for name, data in failures.items():
        err = data.get("error", "")
        tb  = data.get("traceback", "")
        cat = _categorize(err, tb)
        print(f"[self_healer] {name}: {cat} — {err[:80]}")

        if cat == "transient":
            print(f"[self_healer]   Transient — will retry tomorrow automatically.")

        elif cat == "script":
            if _is_schema_error(err, tb):
                print(f"[self_healer]   Schema error detected — using schema-aware fix with live headers.")
                fix = _schema_aware_haiku_fix(name, err, tb)
            else:
                fix = _haiku_fix(name, err, tb)
            if fix.get("confidence", 0) >= 70 and fix.get("old_code"):
                if _create_pr(name, fix, err):
                    prs += 1
                    continue
            # Fast fix not confident — try autonomous multi-context solver
            run_id = data.get("run_id")
            auto = _autonomous_solve(name, err, tb, run_id=run_id)
            if auto and auto.get("confidence", 0) >= 80 and auto.get("old_code"):
                if _create_pr(name, auto, err):
                    prs += 1
                    continue
            # Still stuck — research + calendar (with dedup)
            if _days_since(researched.get(name, {}).get("triggered", "")) >= DEDUP_DAYS:
                if _trigger_research(name, err):
                    researched[name] = {"triggered": today, "error": err[:100]}
                    researched_count += 1
            else:
                print(f"[self_healer]   Research already triggered for {name} — skipping.")

            root_cause = (auto or {}).get("root_cause", "")
            config_steps = (auto or {}).get("config_steps", "")
            task_body = (
                f"Error: {err}\n\nRoot cause analysis: {root_cause or 'see traceback'}\n\n"
                + (f"Config steps:\n{config_steps}\n\n" if config_steps else "")
                + "Auto-fix attempted but confidence too low. Research triggered — check Drive Resources."
            )
            if _days_since(healed.get(name, {}).get("task_created", "")) >= DEDUP_DAYS:
                _calendar_task(calendar_svc, f"⚠️ SCRIPT ERROR: {name}", task_body)
                healed[name] = {"task_created": today, "type": "script"}
                cal_tasks += 1
            else:
                print(f"[self_healer]   Calendar task already exists for {name} — skipping.")

        elif cat == "config":
            if _days_since(healed.get(name, {}).get("task_created", "")) >= DEDUP_DAYS:
                _calendar_task(calendar_svc,
                    f"🔴 CONFIG FIX NEEDED: {name}",
                    f"Module failed with auth/config error.\n\nError: {err}\n\nCheck: GitHub secrets, API keys, sharing permissions on Google SA.")
                healed[name] = {"task_created": today, "type": "config"}
                cal_tasks += 1
            else:
                print(f"[self_healer]   Calendar task already exists for {name} — skipping.")

        elif cat in ("content", "art"):
            label = "CONTENT GENERATION" if cat == "content" else "ART/RENDER"
            print(f"[self_healer]   {label} failure — triggering targeted research loop.")
            # Research with category-specific queries
            if _days_since(researched.get(name, {}).get("triggered", "")) >= DEDUP_DAYS:
                if _trigger_research(name, err, category=cat):
                    researched[name] = {"triggered": today, "error": err[:100], "cat": cat}
                    researched_count += 1
            else:
                print(f"[self_healer]   Research already triggered for {name} — skipping.")
            # Also try autonomous solver — it can detect missing env vars, API changes, etc.
            run_id = data.get("run_id")
            auto = _autonomous_solve(name, err, tb, run_id=run_id)
            if auto and auto.get("confidence", 0) >= 80 and auto.get("old_code"):
                if _create_pr(name, auto, err):
                    prs += 1
                    continue
            if _days_since(healed.get(name, {}).get("task_created", "")) >= DEDUP_DAYS:
                root_cause = (auto or {}).get("root_cause", "")
                _calendar_task(calendar_svc,
                    f"⚠️ {label} FAILURE: {name}",
                    f"Error: {err}\n\nRoot cause: {root_cause or 'see research findings'}\n\n"
                    f"Research triggered — check Drive Resources for findings.\n"
                    f"{'Carousel generation / topic picking / Haiku output parsing.' if cat == 'content' else 'PNG render / motion export / Playwright / ffmpeg.'}")
                healed[name] = {"task_created": today, "type": cat}
                cal_tasks += 1

        else:  # unknown — autonomous solver first, then research + calendar
            run_id = data.get("run_id")
            auto = _autonomous_solve(name, err, tb, run_id=run_id)
            if auto and auto.get("confidence", 0) >= 80 and auto.get("old_code"):
                if _create_pr(name, auto, err):
                    prs += 1
                    continue
            # Autonomous solver didn't produce a PR — research + calendar
            if _days_since(researched.get(name, {}).get("triggered", "")) >= DEDUP_DAYS:
                if _trigger_research(name, err):
                    researched[name] = {"triggered": today, "error": err[:100]}
                    researched_count += 1
            else:
                print(f"[self_healer]   Research already triggered for {name} — skipping.")

            root_cause = (auto or {}).get("root_cause", "")
            config_steps = (auto or {}).get("config_steps", "")
            task_body = (
                f"Error: {err}\n\nRoot cause analysis: {root_cause or 'unknown — see traceback'}\n\n"
                + (f"Config steps:\n{config_steps}\n\n" if config_steps else "")
                + "Autonomous solver and research both triggered. Check Drive Resources for findings."
            )
            if _days_since(healed.get(name, {}).get("task_created", "")) >= DEDUP_DAYS:
                _calendar_task(calendar_svc, f"❓ UNKNOWN FAILURE: {name}", task_body)
                healed[name] = {"task_created": today, "type": "unknown"}
                cal_tasks += 1
            else:
                print(f"[self_healer]   Calendar task already exists for {name} — skipping.")

    # Persist dedup state to GitHub
    _push_to_github(HEALED_FILE, healed)
    _push_to_github(RESEARCHED_FILE, researched)

    print(f"[self_healer] Done. PRs: {prs} | Research: {researched_count} | Calendar: {cal_tasks}")
    return {"prs_created": prs, "researched": researched_count, "calendar_tasks": cal_tasks}
