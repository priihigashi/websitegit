#!/usr/bin/env python3
"""
hormozi_carousel_generator.py
==============================
Generates Hormozi-framework carousel content via Claude API.
Saves result to 📋 Content Queue tab → build-carousels.yml picks it up.

HORMOZI FRAMEWORKS APPLIED:
  Slide 1  → Hook (stops the scroll in 1-3 seconds)
  Slide 2  → Retain Step 1 (open loop, setup the problem)
  Slide 3  → Retain Step 2 (contrast: wrong way vs right way)
  Slide 4  → Retain Step 3 (actionable takeaway, give away the WHAT)
  Slide 5  → Reward + CTA (give them something for finishing + direct action)

TRIGGER:
  GitHub Actions → generate-carousel-content.yml → phone-first

REQUIRED SECRETS:
  CLAUDE_KEY_4_CONTENT
  SHEETS_TOKEN (OAuth JSON for carousel sheet, same as build-carousels.yml)

OPTIONAL SECRETS:
  CONTENT_SHEET_ID (defaults to Ideas & Inbox if not set — override in workflow)
"""

import os
import sys
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────

CLAUDE_KEY_4_CONTENT = os.getenv("CLAUDE_KEY_4_CONTENT", "")
CONTENT_SHEET_ID  = os.getenv("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
QUEUE_TAB         = "📋 Content Queue"

# ─── HORMOZI SYSTEM PROMPT ────────────────────────────────────────────────────
# Extracted from hormozi-hooks.md, hormozi-content.md, hormozi-copy.md

HORMOZI_SYSTEM_PROMPT = """You are a carousel content writer for Oak Park Construction using Alex Hormozi's frameworks.

BRAND:
- Company: Oak Park Construction, Florida (licensed CBC1263425)
- Voice: Direct, expert, homeowner-focused
- Visual brand: Yellow #CBCC10 on Black #000000, Anton font for headlines
- CTA always references Oak Park Construction

HORMOZI HOOK FRAMEWORK (Slide 1):
Choose ONE hook type that fits the topic:
- RESULTS: "How I [result] in [timeframe]" / "[Number] in [timeframe] — here's how"
- CONTRARIAN: "[Common belief] is wrong." / "Stop [common action]. It's killing your [outcome]."
- CURIOSITY GAP: "The [unexpected thing] that [impressive result]" / "Nobody talks about this [topic] secret"
- PAIN AGITATE: "You're losing [money/time] every day because of [specific reason]"
- PATTERN INTERRUPT: "[Shocking statement]. Let me explain."
- QUESTION: "What would change if you could [desirable outcome]?"
- MISTAKE: "The #1 mistake [homeowners] make with [topic] (and what to do instead)"

HORMOZI RULES FOR HOOKS:
- Specificity beats vagueness. "$3,000 wasted" > "a lot of money"
- Under 10 words for the main hook line
- Must pass: "Would a homeowner stop scrolling for this?"

HORMOZI RETAIN FRAMEWORK (Slides 2-4):
- Give away the WHAT and WHY for free. Sell the HOW (implementation = hire Oak Park).
- Slide 2: Open a loop — set up the problem vividly
- Slide 3: Contrast — wrong way vs right way (short, punchy)
- Slide 4: The framework/tip — actionable, specific, numbered if possible

HORMOZI REWARD + CTA FRAMEWORK (Slide 5):
- Give them something for finishing (a memorable takeaway or framework name)
- Direct CTA — one action, no ambiguity
- Hormozi voice: "Here's the thing." / "Do the math." / "It's not complicated."
- Always ends with Oak Park Construction contact

HORMOZI COPY RULES (all slides):
- Short sentences. One idea per sentence.
- Specific numbers over vague claims
- Anti-hype — no "amazing", "life-changing", "revolutionary"
- Write like talking to a friend who needs real advice
- Under 12 words per slide headline
- Under 30 words per slide body text

OUTPUT FORMAT — return ONLY valid JSON, no markdown:
{
  "hook": "The main hook text for slide 1 (under 10 words)",
  "hook_type": "which category you used (results/contrarian/curiosity/pain/mistake/question)",
  "slides": [
    {
      "slide": 1,
      "headline": "Hook slide headline (under 10 words)",
      "body": "Supporting text under 25 words",
      "function": "HOOK"
    },
    {
      "slide": 2,
      "headline": "Problem setup headline",
      "body": "Body text under 30 words",
      "function": "RETAIN — open loop"
    },
    {
      "slide": 3,
      "headline": "Wrong vs Right headline",
      "body": "Contrast body under 30 words",
      "function": "RETAIN — contrast"
    },
    {
      "slide": 4,
      "headline": "The tip/framework headline",
      "body": "Actionable tip under 30 words",
      "function": "RETAIN — value"
    },
    {
      "slide": 5,
      "headline": "Reward + CTA headline",
      "body": "CTA body — direct, specific, Oak Park branded",
      "function": "REWARD + CTA"
    }
  ],
  "caption": "Instagram caption (150-200 words) in Hormozi voice — hook first, deliver value, end with CTA and question for comments",
  "cta": "The CTA line for the last slide (under 12 words)",
  "hashtags": "#OakParkConstruction #[relevant] #[homeowner] #[topic] (8-12 hashtags)",
  "hook_alternatives": [
    "Alternative hook option 2",
    "Alternative hook option 3"
  ]
}"""


# ─── GOOGLE SHEETS AUTH ───────────────────────────────────────────────────────

_token_cache = {}

def get_sheets_token():
    if _token_cache.get("token") and __import__("time").time() < _token_cache.get("exp", 0):
        return _token_cache["token"]

    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        path = os.environ.get("SHEETS_TOKEN_PATH", "")
        if path and __import__("pathlib").Path(path).exists():
            raw = __import__("pathlib").Path(path).read_text()

    # Also try GOOGLE_SA_KEY (service account, base64)
    if not raw:
        sa_b64 = os.environ.get("GOOGLE_SA_KEY", "")
        if sa_b64:
            return _get_sa_token(sa_b64)

    if not raw:
        raise RuntimeError("No SHEETS_TOKEN or GOOGLE_SA_KEY available")

    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["token"] = resp["access_token"]
    _token_cache["exp"]   = __import__("time").time() + resp.get("expires_in", 3500) - 60
    return resp["access_token"]


def _get_sa_token(sa_b64: str) -> str:
    """Get access token from service account (GOOGLE_SA_KEY base64 JSON)."""
    import time, json, base64, jwt
    sa_info = json.loads(base64.b64decode(sa_b64))
    now = int(time.time())
    payload = {
        "iss": sa_info["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    signed = jwt.encode(payload, sa_info["private_key"], algorithm="RS256")
    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  signed,
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    token = resp["access_token"]
    _token_cache["token"] = token
    _token_cache["exp"]   = time.time() + resp.get("expires_in", 3500) - 60
    return token


def sheet_append(token, row: list):
    """Append a row to QUEUE_TAB."""
    tab_enc = urllib.parse.quote(QUEUE_TAB, safe="")
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{CONTENT_SHEET_ID}"
           f"/values/{tab_enc}!A1:P1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS")
    payload = json.dumps({"values": [row]}).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    resp = urllib.request.urlopen(req).read()
    return json.loads(resp)


# ─── CLAUDE API ───────────────────────────────────────────────────────────────

def generate_with_claude(topic: str, niche: str, hook_style: str) -> dict:
    """Call Claude with Hormozi frameworks to generate carousel content."""
    if not CLAUDE_KEY_4_CONTENT:
        raise RuntimeError("CLAUDE_KEY_4_CONTENT not set")

    user_prompt = f"""Create a 5-slide Instagram carousel for Oak Park Construction.

TOPIC: {topic}
NICHE: {niche}
PREFERRED HOOK STYLE: {hook_style if hook_style and hook_style != "auto" else "pick the best one for this topic"}

Apply Hormozi's Hook → Retain → Reward framework strictly.
Remember: give away the WHAT and WHY. The HOW = hire Oak Park Construction.

Return only the JSON object. No markdown, no explanation."""

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "system": HORMOZI_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": CLAUDE_KEY_4_CONTENT,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    content = resp["content"][0]["text"].strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()

    return json.loads(content)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate Hormozi-powered carousel content")
    parser.add_argument("topic",       help="Carousel topic or idea")
    parser.add_argument("--niche",     default="Oak Park Construction",
                        help="Target niche (Oak Park Construction / Brazil / General)")
    parser.add_argument("--hook-style",default="auto",
                        help="Hook type: auto/results/contrarian/curiosity/pain/mistake/question")
    parser.add_argument("--service",   default="General",
                        help="Service type (e.g. Bathroom Remodel, Roofing, Flooring)")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Generate content but do NOT write to sheet")
    args = parser.parse_args()

    print(f"\n🪝 Hormozi Carousel Generator")
    print(f"   Topic: {args.topic}")
    print(f"   Niche: {args.niche}")
    print(f"   Hook style: {args.hook_style}")
    print(f"   Service: {args.service}")
    print()

    # Step 1 — Generate with Claude
    print("📝 Generating content with Claude + Hormozi frameworks...")
    result = generate_with_claude(args.topic, args.niche, args.hook_style)

    # Step 2 — Print results
    print(f"\n✅ Hook ({result.get('hook_type', 'auto')}): {result['hook']}")
    print(f"   Alt 1: {result.get('hook_alternatives', ['—', '—'])[0]}")
    print(f"   Alt 2: {result.get('hook_alternatives', ['—', '—'])[1]}")
    print()
    for slide in result.get("slides", []):
        print(f"  Slide {slide['slide']} [{slide['function']}]")
        print(f"    Headline: {slide['headline']}")
        print(f"    Body: {slide['body']}")
    print(f"\n  Caption (first 150): {result.get('caption', '')[:150]}...")
    print(f"  CTA: {result.get('cta', '')}")
    print(f"  Hashtags: {result.get('hashtags', '')}")

    if args.dry_run:
        print("\n⚠️  DRY RUN — not saving to sheet")
        return

    # Step 3 — Format caption body (all slides as structured text)
    slides = result.get("slides", [])
    caption_body = "\n".join([
        f"[Slide {s['slide']}] {s['headline']} — {s['body']}"
        for s in slides
    ])

    # Step 4 — Append to Content Queue
    print(f"\n📊 Saving to {QUEUE_TAB}...")
    token = get_sheets_token()
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    row = [
        datetime.now().strftime("%Y-%m-%d"),  # A: Date Created
        "Oak Park Construction",               # B: Project Name
        args.service,                          # C: Service Type
        "",                                    # D: Photo(s) Used — Priscila fills in
        "Carousel",                            # E: Content Type
        result["hook"],                        # F: Hook
        caption_body,                          # G: Caption Body (slide structure)
        result.get("cta", ""),                # H: CTA
        result.get("hashtags", ""),           # I: Hashtags
        "Approved",                            # J: Status — ready for build-carousels.yml
        "",                                    # K: after processed
        "",                                    # L: ok to schedule
        tomorrow,                              # M: Suggested Post Date
        "09:00",                               # N: suggested time
        "Instagram",                           # O: Platform
        f"Hormozi Generator — {args.hook_style} hook",  # P: Content Source
    ]
    sheet_append(token, row)
    print(f"✅ Saved to {QUEUE_TAB} — status: Approved")
    print(f"   → build-carousels.yml will pick it up on next run")
    print(f"   → Add photo filename to column D before running carousel builder")
    print()

    # Step 5 — Print hook alternatives for reference
    print("🪝 Alternative hooks (save these for A/B testing):")
    for i, alt in enumerate(result.get("hook_alternatives", []), 1):
        print(f"   {i}. {alt}")


if __name__ == "__main__":
    main()
