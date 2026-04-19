#!/usr/bin/env python3
"""
nonnegotiables_updater.py — Daily extraction of locked rules into NONNEGOTIABLES.md

Sources (in priority order):
  1. CLAUDE.md (local repo) — sections with NON-NEGOTIABLE / LOCKED / NEVER in headings
  2. Global CLAUDE.md (~/.claude/CLAUDE.md) — same scan
  3. Last 7 handoff docs in Drive (Productivity & Routine folder) — PENDING EXTRACTION section
  4. Memory files (~/.claude/projects/.../memory/) — feedback_*.md files

What it does:
  - Scans sources for new rule candidates
  - Compares against existing NONNEGOTIABLES.md (deduplicates by heading text)
  - Appends new rules to PENDING EXTRACTION section for human review
  - Commits the updated NONNEGOTIABLES.md via git

Never overwrites existing LOCKED sections — only appends to PENDING EXTRACTION.
Priscila manually promotes rules from PENDING → LOCKED by editing the file.

Runs via: nonnegotiables.yml (daily 2:00 AM ET)
"""
import os, re, json, urllib.request, urllib.parse, subprocess
from datetime import datetime, timedelta
from pathlib import Path
import pytz

ET = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).parent.parent
NONNEG_PATH = REPO_ROOT / "NONNEGOTIABLES.md"
CLAUDE_MD_LOCAL = REPO_ROOT / "CLAUDE.md"
CLAUDE_MD_GLOBAL = Path.home() / ".claude" / "CLAUDE.md"
MEMORY_DIR = Path.home() / ".claude" / "projects" / "-Users-priscilahigashi" / "memory"

HANDOFF_FOLDER_ID = "1b8Cfc8lJhu5unDaxDQIdo4xdN6X7n1nS"

# Keywords that signal a locked rule heading
LOCK_KEYWORDS = [
    "non-negotiable", "nonnegotiable", "never", "always", "mandatory",
    "locked", "banned", "required", "rule:", "default on", "default flow",
]


def _token():
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        p = Path.home() / "ClaudeWorkspace" / "Credentials" / "sheets_token.json"
        if p.exists():
            raw = p.read_text()
    if not raw:
        return ""
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
        return resp.get("access_token", "")
    except Exception:
        return ""


def _read_handoff_docs():
    """Fetch text of last 7 handoff docs from Drive."""
    token = _token()
    if not token:
        return []

    cutoff = (datetime.now(ET) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    q = (f"'{HANDOFF_FOLDER_ID}' in parents "
         f"and mimeType='application/vnd.google-apps.document' "
         f"and name contains 'HANDOFF' "
         f"and trashed=false "
         f"and modifiedTime > '{cutoff}'")
    q_enc = urllib.parse.quote(q)
    url = (f"https://www.googleapis.com/drive/v3/files"
           f"?q={q_enc}&fields=files(id,name)&supportsAllDrives=true"
           f"&includeItemsFromAllDrives=true")
    try:
        files = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read()
        ).get("files", [])
    except Exception as e:
        print(f"  Drive list failed: {e}")
        return []

    texts = []
    for f in files[:7]:
        try:
            export_url = (f"https://www.googleapis.com/drive/v3/files/{f['id']}"
                          f"/export?mimeType=text%2Fplain")
            text = urllib.request.urlopen(
                urllib.request.Request(export_url, headers={"Authorization": f"Bearer {token}"})
            ).read().decode("utf-8", errors="replace")
            texts.append((f["name"], text[:8000]))
        except Exception:
            pass
    return texts


def _extract_rule_candidates(text, source_label):
    """
    Extract candidate rule paragraphs from a markdown text.
    A candidate is a heading line whose text matches LOCK_KEYWORDS,
    followed by the body lines until the next heading.
    Returns list of (heading, body_text, source).
    """
    candidates = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Match headings (## or **) with lock keywords
        is_heading = line.startswith("#") or (line.startswith("**") and line.endswith("**"))
        heading_text = re.sub(r'^#+\s*|\*\*', '', line).strip()
        if is_heading and any(kw in heading_text.lower() for kw in LOCK_KEYWORDS):
            # Collect body until next heading
            body_lines = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line.startswith("#") or (next_line.startswith("**") and next_line.endswith("**") and len(next_line) > 4):
                    break
                if next_line:
                    body_lines.append(next_line)
                j += 1
            body = " ".join(body_lines[:5])  # first 5 non-empty lines
            if body:
                candidates.append((heading_text[:80], body[:300], source_label))
        i += 1
    return candidates


def _existing_headings():
    """Return set of normalized heading texts already in NONNEGOTIABLES.md."""
    if not NONNEG_PATH.exists():
        return set()
    text = NONNEG_PATH.read_text()
    headings = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("**") and line.endswith("**"):
            headings.add(line.strip("*").strip().lower())
        elif line.startswith("#"):
            headings.add(re.sub(r'^#+\s*', '', line).strip().lower())
    return headings


def _append_pending(new_rules):
    """Append new rule candidates to PENDING EXTRACTION section."""
    if not new_rules:
        return 0
    text = NONNEG_PATH.read_text()
    now = datetime.now(ET).strftime("%Y-%m-%d")

    additions = []
    for heading, body, source in new_rules:
        additions.append(f"\n- **{heading}** (from {source}, {now})\n  {body[:200]}\n")

    # Insert before end of PENDING EXTRACTION section
    marker = "_(none yet)_" if "_(none yet)_" in text else ""
    if marker:
        text = text.replace(marker, "\n".join(additions))
    else:
        # Append to end of PENDING section
        pending_idx = text.rfind("## PENDING EXTRACTION")
        if pending_idx != -1:
            text = text + "\n" + "\n".join(additions)
        else:
            text += "\n\n## PENDING EXTRACTION\n" + "\n".join(additions)

    NONNEG_PATH.write_text(text)
    return len(new_rules)


def _update_datestamp():
    text = NONNEG_PATH.read_text()
    now = datetime.now(ET).strftime("%Y-%m-%d")
    text = re.sub(
        r"_Last updated:.*_",
        f"_Last updated: {now} (auto-updated by nonnegotiables_updater.py)_",
        text,
    )
    NONNEG_PATH.write_text(text)


def _git_commit(count):
    try:
        subprocess.run(["git", "add", "NONNEGOTIABLES.md"], cwd=REPO_ROOT, check=True)
        msg = f"chore: nonnegotiables_updater — {count} new candidate(s) extracted {datetime.now(ET).strftime('%Y-%m-%d')}"
        subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
        print(f"  Committed + pushed NONNEGOTIABLES.md ({count} new rules)")
    except subprocess.CalledProcessError as e:
        print(f"  Git commit skipped or failed: {e}")


def main():
    print(f"\n[nonnegotiables_updater] {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    existing = _existing_headings()
    candidates = []

    # Source 1: repo CLAUDE.md
    if CLAUDE_MD_LOCAL.exists():
        found = _extract_rule_candidates(CLAUDE_MD_LOCAL.read_text(), "repo CLAUDE.md")
        print(f"  repo CLAUDE.md: {len(found)} candidates")
        candidates.extend(found)

    # Source 2: global CLAUDE.md
    if CLAUDE_MD_GLOBAL.exists():
        found = _extract_rule_candidates(CLAUDE_MD_GLOBAL.read_text(), "global CLAUDE.md")
        print(f"  global CLAUDE.md: {len(found)} candidates")
        candidates.extend(found)

    # Source 3: memory feedback files
    if MEMORY_DIR.exists():
        for mf in MEMORY_DIR.glob("feedback_*.md"):
            text = mf.read_text()
            found = _extract_rule_candidates(text, f"memory/{mf.name}")
            candidates.extend(found)
        print(f"  memory/ feedback files scanned")

    # Source 4: last 7 handoff docs from Drive
    handoffs = _read_handoff_docs()
    for name, text in handoffs:
        found = _extract_rule_candidates(text, f"handoff/{name}")
        candidates.extend(found)
    print(f"  Drive handoffs scanned: {len(handoffs)} docs")

    # Deduplicate against existing
    new_rules = []
    seen = set()
    for heading, body, source in candidates:
        key = heading.lower()
        if key not in existing and key not in seen:
            new_rules.append((heading, body, source))
            seen.add(key)

    print(f"  New candidates: {len(new_rules)} (after dedup against {len(existing)} existing)")

    _update_datestamp()
    added = _append_pending(new_rules)

    if added > 0 or True:  # always commit to update datestamp
        _git_commit(added)

    print(f"[nonnegotiables_updater] Done — {added} rules added to PENDING EXTRACTION")
    return added


if __name__ == "__main__":
    main()
