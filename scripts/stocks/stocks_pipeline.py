#!/usr/bin/env python3
"""
Stocks Intelligence Pipeline
Downloads, transcribes, and analyzes financial content for actionable stock insights.

Usage:
  python scripts/stocks/stocks_pipeline.py <url> [--notes "context"]

Outputs:
  stocks/original/   — raw video files
  stocks/transcription/ — .txt transcripts
  Google Sheet (Stocks Intelligence Catalog) — full catalog row
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
import anthropic

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
SPREADSHEET_ID = os.getenv("STOCKS_SHEET_ID", "1plLMR0GQUdFBagKiUT78CMxcncbUc435jt4uluKxhbI")
ORIGINAL_DIR   = Path("stocks/original")
TRANSCRIPT_DIR = Path("stocks/transcription")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SHEET_TAB = "Catalog"
HEADERS = [
    "ID", "Date", "Source URL", "Platform",
    "Original File", "Transcript File", "Transcript",
    "Topic Keywords", "Stocks Mentioned", "Industries",
    "Countries/Markets", "Key Claims",
    "Verification Status", "Verified Facts",
    "Action Insights", "Rewritten Content",
    "Sources", "Status", "Notes"
]


# ──────────────────────────────────────────────────────────────────────────────
# GOOGLE SHEETS
# ──────────────────────────────────────────────────────────────────────────────
def get_sheet():
    sa_json = os.environ["GOOGLE_SA_KEY"]
    info = json.loads(sa_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    wb = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = wb.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet(title=SHEET_TAB, rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS)
    # Ensure headers exist on row 1
    row1 = ws.row_values(1)
    if not row1 or row1[0] != "ID":
        ws.insert_row(HEADERS, 1)
    return ws


def next_id(ws):
    values = ws.col_values(1)
    nums = [int(v.replace("STKS-", "")) for v in values if re.match(r"STKS-\d+", v)]
    n = max(nums) + 1 if nums else 1
    return f"STKS-{n:03d}"


# ──────────────────────────────────────────────────────────────────────────────
# DOWNLOAD
# ──────────────────────────────────────────────────────────────────────────────
def download_video(url, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", url)[:60]
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outtemplate = str(dest_dir / f"{ts}_{slug}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-o", outtemplate,
        "--merge-output-format", "mp4",
        "--no-warnings",
        url,
    ]
    print(f"[download] {url}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr}")
    matches = sorted(dest_dir.glob(f"{ts}_{slug}*"))
    if not matches:
        raise FileNotFoundError("Downloaded file not found after yt-dlp run")
    return matches[-1]


# ──────────────────────────────────────────────────────────────────────────────
# TRANSCRIBE
# ──────────────────────────────────────────────────────────────────────────────
def transcribe(video_path, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    txt_path = dest_dir / (video_path.stem + ".txt")
    print(f"[transcribe] {video_path.name}")
    with open(video_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text"
        )
    txt_path.write_text(result, encoding="utf-8")
    return txt_path, result


# ──────────────────────────────────────────────────────────────────────────────
# ANALYZE (Claude)
# ──────────────────────────────────────────────────────────────────────────────
ANALYSIS_PROMPT = """
You are a senior financial analyst. Analyze this transcript from a financial/stocks video.
Extract SPECIFIC, non-generic intelligence that could guide real research decisions.

TRANSCRIPT:
{transcript}

EXTRA CONTEXT:
{notes}

Return a JSON object with EXACTLY these keys:
{{
  "topic_keywords": ["3-6 specific keywords from the content"],
  "stocks_mentioned": ["specific tickers or company names; empty list if none"],
  "industries": ["sector/industry names discussed"],
  "countries_markets": ["countries or exchanges mentioned, e.g. Brazil/B3, US/NYSE"],
  "key_claims": [
    "Specific factual claim 1 made in the video",
    "Specific factual claim 2..."
  ],
  "action_insights": "Concrete, specific insight: what to investigate (e.g. 'VALE3 on B3 may benefit from China iron ore demand per claim in video — verify via Reuters and B3 filings'). NOT generic.",
  "language_detected": "English|Portuguese|Spanish|Other",
  "platform_guess": "Instagram|TikTok|YouTube|Twitter|Other"
}}

Rules:
- Be SPECIFIC, not generic
- If no stocks are named, say so explicitly with []
- action_insights must name specific stocks/industries/countries if available
- Return ONLY valid JSON, no markdown fences
"""


def analyze_transcript(transcript, notes=""):
    client = anthropic.Anthropic()
    prompt = ANALYSIS_PROMPT.format(
        transcript=transcript,
        notes=notes or "(none)"
    )
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```[json]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


# ──────────────────────────────────────────────────────────────────────────────
# REWRITE (Claude)
# ──────────────────────────────────────────────────────────────────────────────
REWRITE_PROMPT = """
You are a financial content creator. Create an ORIGINAL, copyright-safe educational post
based on the transcript and analysis below. DO NOT copy any phrases from the transcript.

ANALYSIS:
{analysis}

TRANSCRIPT SUMMARY (reference only, do not copy):
{transcript_preview}

EXTRA CONTEXT: {notes}

Write a post that:
1. Is 100% original — paraphrase every idea in your own words
2. Names specific stocks/industries/countries if factually defensible
3. Adds educational context (e.g. what this metric means, why this sector matters)
4. Is formatted for Instagram/LinkedIn caption (250-350 words max)
5. Ends with:
   "\n\n📌 Sources to verify:\n"
   Then 2-3 specific sources to check (e.g. Reuters, Bloomberg, Yahoo Finance, B3.com.br, SEC.gov, Valor Econômico)
6. Final line: "⚠️ Not financial advice. Always do your own research."

Return ONLY the post text, ready to use.
"""


def rewrite_content(transcript, analysis, notes=""):
    client = anthropic.Anthropic()
    prompt = REWRITE_PROMPT.format(
        analysis=json.dumps(analysis, indent=2, ensure_ascii=False),
        transcript_preview=transcript[:800],
        notes=notes or "(none)"
    )
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Stocks Intelligence Pipeline")
    parser.add_argument("url", help="Reel URL (Instagram, TikTok, YouTube, Twitter/X)")
    parser.add_argument("--notes", default="", help="Extra context or research focus")
    args = parser.parse_args()

    bar = "=" * 60
    print(f"\n{bar}")
    print(f"STOCKS INTELLIGENCE PIPELINE")
    print(f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"URL:   {args.url}")
    print(f"Notes: {args.notes or '(none)'}")
    print(f"{bar}\n")

    # ── Step 1: Download ──────────────────────────────────────────────────────
    video_path = download_video(args.url, ORIGINAL_DIR)
    print(f"[✓] Downloaded → {video_path}")

    # ── Step 2: Transcribe ───────────────────────────────────────────────────
    txt_path, transcript = transcribe(video_path, TRANSCRIPT_DIR)
    print(f"[✓] Transcribed → {txt_path}")
    print(f"\n--- TRANSCRIPT PREVIEW ---\n{transcript[:400]}...\n")

    # ── Step 3: Analyze ──────────────────────────────────────────────────────
    print("[analyze] Running Claude analysis...")
    analysis = analyze_transcript(transcript, args.notes)
    print(f"[✓] Analysis complete:")
    print(json.dumps(analysis, indent=2, ensure_ascii=False))

    # ── Step 4: Rewrite ──────────────────────────────────────────────────────
    print("\n[rewrite] Generating copyright-safe content...")
    rewritten = rewrite_content(transcript, analysis, args.notes)
    print(f"[✓] Rewritten content ready.\n")
    print("--- REWRITTEN CONTENT ---")
    print(rewritten)
    print("---\n")

    # ── Step 5: Log to Google Sheets ─────────────────────────────────────────
    print("[sheets] Logging to Google Sheets...")
    ws = get_sheet()
    row_id = next_id(ws)

    # Detect platform from URL
    platform = "Unknown"
    if "instagram.com" in args.url:           platform = "Instagram"
    elif "tiktok.com" in args.url:             platform = "TikTok"
    elif "youtube.com" in args.url or "youtu.be" in args.url: platform = "YouTube"
    elif "twitter.com" in args.url or "x.com" in args.url:  platform = "Twitter/X"

    # Extract sources from rewritten content if present
    sources = ""
    if "Sources to verify" in rewritten:
        sources_block = rewritten.split("Sources to verify")[-1]
        sources = sources_block.strip().lstrip(":\n").split("\u26a0")[0].strip()

    row = [
        row_id,
        datetime.utcnow().strftime("%Y-%m-%d"),
        args.url,
        platform,
        str(video_path),
        str(txt_path),
        transcript,
        ", ".join(analysis.get("topic_keywords", [])),
        ", ".join(analysis.get("stocks_mentioned", [])),
        ", ".join(analysis.get("industries", [])),
        ", ".join(analysis.get("countries_markets", [])),
        "\n".join(analysis.get("key_claims", [])),
        "Pending",
        "",
        analysis.get("action_insights", ""),
        rewritten,
        sources,
        "New",
        args.notes,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"[✓] Logged as {row_id} in Stocks Intelligence Catalog.")

    print(f"\n{'='*60}")
    print(f"✅ PIPELINE COMPLETE — {row_id}")
    print(f"   Video:      {video_path}")
    print(f"   Transcript: {txt_path}")
    print(f"   Insights:   {analysis.get('action_insights', 'n/a')[:120]}")
    print(f"   Sheet:      https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
