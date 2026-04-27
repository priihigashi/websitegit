#!/usr/bin/env python3
"""
youtube_research.py — General-purpose YouTube/Shorts/Reels research agent

Searches for recent videos on any topic, pulls transcripts (no download needed),
Claude analyzes each for technique, tools, quality, key takeaways.

Flow: search 5 → analyze → expand keywords → search 5 more → expand → search 5 more = 15 total
Saves to:
  - Drive: Resources/Video Creation Flow/<topic>/ → raw transcripts + master findings doc
  - Sheet: Ideas & Inbox → 📥 Inspiration Library tab (one row per video)

Usage (local):
  python youtube_research.py --topic "kling ai talking head" --queries "kling ai tutorial 2025,kling 3.0 video" --max 5

GitHub Action: trigger via video-research.yml with workflow_dispatch
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime

try:
    import anthropic
except ImportError:
    os.system("pip install anthropic -q")
    import anthropic

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    os.system("pip install youtube-transcript-api -q")
    from youtube_transcript_api import YouTubeTranscriptApi

try:
    import yt_dlp
except ImportError:
    os.system("pip install yt-dlp -q")
    import yt_dlp

try:
    import gspread
    from google.oauth2 import service_account
except ImportError:
    os.system("pip install gspread google-auth -q")
    import gspread
    from google.oauth2 import service_account

import urllib.request
import urllib.parse

# ── CONFIG ────────────────────────────────────────────────────────────────────
CLAUDE_KEY_4_CONTENT = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
GOOGLE_SA_KEY     = os.environ.get("GOOGLE_SA_KEY", "")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
SHEET_ID          = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
DRIVE_FOLDER_ID   = "1-QRf4xToJf_7cnS5UW7BiDUjd6lXot6o"  # Resources/Video Creation Flow
INSP_TAB          = "📥 Inspiration Library"
TARGET_VIDEOS     = 15
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Failure accumulator — populated by log_pipeline_failure(). Non-empty => script exits 1.
PIPELINE_FAILURES = []
GHA_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

# ── YOUTUBE SEARCH ────────────────────────────────────────────────────────────
def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """Use yt-dlp to search YouTube, return list of {url, title, id, duration}"""
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "default_search": f"ytsearch{max_results}",
        "match_filter": yt_dlp.utils.match_filter_func("duration < 900"),  # max 15min
    }
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            for entry in info.get("entries", []):
                if entry:
                    results.append({
                        "id": entry.get("id", ""),
                        "title": entry.get("title", ""),
                        "url": f"https://youtube.com/watch?v={entry.get('id','')}",
                        "duration": entry.get("duration", 0),
                        "uploader": entry.get("uploader", ""),
                        "upload_date": entry.get("upload_date", ""),
                    })
        except Exception as e:
            print(f"  Search error for '{query}': {e}")
    return results

# ── TRANSCRIPT ────────────────────────────────────────────────────────────────
def get_transcript(video_id: str) -> str:
    """Pull transcript via youtube-transcript-api (no download)"""
    last_error = None
    for attempt, kwargs in enumerate([
        {"languages": ["en", "en-US", "en-GB", "pt", "es"]},  # preferred languages
        {},  # any available (includes auto-generated)
    ]):
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id, **kwargs)
            return " ".join(t.text for t in transcript)
        except Exception as e:
            last_error = e
    return f"[transcript unavailable: {last_error}]"

# ── CLAUDE ANALYSIS ───────────────────────────────────────────────────────────
def analyze_with_claude(video: dict, transcript: str, research_context: str) -> dict:
    """Claude analyzes a video — uses transcript if available, falls back to metadata only"""
    if not CLAUDE_KEY_4_CONTENT:
        return {"summary": "No API key", "watch_priority": "low", "relevance_score": 0}
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    
    has_transcript = transcript and "[transcript unavailable" not in transcript
    
    if has_transcript:
        content_block = f"TRANSCRIPT:\n{transcript[:4000]}"
        mode_note = "You have the full transcript to analyze."
    else:
        content_block = f"NOTE: Transcript unavailable. Analyze based on title, channel, and date only."
        mode_note = "No transcript — use title and channel to infer what this video likely covers."
    
    prompt = f"""You are analyzing a YouTube video for research on: {research_context}

Video: "{video['title']}" by {video.get('uploader', 'unknown')}
Published: {video.get('upload_date', 'unknown')}
URL: {video['url']}

{content_block}

{mode_note}

Extract and return JSON with:
{{
  "summary": "2-3 sentence summary of what this video shows/teaches (infer from title if no transcript)",
  "tools_used": ["list of AI tools, software, platforms mentioned or likely mentioned"],
  "technique": "specific technique or workflow demonstrated",
  "quality_assessment": "honest assessment — note if this is inferred from title only",
  "key_tips": ["up to 3 likely actionable tips based on title/topic"],
  "use_case": "what this is best for",
  "relevant_to_us": true/false,
  "relevance_reason": "why or why not relevant to Oak Park Construction / Hig Negocios",
  "watch_priority": "high / medium / low",
  "relevance_score": 5,
  "has_transcript": {str(has_transcript).lower()}
}}

relevance_score is 1-10. If no transcript, cap at 6 (needs manual verification).
Return only valid JSON, no markdown."""

    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return json.loads(msg.content[0].text)
    except Exception as e:
        return {"summary": f"Analysis failed: {e}", "watch_priority": "low", "relevance_score": 0, "has_transcript": has_transcript}

# ── KEYWORD EXPANSION ─────────────────────────────────────────────────────────
def expand_keywords(topic: str, results_so_far: list, round_num: int) -> list[str]:
    """Ask Claude to generate 5 new search queries based on videos analyzed so far"""
    if not CLAUDE_KEY_4_CONTENT:
        return []
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)
    
    summaries = []
    for r in results_so_far[-10:]:
        score = r["analysis"].get("relevance_score", 0)
        summaries.append(f"- [{score}/10] {r['title']}: {r['analysis'].get('summary', '')[:150]}")
    
    prompt = f"""You are a YouTube research assistant expanding research on: {topic}
Round: {round_num} of 3. Target: 15 total videos.

Videos analyzed so far:
{chr(10).join(summaries)}

Generate 5 new YouTube search queries to find MORE useful videos.
- If high-scoring videos exist, go deeper on that specific angle
- If all scores are low, pivot to a different angle of the same topic
- Avoid queries that would return the same videos already found
- Each query should target a specific subtopic or technique

Return ONLY a JSON array of 5 query strings, nothing else:
["query 1", "query 2", "query 3", "query 4", "query 5"]"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if Haiku wrapped the JSON
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if not raw:
            raise ValueError(f"Haiku returned empty body (model=claude-haiku-4-5-20251001, round={round_num})")
        print(f"  [round {round_num}] raw response (first 200 chars): {raw[:200]}")
        return json.loads(raw)
    except Exception as e:
        print(f"  Keyword expansion failed: {e}")
        log_pipeline_failure(f"Round {round_num} keyword expansion", e)
        return []

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def get_sheet():
    if not GOOGLE_SA_KEY:
        return None
    try:
        creds_dict = json.loads(GOOGLE_SA_KEY)
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        gc = gspread.authorize(creds)
        return gc.open_by_key(SHEET_ID)
    except Exception as e:
        print(f"  Sheet error: {e}")
        return None

def save_to_sheet(sheet, video: dict, analysis: dict, topic: str):
    try:
        ws = sheet.worksheet(INSP_TAB)
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            f"[VIDEO RESEARCH] {topic}",
            video["title"],
            video["url"],
            video.get("uploader", ""),
            analysis.get("summary", ""),
            ", ".join(analysis.get("tools_used", [])),
            analysis.get("technique", ""),
            analysis.get("quality_assessment", ""),
            analysis.get("watch_priority", ""),
            analysis.get("relevance_reason", ""),
        ]
        ws.append_row(row)
        print(f"  Saved to sheet: {video['title'][:50]}")
    except Exception as e:
        print(f"  Sheet save error: {e}")

# ── DRIVE UPLOAD ──────────────────────────────────────────────────────────────
def upload_to_drive(content: str, filename: str, folder_id: str, token: str):
    """Upload a text file to Drive using OAuth token"""
    if not token:
        print(f"  No Drive token — skipping upload of {filename}")
        return None
    
    boundary = "boundary_xyz_123"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps({'name': filename, 'parents': [folder_id]})}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/plain\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode()

    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            print(f"  Uploaded to Drive: {filename}")
            return result.get("id")
    except Exception as e:
        print(f"  Drive upload failed: {e}")
        log_pipeline_failure("Drive upload", e)
        return None

# ── FAILURE LOGGING ───────────────────────────────────────────────────────────
def log_pipeline_failure(stage: str, error: str, sheet=None):
    """Record a silent-failure event. Appends to '🚨 Pipeline Failures' tab and
    accumulates in PIPELINE_FAILURES so __main__ can exit non-zero."""
    PIPELINE_FAILURES.append({"stage": stage, "error": str(error)[:500]})
    print(f"  ❌ FAILURE [{stage}]: {str(error)[:200]}")
    if sheet is None:
        return
    try:
        ws = sheet.worksheet("🚨 Pipeline Failures")
    except Exception:
        return  # tab missing — failures still tracked in PIPELINE_FAILURES
    try:
        run_url = (
            f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{GHA_RUN_ID}"
            if GHA_RUN_ID else ""
        )
        ws.append_row([
            datetime.utcnow().isoformat() + "Z",
            "video-research.yml",
            GHA_RUN_ID,
            stage,
            str(error)[:500],
            run_url,
            "",  # RESOLVED checkbox — leave empty per checkbox rule
            "",  # NOTE
        ], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"  (failure-log write itself failed: {e})")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def run(topic: str, queries: list[str], max_per_query: int = 5):
    print(f"\n=== VIDEO RESEARCH: {topic} ===")
    print(f"Initial queries: {queries}")
    print(f"Target: {TARGET_VIDEOS} transcribed videos across 3 rounds\n")

    sheet = get_sheet()
    drive_token = os.environ.get("DRIVE_OAUTH_TOKEN", "")
    
    all_results = []
    seen_ids = set()

    def process_batch(batch_queries: list[str], round_num: int) -> int:
        """Search + analyze a batch of queries. Returns count of new videos added."""
        new_count = 0
        for query in batch_queries:
            if len(all_results) >= TARGET_VIDEOS:
                break
            print(f"\n  [Round {round_num}] Searching: {query}")
            videos = search_youtube(query, max_per_query)
            
            for video in videos:
                if len(all_results) >= TARGET_VIDEOS:
                    break
                if video["id"] in seen_ids:
                    continue
                seen_ids.add(video["id"])
                
                print(f"  [{video['id']}] {video['title'][:60]}")
                transcript = get_transcript(video["id"])
                
                has_transcript = "[transcript unavailable" not in transcript
                mode = "with transcript" if has_transcript else "metadata only"
                print(f"    Analyzing ({mode})...")
                analysis = analyze_with_claude(video, transcript, topic)
                
                result = {**video, "analysis": analysis, "transcript_excerpt": transcript[:500]}
                all_results.append(result)
                new_count += 1
                
                if sheet:
                    save_to_sheet(sheet, video, analysis, topic)
        return new_count

    # Round 1 — initial queries
    print(f"\n{'='*40}")
    print(f"ROUND 1 — Initial search ({len(queries)} queries)")
    print(f"{'='*40}")
    process_batch(queries, 1)
    print(f"\nRound 1 done: {len(all_results)}/{TARGET_VIDEOS} videos")

    # Round 2 — expand keywords based on round 1 findings
    if len(all_results) < TARGET_VIDEOS and all_results:
        print(f"\n{'='*40}")
        print(f"ROUND 2 — Expanding keywords from Round 1 findings")
        print(f"{'='*40}")
        expanded = expand_keywords(topic, all_results, 2)
        if expanded:
            print(f"New queries: {expanded}")
            process_batch(expanded, 2)
            print(f"\nRound 2 done: {len(all_results)}/{TARGET_VIDEOS} videos")
        else:
            print(f"  Keyword expansion skipped (no API key or failed)")

    # Round 3 — expand again
    if len(all_results) < TARGET_VIDEOS and all_results:
        print(f"\n{'='*40}")
        print(f"ROUND 3 — Second expansion")
        print(f"{'='*40}")
        expanded2 = expand_keywords(topic, all_results, 3)
        if expanded2:
            print(f"New queries: {expanded2}")
            process_batch(expanded2, 3)
            print(f"\nRound 3 done: {len(all_results)}/{TARGET_VIDEOS} videos")
        else:
            print(f"  Keyword expansion skipped (no API key or failed)")

    # Build master findings report
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    high = [r for r in all_results if r["analysis"].get("watch_priority") == "high"]
    implementable = sorted(
        [r for r in all_results if r["analysis"].get("relevance_score", 0) >= 7],
        key=lambda r: r["analysis"].get("relevance_score", 0),
        reverse=True
    )
    
    doc_lines = [
        f"RESEARCH REPORT: {topic}",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Videos analyzed: {len(all_results)} (target: {TARGET_VIDEOS})",
        f"High priority: {len(high)} | Immediately implementable (score 7+): {len(implementable)}",
        "",
        "=" * 60,
        "",
    ]

    if implementable:
        doc_lines.append("BEST IDEAS TO IMPLEMENT NOW")
        doc_lines.append("-" * 40)
        for i, r in enumerate(implementable[:5], 1):
            score = r["analysis"].get("relevance_score", 0)
            doc_lines.append(f"{i}. [{score}/10] {r['title']}")
            doc_lines.append(f"   URL: {r['url']}")
            doc_lines.append(f"   {r['analysis'].get('summary', '')}")
            tips = r["analysis"].get("key_tips", [])
            if tips:
                doc_lines.append("   Key tips:")
                for tip in tips[:3]:
                    doc_lines.append(f"     - {tip}")
            doc_lines.append(f"   Why: {r['analysis'].get('relevance_reason', '')}")
            doc_lines.append("")

    doc_lines.append("=" * 60)
    doc_lines.append("ALL VIDEOS ANALYZED")
    doc_lines.append("-" * 40)
    for r in all_results:
        score = r["analysis"].get("relevance_score", 0)
        priority = r["analysis"].get("watch_priority", "?")
        doc_lines.append(f"[{score}/10 | {priority}] {r['title']}")
        doc_lines.append(f"  URL: {r['url']}")
        doc_lines.append(f"  {r['analysis'].get('summary', '')}")
        tools = r["analysis"].get("tools_used", [])
        if tools:
            doc_lines.append(f"  Tools: {', '.join(tools)}")
        doc_lines.append("")

    doc_content = "\n".join(doc_lines)
    filename = f"research_{topic.replace(' ','_')}_{timestamp}.txt"
    
    print(f"\nSaving report: {filename}")
    if drive_token:
        upload_to_drive(doc_content, filename, DRIVE_FOLDER_ID, drive_token)
    else:
        with open(f"/tmp/{filename}", "w") as f:
            f.write(doc_content)
        print(f"  Saved locally to /tmp/{filename} (no Drive token)")
    
    # Bug 3 ceiling check: if 0 videos got transcripts, log it (YouTube IP block)
    transcripts_ok = sum(1 for r in all_results if r["analysis"].get("has_transcript"))
    if all_results and transcripts_ok == 0:
        log_pipeline_failure(
            "Transcription (all videos metadata-only)",
            f"0/{len(all_results)} videos returned a transcript — YouTube likely blocking GHA IP. "
            "Permanent unless we route via residential proxy or Whisper-on-audio.",
            sheet,
        )

    # Flush any failures recorded BEFORE sheet was available
    if sheet and PIPELINE_FAILURES:
        for f in PIPELINE_FAILURES:
            try:
                ws = sheet.worksheet("🚨 Pipeline Failures")
                run_url = (
                    f"https://github.com/priihigashi/oak-park-ai-hub/actions/runs/{GHA_RUN_ID}"
                    if GHA_RUN_ID else ""
                )
                ws.append_row([
                    datetime.utcnow().isoformat() + "Z",
                    "video-research.yml",
                    GHA_RUN_ID,
                    f["stage"],
                    f["error"],
                    run_url,
                    "",
                    "(flushed at run end)",
                ], value_input_option="USER_ENTERED")
            except Exception:
                pass

    print(f"\n{'='*60}")
    print(f"DONE: {len(all_results)} videos analyzed")
    print(f"Implementable (7+): {len(implementable)}")
    print(f"High priority: {len(high)}")
    if PIPELINE_FAILURES:
        print(f"❌ {len(PIPELINE_FAILURES)} silent failure(s) — see '🚨 Pipeline Failures' tab in Ideas & Inbox")
        for f in PIPELINE_FAILURES:
            print(f"   - {f['stage']}: {f['error'][:120]}")
    if implementable:
        top = implementable[0]
        print(f"Top pick: {top['title']}")
        print(f"  {top['url']}")
    print(f"{'='*60}")
    return all_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True, help="Research topic label (e.g. 'kling ai talking head')")
    parser.add_argument("--queries", required=True, help="Comma-separated search queries")
    parser.add_argument("--max", type=int, default=5, help="Max results per query")
    args = parser.parse_args()
    
    queries = [q.strip() for q in args.queries.split(",")]
    run(args.topic, queries, args.max)

    # Fail loud: any silent failure → non-zero exit so GitHub marks run ❌
    if PIPELINE_FAILURES:
        sys.exit(1)
