"""person_evidence_dispatcher.py — detect "find more clips of this person"
intent in capture notes and auto-dispatch video-research.yml in
person_evidence_mining mode.

Called from capture_pipeline.py main() AFTER the seed capture has
completed. Non-blocking: any failure here is logged but does NOT
break the normal capture.

Triggers (any one matches):
  - "find more clips of this person"
  - "find N more clips" / "find six more clips" (numerals or word numbers)
  - "same person" / "same guy"
  - "use this as hook" + (any of the above)
  - explicit: "mode: person_evidence_mining"

Optional notes-embedded fields:
  - Person: <Name>          (or "person: <name>")
  - Requirement: <text>     (or "requirement: ...", "evidence: ...",
                             "filter: ...")
  - Count: 6                (or "find 6", "6 clips", etc.)

If person_name not found in notes, infer from caption / transcript via
the shared Claude → OpenAI → Gemini LLM cascade.
"""

from __future__ import annotations
import json
import os
import re
import subprocess
from datetime import datetime, timezone

GH_BIN     = os.environ.get("GH_BIN", "/Users/priscilahigashi/bin/gh")
REPO       = "priihigashi/oak-park-ai-hub"

try:
    from _llm_fallback import llm_text
except Exception:
    llm_text = None

# ── trigger detection ───────────────────────────────────────────────────────

_TRIGGER_PATTERNS = [
    r"find\s+(?:\d+|\w+)?\s*more\s+clips",
    r"more\s+clips\s+of\s+this\s+person",
    r"\bsame\s+(?:person|guy|man|woman|speaker)\b",
    r"\buse\s+this\s+as\s+(?:the\s+)?hook\b",
    r"mode\s*:\s*person_evidence_mining\b",
    r"\bperson_evidence_mining\b",
    r"\bclip[-\s]?min(?:ing|e)\b",
]
_TRIGGER_RE = re.compile("|".join(_TRIGGER_PATTERNS), re.IGNORECASE)


def is_evidence_mining_request(notes: str) -> bool:
    if not notes or not notes.strip():
        return False
    return bool(_TRIGGER_RE.search(notes))


# ── parse person name / count / requirement from notes ─────────────────────

_NUM_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def parse_target_count(notes: str, default: int = 6) -> int:
    """Look for 'find N more', 'N clips', 'count: N'."""
    m = re.search(r"(?:find|need|want|count\s*:?\s*)\s+(\d+)\s+(?:more\s+)?clips?",
                  notes, re.IGNORECASE)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 20:
                return n
        except Exception:
            pass
    m = re.search(r"\b(\d+)\s+more\s+clips?\b", notes, re.IGNORECASE)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 20:
                return n
        except Exception:
            pass
    # word numbers
    m = re.search(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+more\s+clips?",
                  notes, re.IGNORECASE)
    if m:
        return _NUM_WORDS.get(m.group(1).lower(), default)
    return default


def parse_person_name(notes: str) -> str:
    """Look for 'Person: <name>' / 'person is <name>' / 'his/her name is <name>'."""
    patterns = [
        r"(?:^|\n)\s*person\s*:\s*([^\n]+)",
        r"\bperson\s+is\s+([^.,;\n]+)",
        r"\b(?:his|her|their)\s+name\s+is\s+([^.,;\n]+)",
        r"\bname\s*:\s*([^\n]+)",
    ]
    for pat in patterns:
        m = re.search(pat, notes, re.IGNORECASE)
        if m:
            name = m.group(1).strip().strip('"').strip("'")
            # Strip trailing common phrases
            name = re.sub(r"\s+(then|so|and|but|find|use|same).*$", "", name,
                          flags=re.IGNORECASE)
            if 2 <= len(name) <= 80:
                return name
    return ""


def parse_evidence_requirement(notes: str) -> str:
    """Look for 'Requirement: ...', 'evidence: ...', 'filter: ...', or
    'looking for ...'. Falls back to a generic placeholder."""
    patterns = [
        r"(?:^|\n)\s*requirement\s*:\s*([^\n]+(?:\n(?!\s*\w+\s*:)[^\n]*)*)",
        r"(?:^|\n)\s*evidence\s*:\s*([^\n]+(?:\n(?!\s*\w+\s*:)[^\n]*)*)",
        r"(?:^|\n)\s*filter\s*:\s*([^\n]+(?:\n(?!\s*\w+\s*:)[^\n]*)*)",
        r"\blooking\s+for\s+([^\n.]+)",
    ]
    for pat in patterns:
        m = re.search(pat, notes, re.IGNORECASE)
        if m:
            r = m.group(1).strip()
            if 8 <= len(r) <= 1000:
                return r
    return ("Same public person making statements that match the seed clip's "
            "topic — unfair toward a group, morally contradictory, or "
            "needing context against public persona.")


# ── infer person from caption/transcript via Haiku ──────────────────────────

def infer_person_name(caption: str, transcript: str, creator_name: str = "") -> tuple[str, float]:
    """Return (name, confidence). Empty string if cannot infer."""
    if creator_name and creator_name.strip():
        # Creator handle is a strong signal
        return creator_name.strip(), 0.85
    if llm_text is None:
        return "", 0.0
    excerpt = (caption or "")[:400] + "\n\n" + (transcript or "")[:1500]
    if not excerpt.strip():
        return "", 0.0
    prompt = f"""From the social media post excerpt below, identify the public figure who is the main speaker. Return STRICT JSON only.

Excerpt:
\"\"\"
{excerpt}
\"\"\"

If you can identify a single named public figure who is speaking on camera, return:
{{"name": "Their Full Name", "confidence": 0.0-1.0}}

If the speaker is unclear, anonymous, or you cannot tell, return:
{{"name": "", "confidence": 0.0}}

NO prose. JSON only."""
    try:
        raw = llm_text(
            prompt,
            model_tier="haiku",
            max_tokens=200,
            temperature=0,
            context="person_evidence_dispatcher.infer_person_name",
        ).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"```\s*$", "", raw)
        m_json = re.search(r"\{.*\}", raw, re.DOTALL)
        if m_json:
            raw = m_json.group()
        data = json.loads(raw)
        name = str(data.get("name", "")).strip()
        conf = float(data.get("confidence", 0.0))
        if name and 2 <= len(name) <= 80:
            return name, max(0.0, min(1.0, conf))
    except Exception as e:
        print(f"  [evidence-mining] person inference failed: {e}")
    return "", 0.0


# ── dispatch ────────────────────────────────────────────────────────────────

def _log_dispatch_failure(stage: str, error: str):
    """Best-effort write to 🚨 Pipeline Failures tab. Never raises."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        token_raw = os.environ.get("SHEETS_TOKEN", "")
        if not token_raw:
            return
        creds = Credentials.from_authorized_user_info(
            json.loads(token_raw),
            scopes=["https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"],
        )
        svc = build("sheets", "v4", credentials=creds)
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        run_url = (f"https://github.com/{REPO}/actions/runs/{run_id}"
                   if run_id else "")
        svc.spreadsheets().values().append(
            spreadsheetId="1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU",
            range="'🚨 Pipeline Failures'!A:H",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[
                datetime.now(timezone.utc).isoformat(),
                "capture_pipeline.py (person_evidence_dispatcher)",
                run_id, stage, str(error)[:500], run_url, "", "",
            ]]},
        ).execute()
    except Exception as e:
        print(f"  [evidence-mining] failure-log write failed: {e}")


def dispatch_evidence_mining(seed_url: str, person_name: str,
                             evidence_requirement: str, target_clip_count: int,
                             niche: str) -> tuple[bool, str]:
    """Call gh workflow run video-research.yml. Returns (success, message)."""
    if not all([seed_url, person_name, evidence_requirement]):
        return False, "missing_inputs"
    if not os.path.exists(GH_BIN):
        # Try plain `gh` on PATH (e.g. inside GitHub Actions runner)
        gh_cmd = ["gh"]
    else:
        gh_cmd = [GH_BIN]
    cmd = gh_cmd + [
        "workflow", "run", "video-research.yml",
        "--repo", REPO,
        "-f", "mode=person_evidence_mining",
        "-f", f"seed_url={seed_url}",
        "-f", f"person_name={person_name}",
        "-f", f"evidence_requirement={evidence_requirement}",
        "-f", f"target_clip_count={target_clip_count}",
        "-f", f"niche={niche}",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, (r.stdout.strip() or "dispatched")
        msg = (r.stderr or r.stdout)[:400]
        _log_dispatch_failure("dispatch", msg)
        return False, msg
    except FileNotFoundError as e:
        _log_dispatch_failure("dispatch", f"gh CLI not found: {e}")
        return False, f"gh CLI not found: {e}"
    except Exception as e:
        _log_dispatch_failure("dispatch", str(e))
        return False, str(e)


# ── orchestrator entry ──────────────────────────────────────────────────────

def maybe_dispatch_from_capture(notes: str, seed_url: str, niche: str,
                                caption: str = "", transcript: str = "",
                                creator_name: str = "") -> dict:
    """Single entry point called from capture_pipeline.py main() after seed
    capture completes. Always returns a status dict; never raises.

    Status dict shape:
      {"triggered": bool, "dispatched": bool, "reason": str,
       "person_name": str, "person_confidence": float,
       "target_clip_count": int, "evidence_requirement": str}
    """
    out = {
        "triggered": False, "dispatched": False, "reason": "",
        "person_name": "", "person_confidence": 0.0,
        "target_clip_count": 6, "evidence_requirement": "",
    }
    if not is_evidence_mining_request(notes):
        out["reason"] = "no_trigger_in_notes"
        return out
    out["triggered"] = True
    print("\n[evidence-mining] notes match person_evidence_mining trigger")

    # 1) Person name
    person = parse_person_name(notes)
    confidence = 1.0 if person else 0.0
    if not person:
        person, confidence = infer_person_name(caption, transcript, creator_name)
        if person:
            print(f"  [evidence-mining] inferred person={person} (confidence={confidence:.2f})")
    if not person:
        out["reason"] = "no_person_name_could_be_inferred"
        _log_dispatch_failure("parse_person", out["reason"])
        return out
    out["person_name"] = person
    out["person_confidence"] = confidence

    # 2) Count
    out["target_clip_count"] = parse_target_count(notes, default=6)

    # 3) Requirement
    out["evidence_requirement"] = parse_evidence_requirement(notes)

    print(f"  [evidence-mining] person={person} count={out['target_clip_count']} "
          f"niche={niche}")
    print(f"  [evidence-mining] requirement={out['evidence_requirement'][:120]}…")

    # 4) Dispatch
    ok, msg = dispatch_evidence_mining(
        seed_url=seed_url,
        person_name=person,
        evidence_requirement=out["evidence_requirement"],
        target_clip_count=out["target_clip_count"],
        niche=niche or "brazil",
    )
    out["dispatched"] = ok
    out["reason"] = msg
    if ok:
        print(f"  [evidence-mining] ✅ dispatched video-research.yml")
    else:
        print(f"  [evidence-mining] ❌ dispatch failed: {msg}")
    return out
