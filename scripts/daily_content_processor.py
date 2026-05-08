#!/usr/bin/env python3
from __future__ import annotations

"""
daily_content_processor.py — Oak Park AI Hub
Runs every day via GitHub Actions (or manually).

WHAT IT DOES:
1. Reads Content Inspo sheet (📱 Content Inspo tab)
2. Finds rows where column B (Link) has a URL but column G (What we extracted) is empty/bad
3. For each pending link:
   a. Downloads via yt-dlp
   b. Runs BOTH Whisper (audio) AND Vision (text on screen) — always both
   c. Uses Claude to extract: title, summary, tips, quality, flow fit, suggested action
   d. Detects niche automatically
   e. Uploads transcript to Google Drive
   f. Updates the sheet row with proper data
4. After all extractions, checks master plan docs and logs suggestions

QUOTAS RESPECTED (GA4 Data API v1 limits):
- Max 1 video per 10 seconds to avoid rate limits
- Max 20 videos per run to stay within Whisper + Anthropic quotas
- Batch sheet updates (1 request per run end, not per row)

ENVIRONMENT (GitHub Secrets needed):
  SHEETS_TOKEN        - OAuth token JSON for Sheets + Drive
  OPENAI_API_KEY      - For Whisper transcription
  CLAUDE_KEY_4_CONTENT   - For Claude extraction + Vision
  CONTENT_SHEET_ID    - The Ideas & Inbox spreadsheet ID
  GOOGLE_SA_KEY       - Service account JSON for Drive uploads (optional)
"""

import os, sys, json, time, tempfile, subprocess, base64, re, urllib.request, urllib.parse
from datetime import date, datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID        = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
INSPO_TAB       = os.environ.get("CONTENT_INSPO_TAB", "📱 Content Inspo")
INSPO_FALLBACK_TABS = [INSPO_TAB, "📥 Inspiration Library"]
OPENAI_KEY      = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_KEY   = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
SHEETS_TOKEN_PATH = os.environ.get("SHEETS_TOKEN_PATH", "/tmp/oak_park_creds/sheets_token.json")
DRIVE_FOLDER_ID = "1Y2ymfzpE4mZOFrIwWrFQHEfFeYK5sDmG"  # Content - Reels & TikTok
MAX_PER_RUN     = 20  # don't over-process

# Master plan docs — suggest where content fits
MASTER_PLAN_DOCS = {
    "OAK_PARK_FINAL_EXECUTION_PLAN":      "1ra4fbwpoqbiJ-gBmaEyhSgPWqrBNc8VCy4yFcaSKmuw",
    "Oak Park IG Plan (12 Weeks)":        "1Xxzs1vLKpExuw4qmOZQua35fKm-TJ_Cfwv_3kDAGbBc",
    "Google Ads Optimization Plan":       "18R6rG1xgxyJk0abmlFK_l1-vLnEK_H3fOnCqC-aHi_s",
    "MKT PLAN SOCIAL MEDIA":             "19eEPnCB9DNAFugvH3vq0yf_e9HzHLSkUJei731jNw3s",
    "Blog Plan":                          "1yXcBHKgROfxsC2mxkYDwL7z4LLjHEtPcunnK_oKbxP8",
}

NICHE_KEYWORDS = {
    "AI Tips":               ["claude", "ai ", "chatgpt", "automation", "prompt", "llm", "openai", "skill", "mcp", "agent"],
    "Oak Park Construction": ["construction", "renovation", "patio", "concrete", "build", "floor", "coating", "epoxy", "deck", "roofing"],
    "Brazil News":           ["brazil", "brasil", "bolsonaro", "lula", "rio", "sao paulo"],
    "USA News":              ["trump", "congress", "democrat", "republican", "senate", "election"],
    "News Mix Countries":    ["disinformation", "fake news", "propaganda", "disinfo", "media bias"],
    "UGC":                   ["testimonial", "before and after", "review", "client", "transformation"],
}

VIDEO_DOMAINS = ["instagram.com", "tiktok.com", "youtube.com", "youtu.be", "reel", "vm.tiktok", "twitter.com", "x.com"]

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_token() -> str:
    td = json.loads(Path(SHEETS_TOKEN_PATH).read_text())
    data = urllib.parse.urlencode({
        "client_id": td["client_id"], "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"], "grant_type": "refresh_token"
    }).encode()
    return json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())["access_token"]

# ── Sheet helpers ──────────────────────────────────────────────────────────────

def sheet_get(token: str, tab: str, range_str: str) -> list:
    enc = urllib.parse.quote(f"'{tab}'!{range_str}", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    r = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(r).read()).get("values", [])

def sheet_update_row(token: str, tab: str, row_idx: int, values: list):
    """Update a specific row (1-indexed). row_idx=2 is first data row."""
    enc = urllib.parse.quote(f"'{tab}'!A{row_idx}:J{row_idx}", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}?valueInputOption=USER_ENTERED"
    body = json.dumps({"values": [values]}).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req)

def load_inspo_rows(token: str) -> tuple[str, list]:
    """Load the configured inspo tab, falling back to the canonical library tab."""
    seen = set()
    for tab in INSPO_FALLBACK_TABS:
        if not tab or tab in seen:
            continue
        seen.add(tab)
        try:
            rows = sheet_get(token, tab, "A1:AG100")
            if rows:
                if tab != INSPO_TAB:
                    print(f"  ℹ️  Using fallback tab: {tab}")
                return tab, rows
        except Exception as e:
            print(f"  ⚠️  Could not read tab {tab!r}: {e}")
    return INSPO_TAB, []

def normalize_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

def header_index(header: list, *names: str) -> int | None:
    norm = [normalize_header(h) for h in header]
    wanted = [normalize_header(n) for n in names]
    for name in wanted:
        if name in norm:
            return norm.index(name)
    for name in wanted:
        for idx, h in enumerate(norm):
            if name and name in h:
                return idx
    return None

def sheet_update_by_headers(token: str, tab: str, row_idx: int,
                            header: list, values_by_header: dict[str, str]):
    """Patch only named columns so fallback tabs cannot be corrupted by A:J writes."""
    hmap = {normalize_header(h): i for i, h in enumerate(header)}
    data = []
    for col_name, val in values_by_header.items():
        idx = hmap.get(normalize_header(col_name))
        if idx is None:
            continue
        col_letter = column_letter(idx + 1)
        data.append({
            "range": f"'{tab}'!{col_letter}{row_idx}",
            "values": [[val]],
        })
    if not data:
        return
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate"
    body = json.dumps({"valueInputOption": "USER_ENTERED", "data": data}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urllib.request.urlopen(req)

def column_letter(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out

def build_row_update(header: list, existing_row: list, info: dict,
                     drive_link: str, transcript_content: str) -> dict[str, str]:
    """Return header-keyed updates for either old Content Inspo or Inspiration Library."""
    extracted_summary = (
        f"{info.get('about', '')}\n\n"
        f"TIPS:\n{info.get('extracted', '')}\n\n"
        f"FITS: {info.get('flow_fit', '')} | ACTION: {info.get('suggested_action', '')}"
    ).strip()
    title = info.get("title", "")
    quality = str(info.get("quality", "?"))
    niche = info.get("niche", "")
    updates = {
        "Niche": niche,
        "Title": title,
        "Topic / Title": title,
        "What we extracted": extracted_summary,
        "Description": info.get("about", ""),
        "Transcription": (transcript_content[:45000] + f"\n\nTranscript Drive Link: {drive_link}").strip(),
        "Transcript Drive Link": drive_link,
        "AI Score (1-5)": quality,
        "Quality": quality,
        "Status": "Processed",
        "Date Status Changed": datetime.utcnow().strftime("%Y-%m-%d"),
    }
    return {k: v for k, v in updates.items() if header_index(header, k) is not None}

# ── Video processing ───────────────────────────────────────────────────────────

def is_video_url(url: str) -> bool:
    return any(d in url.lower() for d in VIDEO_DOMAINS)

def youtube_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else ""

def youtube_transcript_text(url: str) -> str:
    """Use YouTube captions when yt-dlp is blocked by runner bot checks."""
    video_id = youtube_video_id(url)
    if not video_id:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        for kwargs in (
            {"languages": ["en", "en-US", "en-GB", "pt", "pt-BR", "es"]},
            {},
        ):
            try:
                transcript = api.fetch(video_id, **kwargs)
                text = " ".join(t.text for t in transcript).strip()
                if text:
                    return text
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  YouTube transcript API unavailable: {e}")
    return ""

def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

def download_audio(url: str) -> str | None:
    ffmpeg = get_ffmpeg()
    tmp = tempfile.mktemp(suffix=".mp3")
    r = subprocess.run(
        ["yt-dlp", "--no-warnings", "--ffmpeg-location", ffmpeg,
         "-x", "--audio-format", "mp3", "-o", tmp, url],
        capture_output=True, text=True, timeout=120
    )
    if r.returncode == 0 and os.path.exists(tmp):
        return tmp
    print(f"  ⚠️  Download failed: {r.stderr[:200]}")
    return None

def download_video_frames(url: str) -> str | None:
    """Download video file for frame extraction."""
    ffmpeg = get_ffmpeg()
    tmp = tempfile.mktemp(suffix=".mp4")
    r = subprocess.run(
        ["yt-dlp", "--no-warnings", "--ffmpeg-location", ffmpeg,
         "-f", "worst[ext=mp4]/worst", "-o", tmp, url],
        capture_output=True, text=True, timeout=120
    )
    if r.returncode == 0 and os.path.exists(tmp):
        return tmp
    return None

def whisper_transcribe(audio_path: str) -> str:
    ffmpeg = get_ffmpeg()
    audio_16k = tempfile.mktemp(suffix=".mp3")
    subprocess.run([ffmpeg, "-i", audio_path, "-vn", "-ar", "16000", "-ac", "1",
                    "-b:a", "64k", audio_16k, "-y"], capture_output=True)
    with open(audio_16k, "rb") as f:
        audio_data = f.read()
    os.remove(audio_16k)
    boundary = "WB7x"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.mp3\"\r\n"
            f"Content-Type: audio/mpeg\r\n\r\n").encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {OPENAI_KEY}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = json.loads(urllib.request.urlopen(req).read())
    lines = []
    for seg in resp.get("segments", []):
        m, s = divmod(int(seg["start"]), 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg['text'].strip()}")
    return "\n".join(lines)

def vision_extract(video_path: str) -> str:
    """Extract frames and read text with Claude Vision."""
    ffmpeg = get_ffmpeg()
    frames_dir = tempfile.mkdtemp()
    subprocess.run([ffmpeg, "-i", video_path, "-vf", "select=not(mod(n\\,15))",
                    "-vsync", "vfr", "-frames:v", "8",
                    f"{frames_dir}/f_%03d.jpg", "-y"], capture_output=True)
    frames = []
    for f in sorted(Path(frames_dir).glob("f_*.jpg"))[:8]:
        frames.append(base64.b64encode(f.read_bytes()).decode())
        f.unlink()
    try:
        Path(frames_dir).rmdir()
    except Exception:
        pass
    if not frames:
        return ""
    content = [{"type": "text", "text": (
        "These are frames from a social media video/reel. "
        "Read ALL text visible on screen: overlays, captions, subtitles, on-screen text, tips, steps, numbers. "
        "List everything as plain text. If no text is visible, say: NO TEXT ON SCREEN."
    )}]
    for fb in frames:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": fb}})
    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 800,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["content"][0]["text"]

def claude_extract(whisper_text: str, vision_text: str, url: str) -> dict:
    combined = f"AUDIO TRANSCRIPT:\n{whisper_text}\n\nON-SCREEN TEXT:\n{vision_text}"
    prompt = f"""You are analyzing a social media video captured for content research.

URL: {url}

CONTENT:
{combined[:4000]}

Extract structured info for a content inspiration spreadsheet. Return ONLY valid JSON:
{{
  "title": "Short clear title — what this video teaches/shows (max 10 words)",
  "about": "1-2 sentence explanation of what this is and why it's useful for our content",
  "extracted": "3-5 bullet points of specific tips, quotes, frameworks, or ideas. If music only: Music reel — trending audio, no educational content",
  "quality": 3,
  "niche": "AI Tips OR Oak Park Construction OR Brazil News OR USA News OR News Mix Countries OR UGC",
  "flow_fit": "Which plan: Content Creation, Google Ads, Blog, Social Media Strategy, AI Tips, Oak Park Construction, or None",
  "suggested_action": "One specific next action"
}}

Quality: 1=music/junk, 2=low value, 3=decent tips, 4=strong framework, 5=must-implement"""

    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 700,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req).read())
    text = resp["content"][0]["text"].strip()
    try:
        clean = text
        if "```" in clean:
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        return json.loads(clean.strip())
    except Exception:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"title": "Review manually", "about": "Extraction failed", "extracted": text[:300],
            "quality": 1, "niche": "AI Tips", "flow_fit": "None", "suggested_action": "Review manually"}

def upload_to_drive(content: str, filename: str, token: str) -> str:
    """Upload transcript text directly to Drive."""
    try:
        boundary = "DriveB7x"
        metadata = json.dumps({"name": filename, "parents": [DRIVE_FOLDER_ID],
                               "mimeType": "text/plain"}).encode()
        body_bytes = (f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode()
                      + metadata
                      + f"\r\n--{boundary}\r\nContent-Type: text/plain\r\n\r\n".encode()
                      + content.encode()
                      + f"\r\n--{boundary}--".encode())
        req = urllib.request.Request(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink",
            data=body_bytes,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/related; boundary={boundary}"})
        result = json.loads(urllib.request.urlopen(req).read())
        return result.get("webViewLink", "")
    except Exception as e:
        print(f"  ⚠️  Drive upload failed: {e}")
        return ""

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🚀 Daily Content Processor — {date.today()}")
    token = get_token()

    inspo_tab, rows = load_inspo_rows(token)
    if not rows:
        print("Sheet empty.")
        return

    header = rows[0]
    print(f"📊 Columns: {header}")

    # Find column indices
    col_link      = header_index(header, "URL", "Link")
    col_niche     = header_index(header, "Niche")
    col_title     = header_index(header, "Topic / Title", "Title")
    col_extracted = header_index(header, "Transcription", "What we extracted", "Extracted")
    col_status    = header_index(header, "Status")

    if col_link is None:
        print("❌ Can't find Link column. Check headers.")
        return

    pending = []
    for i, row in enumerate(rows[1:], start=2):  # 1-indexed sheet rows
        def v(col):
            return row[col].strip() if col is not None and len(row) > col else ""

        link = v(col_link)
        extracted = v(col_extracted)
        status = v(col_status)

        # Needs processing: has a video link, but extracted is empty/bad/pending
        needs_work = (
            link and is_video_url(link) and (
                not extracted
                or "Needs processing" in extracted
                or "Pending" in status
                or extracted.strip() in ["", "?"]
                or "see /Users" in extracted  # old local path references
            )
        )
        if needs_work:
            pending.append((i, row, link))

    print(f"🔍 Found {len(pending)} rows to process (max {MAX_PER_RUN} this run)")
    pending = pending[:MAX_PER_RUN]

    updates = []
    for sheet_row, row, link in pending:
        print(f"\n▶ Row {sheet_row}: {link[:60]}")

        def v(col):
            return row[col].strip() if col is not None and len(row) > col else ""

        # Download / transcript cascade. YouTube often blocks yt-dlp on GitHub
        # runners; captions are a safe text-first fallback when available.
        transcript_api_text = youtube_transcript_text(link)
        if transcript_api_text:
            print("  📝 YouTube transcript API text found")
        audio_path  = None if transcript_api_text else download_audio(link)
        video_path  = download_video_frames(link)

        whisper_text = transcript_api_text
        vision_text  = ""

        # ALWAYS run both Whisper and Vision
        if audio_path:
            try:
                print("  🎙️  Whisper transcribing...")
                whisper_text = whisper_transcribe(audio_path)
                os.remove(audio_path)
            except Exception as e:
                print(f"  ⚠️  Whisper failed: {e}")

        if video_path:
            try:
                print("  👁️  Vision extracting text on screen...")
                vision_text = vision_extract(video_path)
                os.remove(video_path)
            except Exception as e:
                print(f"  ⚠️  Vision failed: {e}")

        if not whisper_text and not vision_text:
            print("  ❌ Both paths failed — skipping")
            continue

        # Claude extraction
        print("  🧠 Claude extracting...")
        try:
            info = claude_extract(whisper_text, vision_text, link)
        except Exception as e:
            print(f"  ⚠️  Claude extraction failed: {e}")
            fallback_text = (whisper_text or vision_text or "").strip()
            info = {
                "title": "Review manually",
                "about": "Automated extraction failed; transcript was saved for manual review.",
                "extracted": fallback_text[:1000],
                "quality": 1,
                "niche": v(col_niche) or "AI Tips",
                "flow_fit": "None",
                "suggested_action": "Review transcript manually",
            }

        # Build transcript text
        slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        transcript_content = (
            f"Source: {link}\nDate: {date.today()}\nNiche: {info.get('niche')}\n"
            f"Title: {info.get('title')}\nQuality: {info.get('quality')}/5\n"
            f"Flow fit: {info.get('flow_fit')}\n\n"
            f"SUMMARY:\n{info.get('about')}\n\n"
            f"EXTRACTED TIPS:\n{info.get('extracted')}\n\n"
            f"SUGGESTED ACTION: {info.get('suggested_action')}\n\n"
            f"--- WHISPER TRANSCRIPT ---\n{whisper_text}\n\n"
            f"--- VISION TEXT ---\n{vision_text}"
        )

        # Upload to Drive
        drive_link = upload_to_drive(transcript_content, f"transcript_{slug}.txt", token)

        updated_row = build_row_update(header, row, info, drive_link, transcript_content)
        updates.append((sheet_row, updated_row))
        print(f"  ✅ '{info.get('title')}' | Niche: {info.get('niche')} | Q:{info.get('quality')}/5")

        time.sleep(3)  # rate limit — don't hammer APIs

    # Write all updates to sheet
    for sheet_row, values in updates:
        try:
            sheet_update_by_headers(token, inspo_tab, sheet_row, header, values)
            print(f"  📊 Updated row {sheet_row}")
        except Exception as e:
            print(f"  ⚠️  Sheet update row {sheet_row} failed: {e}")

    print(f"\n✅ Done. Processed {len(updates)}/{len(pending)} rows.")

if __name__ == "__main__":
    main()
