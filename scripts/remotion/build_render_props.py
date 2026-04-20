#!/usr/bin/env python3
"""
build_render_props.py — Generate Remotion render props JSON from SRT + proof slides.
Outputs JSON to stdout (captured by render-video.yml).

Usage:
  python build_render_props.py \\
    --story-id SVG-202604171927 \\
    --language en \\
    --proof-slides '[...]' \\
    --srt-file /tmp/captions.srt \\
    [--translate]  # when language=pt, translates EN captions via Claude Haiku
"""

import argparse
import json
import os
import re
import sys


FPS = 30


def srt_time_to_frames(ts: str) -> int:
    """Convert SRT timestamp HH:MM:SS,mmm to frame number at 30fps."""
    hms, ms = ts.split(",")
    h, m, s = hms.split(":")
    total_sec = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return round(total_sec * FPS)


def parse_srt(srt_path: str) -> list:
    if not srt_path or not os.path.exists(srt_path):
        return []
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    blocks = re.split(r"\n\n+", content.strip())
    captions = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})", lines[1]
        )
        if not match:
            continue
        captions.append({
            "startFrame": srt_time_to_frames(match.group(1)),
            "endFrame":   srt_time_to_frames(match.group(2)),
            "text":       " ".join(lines[2:]).strip(),
        })
    return captions


def translate_captions(captions: list, target_lang: str = "pt") -> list:
    """Translate caption text to target language via Claude Haiku. Non-fatal if fails."""
    api_key = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    if not api_key or not captions:
        return captions
    try:
        import urllib.request
        texts = [c["text"] for c in captions]
        prompt = (
            f"Translate these subtitle lines to Brazilian Portuguese. "
            f"Keep each line SHORT (subtitle length). Output ONLY a JSON array of strings, same order.\n\n"
            + json.dumps(texts)
        )
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        translated = json.loads(resp["content"][0]["text"])
        if len(translated) == len(captions):
            return [
                {**c, "text": t} for c, t in zip(captions, translated)
            ]
    except Exception as e:
        print(f"WARNING: caption translation failed: {e}", file=sys.stderr)
    return captions


def generate_voiceover(text, lang, story_id):
    """Generate MP3 voiceover via ElevenLabs TTS. Returns local path or None on failure."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key or not text.strip():
        return None
    # Rachel — neutral, clear, documentary style (EN)
    # Adam — works well in Portuguese (PT)
    voice_id = "21m00Tcm4TlvDq8ikWAM" if lang == "en" else "pNInz6obpgDQGcFmaJgB"
    try:
        import requests as _requests
        r = _requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            timeout=60,
        )
        r.raise_for_status()
        path = f"/tmp/{story_id}_vo_{lang}.mp3"
        with open(path, "wb") as f:
            f.write(r.content)
        print(f"Voiceover ({lang}): {len(r.content) / 1024:.1f} KB → {path}", file=sys.stderr)
        return path
    except Exception as e:
        print(f"WARNING: ElevenLabs voiceover ({lang}) failed: {e}", file=sys.stderr)
        return None


def upload_audio_to_drive(local_path, story_id, lang):
    """Upload MP3 to HISTORY_TEMPLATE_FOLDER (or legacy SOVEREIGN_TEMPLATE_FOLDER) for archiving. Non-fatal."""
    token_env = os.environ.get("SHEETS_TOKEN", "")
    folder_id = os.environ.get("HISTORY_TEMPLATE_FOLDER", "") or os.environ.get("SOVEREIGN_TEMPLATE_FOLDER", "")
    if not token_env or not folder_id:
        return
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build as _build
        from googleapiclient.http import MediaFileUpload

        token_data = json.loads(token_env)
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
        )
        drive = _build("drive", "v3", credentials=creds)
        result = drive.files().create(
            body={"name": f"{story_id}_vo_{lang}.mp3", "parents": [folder_id]},
            media_body=MediaFileUpload(local_path, mimetype="audio/mpeg"),
            supportsAllDrives=True,
            fields="id,name,webViewLink",
        ).execute()
        print(f"Drive upload vo_{lang}: {result.get('webViewLink', result['id'])}", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Drive upload vo_{lang} failed: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--language", choices=["en", "pt"], default="en")
    parser.add_argument("--proof-slides", default="[]")
    parser.add_argument("--video-start-frame", type=int, default=0)
    parser.add_argument("--total-frames", type=int, default=900)
    parser.add_argument("--srt-file", default="")
    parser.add_argument("--translate", action="store_true",
                        help="Translate captions to PT (use with --language pt)")
    parser.add_argument("--hook", default="",
                        help="Bold hook text for the first 5 seconds (frames 0-150). Scroll-stopper.")
    parser.add_argument("--speaker-name", default="")
    parser.add_argument("--speaker-role", default="")
    parser.add_argument("--topic-title", default="")
    parser.add_argument("--video-offset-y", default="15%",
                        help="Vertical crop anchor for face framing (default 15%%). Higher = lower in frame.")
    parser.add_argument("--voiceover", action="store_true",
                        help="Generate ElevenLabs voiceover and upload to Drive")
    args = parser.parse_args()

    captions = parse_srt(args.srt_file)
    if args.translate and args.language == "pt":
        captions = translate_captions(captions, "pt")

    voiceover_url = None
    if args.voiceover and captions:
        narration = " ".join(c["text"] for c in captions if c.get("text"))
        local_audio = generate_voiceover(narration, args.language, args.story_id)
        if local_audio:
            upload_audio_to_drive(local_audio, args.story_id, args.language)
            voiceover_url = "./public/vo.mp3"

    proof_slides = json.loads(args.proof_slides) if args.proof_slides.strip() != "[]" else []

    props = {
        "videoSrc":         "./public/source_clip.mp4",
        "videoStartFrame":  args.video_start_frame,
        "proofSlides":      proof_slides,
        "captions":         captions,
        "language":         args.language,
        "totalFrames":      args.total_frames,
        "hook":             args.hook or None,
        "speakerName":      args.speaker_name or None,
        "speakerRole":      args.speaker_role or None,
        "topicTitle":       args.topic_title,
        "videoOffsetY":     args.video_offset_y if args.video_offset_y != "15%" else None,  # skip if default
        "voiceover_url":    voiceover_url,
    }
    # strip None values — Remotion ignores missing optional props cleanly
    props = {k: v for k, v in props.items() if v is not None}
    print(json.dumps(props, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
