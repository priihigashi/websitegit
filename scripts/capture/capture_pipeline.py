#!/usr/bin/env python3
"""
capture_pipeline.py
===================
Capture Pipeline v2 — runs via GitHub Actions, triggered from phone.

WHAT IT DOES:
  1. Downloads audio from Instagram/TikTok/YouTube using yt-dlp
  2. Transcribes with OpenAI Whisper API (whisper-1)
  3. Saves transcript locally (uploaded as artifact)
  4. Routes based on --project:
     book      → Claude fact-checks → story doc in The Book Drive folder
                → Book Tracker Stories tab → Calendar task
     sovereign → Claude analyses   → study doc in SOVEREIGN Drive folder
                → Calendar task
     content   → Claude classifies niche → Inspiration Library tab
                → Calendar task

REQUIRED ENV VARS (all stored as GitHub Secrets in oak-park-ai-hub):
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  GOOGLE_SA_KEY   (base64-encoded service account JSON — same secret used by other workflows)
"""

import os
import sys
import json
import re
import argparse
import tempfile
import base64
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── CONFIG ───────────────────────────────────────────────────────────────────

OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")

# Spreadsheet IDs — hardcoded as defaults, can override via env
BOOK_TRACKER_ID    = os.getenv("BOOK_TRACKER_ID",    "1SeDFDisb0uNeyfyv5fCS_0x5EbkJRcFeS6CGuUmlH7c")
IDEAS_INBOX_ID     = os.getenv("IDEAS_INBOX_ID",     "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")

# Drive folder IDs — hardcoded as defaults
BOOK_FOLDER_ID      = "1HlY1tmUHmRZ_ZfPUzGpY_j7sHbe_OCz1"
SOVEREIGN_FOLDER_ID = "1L89dLiVYfjNu3uz3l3S_rvZPxd2I8xjZ"

TRANSCRIPTS_DIR = Path("transcripts")
TRANSCRIPTS_DIR.mkdir(exist_ok=True)


# ─── GOOGLE AUTH ──────────────────────────────────────────────────────────────

def _get_creds(scopes: list):
    """Return Google credentials. Uses GOOGLE_SA_KEY env var (base64 JSON)."""
    from google.oauth2.service_account import Credentials

    # oak-park-ai-hub uses GOOGLE_SA_KEY (base64 encoded)
    sa_b64 = os.getenv("GOOGLE_SA_KEY")
    if sa_b64:
        sa_info = json.loads(base64.b64decode(sa_b64))
        return Credentials.from_service_account_info(sa_info, scopes=scopes)

    # Fallback: local file
    creds_path = Path("credentials/service_account.json")
    if creds_path.exists():
        return Credentials.from_service_account_file(str(creds_path), scopes=scopes)

    raise RuntimeError("No Google credentials. Set GOOGLE_SA_KEY secret.")


def get_sheets_client():
    try:
        import gspread
        creds = _get_creds(["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)
    except Exception as e:
        print(f"  SKIP Sheets: {e}")
        return None


def get_drive_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds([
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
        ])
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"  SKIP Drive: {e}")
        return None


def get_docs_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds(["https://www.googleapis.com/auth/documents"])
        return build("docs", "v1", credentials=creds)
    except Exception as e:
        print(f"  SKIP Docs: {e}")
        return None


def get_calendar_service():
    try:
        from googleapiclient.discovery import build
        creds = _get_creds(["https://www.googleapis.com/auth/calendar"])
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"  SKIP Calendar: {e}")
        return None


# ─── STEP 1: DOWNLOAD ─────────────────────────────────────────────────────────

def download_audio(url: str, tmp_dir: str) -> str:
    print(f"\n[1/3] Downloading audio: {url}")
    output = os.path.join(tmp_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp", "--extract-audio", "--audio-format", "mp3",
        "--audio-quality", "0", "--output", output,
        "--no-playlist", "--quiet", url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        sys.exit(1)

    mp3 = os.path.join(tmp_dir, "audio.mp3")
    if not os.path.exists(mp3):
        for ext in ["m4a", "webm", "ogg", "wav"]:
            alt = os.path.join(tmp_dir, f"audio.{ext}")
            if os.path.exists(alt):
                mp3 = alt
                break

    size = os.path.getsize(mp3) / 1024
    print(f"  Downloaded ({size:.0f} KB)")
    return mp3


# ─── STEP 2: TRANSCRIBE ───────────────────────────────────────────────────────

def transcribe_audio(audio_path: str) -> str:
    print("\n[2/3] Transcribing with Whisper...")
    if not OPENAI_API_KEY:
        print("  ERROR: OPENAI_API_KEY not set")
        sys.exit(1)
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="text"
        )
    print(f"  Transcribed ({len(result)} chars)")
    return result


# ─── STEP 3: SAVE TRANSCRIPT ──────────────────────────────────────────────────

def save_transcript(transcript: str, url: str, story_id: str, project: str) -> str:
    print("\n[3/3] Saving transcript...")
    slug = url.split("/reel/")[-1].split("/")[0].split("?")[0] if "/reel/" in url else "capture"
    filename = f"{story_id}_{slug}_transcript.txt"
    filepath = TRANSCRIPTS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"STORY ID: {story_id}\nPROJECT: {project}\nURL: {url}\nDATE: {datetime.now()}\n\n{transcript}")
    print(f"  Saved: {filepath}")
    return str(filepath)


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def analyze_book(transcript: str, url: str, story_id: str, notes: str) -> str:
    if not ANTHROPIC_API_KEY:
        return f"[PENDING — ANTHROPIC_API_KEY required]\n\n{transcript}"
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("  Claude (claude-opus-4-6) fact-checking...")
    prompt = f"""Run capture_crazy_ideas skill for RECEIPTS book.

Story ID: {story_id}
Source URL: {url}
Notes: {notes or "None"}
Date: {datetime.now().strftime("%Y-%m-%d")}

TRANSCRIPT:
{transcript}

Produce STORY DOCUMENT (no markdown tables — plain text only):

STORY ID: {story_id}
BOOK SECTION: [Trump Pardons | Political Deals | Historical Context | Other]
DATE CAPTURED: {datetime.now().strftime("%Y-%m-%d")}
SOURCE URL: {url}
TRANSCRIPT: [paste above]

SPEAKER: [full name, title, affiliation]
CREDENTIALS: [what makes them credible or not]
CREDIBILITY: HIGH / MEDIUM / LOW / UNVERIFIED

BACKGROUND (8th grade level, 2-3 paragraphs):

CLAIMS MADE:
  Claim 1: [quote or paraphrase]
  Fact Check: TRUE / FALSE / PARTIALLY TRUE / UNVERIFIED
  Evidence: [what we found]
  Official Sources: [URL1] | [URL2] | [URL3]

SPEAKER VERIFICATION:
  Red flags: [vague? no sources?]
  Credibility: HIGH / MEDIUM / LOW / UNVERIFIED

MEETING VERIFICATION:
  Meeting claimed: YES [describe] / NO
  Evidence: [URL or "No corroborating evidence found"]
  Official confirmation: [yes/no/silent]

PRESIDENTIAL / OFFICIAL STATEMENTS:
  [Quote with source URL. If none: "No official statement found."]

PATTERN / CONNECTION:
  [Donations? Deals? Timing? Visits?]
  [PATTERN - investigate further] or [No pattern found yet]

VISUAL SUGGESTIONS:
  - [Image/screenshot idea 1]
  - [Image/screenshot idea 2]

SOVEREIGN POST ANGLE:
  Hook: [scroll-stopping opening line]
  Core message: [concrete examples, not just negatives]
  Format: [talking head / carousel / before-after]

PORTUGUESE ANGLE:
  Relevant to Brazilian audience: YES / NO
  PT-BR hook: [if YES]

QR CODE SOURCES:
  1. [Source name] - [URL]
  2. [Source name] - [URL]
  3. [Source name] - [URL]

BOOK READY: YES / NO / NEEDS MORE RESEARCH"""
    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def analyze_sovereign(transcript: str, url: str, story_id: str, notes: str) -> str:
    if not ANTHROPIC_API_KEY:
        return f"[PENDING — ANTHROPIC_API_KEY required]\n\n{transcript}"
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("  Claude (claude-opus-4-6) SOVEREIGN analysis...")
    prompt = f"""Analyze this content for the SOVEREIGN political inspiration page.
Study the format and identify how to do it better — more examples, more teaching, not just negatives.

Story ID: {story_id}
Source URL: {url}
Notes: {notes or "None"}

TRANSCRIPT:
{transcript}

Produce SOVEREIGN CAPTURE DOCUMENT (no markdown tables):

STORY ID: {story_id}
PROJECT: SOVEREIGN
DATE: {datetime.now().strftime("%Y-%m-%d")}
SOURCE URL: {url}

SPEAKER ANALYSIS:
  Who: [name, title, platform/following]
  Credibility: HIGH / MEDIUM / LOW / UNVERIFIED
  Red flags: [vague? no sources? only negatives?]

CONTENT ANALYSIS:
  Main message: [one sentence]
  Emotional tone: [anger / fear / inspiration / outrage]
  What works: [specific format strengths]
  What's missing: [e.g. no examples, only complaints, no solutions]

SOVEREIGN POST ANGLE:
  Hook: [opening line that stops the scroll]
  Core message: [what SOVEREIGN says differently — with concrete examples]
  Teaching moment: [what audience learns and can apply]
  Format: [talking head / carousel / before-after / text overlay]
  CTA: [what action we want]

PORTUGUESE ANGLE:
  Relevant to Brazilian audience: YES / NO
  PT-BR hook: [if YES]

STUDY NOTES (3 specific ways to do it better):
  1. [Improvement]
  2. [Improvement]
  3. [Improvement]

CONTENT READY: YES / NO / NEEDS REFINEMENT"""
    msg = client.messages.create(
        model="claude-opus-4-6", max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


def analyze_content(transcript: str, url: str, notes: str) -> dict:
    if not ANTHROPIC_API_KEY:
        return {"niche": "Oak Park", "classification": "NEEDS_REVIEW", "summary": transcript[:150]}
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("  Claude (claude-sonnet-4-6) classifying...")
    prompt = f"""Classify this video transcript for Oak Park Construction content pipeline.
URL: {url}
Notes: {notes or "None"}
TRANSCRIPT: {transcript}

Respond with JSON only:
{{"niche": "Oak Park" or "Brazil" or "UGC" or "News", "content_type": "Talking Head/Expert" or "Project Progress/Before-After" or "Product Tips" or "Other", "classification": "READY" or "NEEDS_REVIEW" or "NOT_RELEVANT", "summary": "one sentence", "hook": "suggested hook for Oak Park repost", "notes": "why classified this way"}}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {"niche": "Oak Park", "classification": "NEEDS_REVIEW", "summary": text[:150]}


# ─── DRIVE DOC ────────────────────────────────────────────────────────────────

def create_drive_doc(title: str, content: str, folder_id: str) -> str:
    drive = get_drive_service()
    if not drive:
        return ""
    try:
        file = drive.files().create(
            body={"name": title, "mimeType": "application/vnd.google-apps.document", "parents": [folder_id]},
            supportsAllDrives=True, fields="id,webViewLink"
        ).execute()
        file_id = file.get("id")
        doc_url = file.get("webViewLink", f"https://docs.google.com/document/d/{file_id}/edit")
        print(f"  Drive doc: {doc_url}")
        docs = get_docs_service()
        if docs and content:
            try:
                docs.documents().batchUpdate(
                    documentId=file_id,
                    body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]}
                ).execute()
            except Exception as e:
                print(f"  WARNING doc write: {e}")
        return doc_url
    except Exception as e:
        print(f"  WARNING Drive: {e}")
        return ""


# ─── SHEETS ───────────────────────────────────────────────────────────────────

def update_book_tracker(story_id, url, doc_url, analysis, notes):
    gc = get_sheets_client()
    if not gc:
        return
    try:
        sh = gc.open_by_key(BOOK_TRACKER_ID)
        stories = sh.worksheet("Stories")
        summary = analysis[:150].replace("\n", " ")
        section = "Other"
        for s in ["Trump Pardons", "Political Deals", "Historical Context"]:
            if s in analysis:
                section = s
                break
        stories.append_row([
            story_id, section, "", summary, url,
            "NEEDS REVIEW", notes or "", "", "", "", "",
            datetime.now().strftime("%Y-%m-%d"), url, doc_url, "NO"
        ])
        print(f"  Book Tracker Stories: {story_id} added")
        try:
            inbox = sh.worksheet("Inbox")
            for i, row in enumerate(inbox.get_all_values()):
                if url.split("?")[0] in str(row):
                    inbox.update_cell(i + 1, 4, story_id)
                    inbox.update_cell(i + 1, 5, f"CAPTURED {datetime.now().strftime('%Y-%m-%d')}")
                    break
        except Exception:
            pass
    except Exception as e:
        print(f"  WARNING Sheets: {e}")


def update_inspiration_library(url, transcript, classification):
    gc = get_sheets_client()
    if not gc:
        return
    try:
        sh = gc.open_by_key(IDEAS_INBOX_ID)
        lib = sh.worksheet("Inspiration Library")
        lib.append_row([
            datetime.now().strftime("%Y-%m-%d"), url,
            classification.get("summary", ""),
            classification.get("niche", "Oak Park"),
            classification.get("content_type", ""),
            classification.get("classification", "NEEDS_REVIEW"),
            transcript[:300],
            classification.get("hook", ""),
            classification.get("notes", ""),
        ])
        print("  Inspiration Library updated")
    except Exception as e:
        print(f"  WARNING Sheets: {e}")


# ─── CALENDAR ─────────────────────────────────────────────────────────────────

def create_calendar_task(story_id, project, url, doc_url, preview, notes):
    cal = get_calendar_service()
    if not cal:
        return
    labels = {"book": "BOOK CAPTURE", "sovereign": "SOVEREIGN CAPTURE", "content": "CONTENT CAPTURE"}
    label = labels.get(project, "CAPTURE")
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    try:
        cal.events().insert(calendarId="primary", body={
            "summary": f"{label} — {story_id} — Review Required",
            "description": (
                f"{label}: {story_id}\n\nSOURCE: {url}\n\nTRANSCRIPT PREVIEW:\n{preview[:400]}\n\n"
                f"DRIVE DOC: {doc_url or 'check artifacts'}\n\nNOTES: {notes or 'None'}\n\n"
                f"NEXT STEPS:\n1. Review story doc in Drive\n2. Verify sources manually\n"
                f"3. If BOOK READY: move to editing queue"
            ),
            "start": {"dateTime": tomorrow.isoformat(), "timeZone": "America/New_York"},
            "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat(), "timeZone": "America/New_York"},
        }).execute()
        print(f"  Calendar task: tomorrow 9am ET")
    except Exception as e:
        print(f"  WARNING Calendar: {e}")


# ─── PIPELINES ────────────────────────────────────────────────────────────────

def run_book(args, transcript):
    print("\n[BOOK] Running fact-check pipeline...")
    analysis = analyze_book(transcript, args.url, args.story_id, args.notes or "")
    path = TRANSCRIPTS_DIR / f"{args.story_id}_analysis.txt"
    path.write_text(analysis, encoding="utf-8")
    print(f"  Analysis saved: {path}")
    doc_title = f"{args.story_id} — {datetime.now().strftime('%Y-%m-%d')}"
    doc_url = create_drive_doc(doc_title, analysis, BOOK_FOLDER_ID)
    update_book_tracker(args.story_id, args.url, doc_url, analysis, args.notes or "")
    create_calendar_task(args.story_id, args.project, args.url, doc_url, transcript[:400], args.notes or "")
    print(f"\n{'='*50}\nBOOK CAPTURE DONE\nStory ID: {args.story_id}\nDoc: {doc_url or 'check artifacts'}\n{'='*50}")


def run_sovereign(args, transcript):
    print("\n[SOVEREIGN] Running format analysis...")
    analysis = analyze_sovereign(transcript, args.url, args.story_id, args.notes or "")
    path = TRANSCRIPTS_DIR / f"{args.story_id}_sovereign.txt"
    path.write_text(analysis, encoding="utf-8")
    doc_url = create_drive_doc(f"{args.story_id} — SOVEREIGN — {datetime.now().strftime('%Y-%m-%d')}", analysis, SOVEREIGN_FOLDER_ID)
    create_calendar_task(args.story_id, args.project, args.url, doc_url, transcript[:400], args.notes or "")
    print(f"\n{'='*50}\nSOVEREIGN CAPTURE DONE\nStory ID: {args.story_id}\nDoc: {doc_url or 'check artifacts'}\n{'='*50}")


def run_content(args, transcript):
    print("\n[CONTENT] Running classification...")
    cl = analyze_content(transcript, args.url, args.notes or "")
    sid = args.story_id or f"CNT-{datetime.now().strftime('%Y%m%d%H%M')}"
    update_inspiration_library(args.url, transcript, cl)
    create_calendar_task(sid, args.project, args.url, "", transcript[:400], args.notes or "")
    print(f"\n{'='*50}\nCONTENT CAPTURE DONE\nNiche: {cl.get('niche')}\nType: {cl.get('content_type')}\nStatus: {cl.get('classification')}\n{'='*50}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Capture Pipeline v2")
    parser.add_argument("url")
    parser.add_argument("--project", choices=["book", "sovereign", "content"], default="book")
    parser.add_argument("--story-id", default=None)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    if not args.story_id:
        prefix = {"book": "BCI", "sovereign": "SVG", "content": "CNT"}[args.project]
        args.story_id = f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M')}"

    print(f"\n{'='*50}\nCAPTURE PIPELINE v2\nURL: {args.url}\nProject: {args.project.upper()}\nStory ID: {args.story_id}\n{'='*50}")

    with tempfile.TemporaryDirectory() as tmp:
        audio = download_audio(args.url, tmp)
        transcript = transcribe_audio(audio)
        save_transcript(transcript, args.url, args.story_id, args.project)

    if args.project == "book":
        run_book(args, transcript)
    elif args.project == "sovereign":
        run_sovereign(args, transcript)
    else:
        run_content(args, transcript)


if __name__ == "__main__":
    main()
