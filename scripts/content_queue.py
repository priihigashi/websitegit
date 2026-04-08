#!/usr/bin/env python3
"""
content_queue.py — Oak Park Construction Content Creation
Reads the 📸 Photo Catalog tab, groups photos by Project Name,
generates Instagram content (Hook + Caption + CTA + Hashtags) using Claude,
and appends new rows to the 📋 Content Queue tab.

SAFE: never clears existing rows. Skips projects already in the queue.
Usage: python3 content_queue.py
"""

import os, json, time, urllib.request, urllib.parse
from datetime import date, datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
WORKSPACE    = Path("/Users/priscilahigashi/ClaudeWorkspace")
TOKEN_FILE   = WORKSPACE / "Credentials" / "sheets_token.json"
ENV_FILE     = WORKSPACE / ".env"
SHEET_ID     = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CATALOG_TAB  = "📸 Photo Catalog"
QUEUE_TAB    = "📋 Content Queue"

QUEUE_HEADER = [
    "Date Created", "Project Name", "Service Type", "Photo(s) Used",
    "Content Type", "Hook", "Caption Body", "CTA", "Hashtags",
    "Status", "Suggested Post Date", "Platform"
]

# ── Env ───────────────────────────────────────────────────────────────────────
def load_env() -> dict:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

# ── Google Auth ───────────────────────────────────────────────────────────────
def get_credentials():
    from google.oauth2.credentials import Credentials

    token_data = json.loads(TOKEN_FILE.read_text())
    data = urllib.parse.urlencode({
        "client_id":     token_data["client_id"],
        "client_secret": token_data["client_secret"],
        "refresh_token": token_data["refresh_token"],
        "grant_type":    "refresh_token"
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    resp = json.loads(urllib.request.urlopen(req).read())
    access_token = resp["access_token"]

    return Credentials(
        token=access_token,
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data.get("scopes", [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets"
        ])
    )

# ── Sheet helpers ─────────────────────────────────────────────────────────────
def get_sheets(creds):
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds)

def read_tab(sheets, tab: str) -> list[list]:
    """Read all rows from a tab. Returns list of rows (each row = list of strings)."""
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{tab}'"
    ).execute()
    return result.get("values", [])

def ensure_queue_tab(sheets):
    """Create Content Queue tab with header if it doesn't exist."""
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if QUEUE_TAB not in tabs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": QUEUE_TAB}}}]}
        ).execute()
        # Write header
        sheets.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"'{QUEUE_TAB}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [QUEUE_HEADER]}
        ).execute()
        print(f"✅ Created '{QUEUE_TAB}' tab with headers")
    else:
        print(f"✅ '{QUEUE_TAB}' tab already exists")

def get_queue_projects(sheets) -> set:
    """Return set of Project Names already in the queue (to skip duplicates)."""
    try:
        rows = read_tab(sheets, QUEUE_TAB)
        if len(rows) <= 1:
            return set()
        return {r[1].strip() for r in rows[1:] if len(r) > 1}
    except Exception:
        return set()

def append_rows(sheets, rows: list):
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"'{QUEUE_TAB}'!A:L",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()

# ── Read + Group Catalog ───────────────────────────────────────────────────────
def parse_catalog(rows: list) -> dict:
    """
    Returns dict: { project_name: [{"service": ..., "filename": ..., "url": ..., "description": ..., "phase": ...}, ...] }
    Header row (row 0) maps column names to indices.
    """
    if not rows:
        return {}

    header = [h.lower().strip() for h in rows[0]]
    def col(name):
        for i, h in enumerate(header):
            if name in h:
                return i
        return None

    ci_project  = col("project")
    ci_service  = col("service")
    ci_filename = col("filename")
    ci_url      = col("drive url")
    ci_desc     = col("ai description")
    ci_phase    = col("phase")

    groups = {}
    for row in rows[1:]:
        if not row or len(row) <= (ci_project or 0):
            continue
        project = row[ci_project].strip() if ci_project is not None and len(row) > ci_project else ""
        if not project:
            continue

        def safe(i):
            return row[i].strip() if i is not None and len(row) > i else ""

        entry = {
            "service":     safe(ci_service),
            "filename":    safe(ci_filename),
            "url":         safe(ci_url),
            "description": safe(ci_desc),
            "phase":       safe(ci_phase),
        }
        groups.setdefault(project, []).append(entry)

    return groups

def priority_sort(groups: dict) -> list:
    """Return project names sorted: Kitchens first, then Bathrooms, then rest."""
    kitchens = [p for p in groups if "kitchen" in p.lower()]
    bathrooms = [p for p in groups if "bath" in p.lower() and p not in kitchens]
    rest = [p for p in groups if p not in kitchens and p not in bathrooms]
    return kitchens + bathrooms + rest

# ── Claude Content Generation ─────────────────────────────────────────────────
def generate_content(project_name: str, photos: list, api_key: str) -> list[dict]:
    """
    Call Claude API to generate 2-3 content ideas for this project group.
    Returns list of dicts with keys: content_type, hook, caption, cta, hashtags
    """
    service_type = photos[0]["service"] if photos else "Renovation"

    # Build a summary of available photos + descriptions
    photo_lines = []
    for p in photos:
        if p["description"]:
            photo_lines.append(f"- {p['filename']} ({p['phase']}): {p['description']}")
    photo_summary = "\n".join(photo_lines) if photo_lines else "No descriptions available yet."

    # Map project name to a safe public location label (city only — never street address)
    location_map = {
        "1270 harbor": "Pompano Beach",
        "harbor ct": "Pompano Beach",
        "dockside": "Pompano Beach",
        "kinney": "Fort Lauderdale",
        "sawgrass": "Plantation",
        "plantation": "Plantation",
        "clark": "South Florida",
        "9720": "Miami",
        "92nd": "Miami",
    }
    location_label = "South Florida"
    for key, city in location_map.items():
        if key in project_name.lower():
            location_label = city
            break

    prompt = f"""You are a social media content writer for Oak Park Construction, a boutique renovation and construction company in Pompano Beach, FL. Mike is the GC/PM, his wife Priscila runs the social media.

Brand voice: warm, proud, direct. Real homeowners, real results. South Florida market.

Project: "{project_name}" (location: {location_label})
Service Type: {service_type}
Photos available:
{photo_summary}

PRIVACY RULE — STRICTLY ENFORCE:
- NEVER write a street address, house number, or specific address in any caption or hook
- Use ONLY city names: Pompano Beach, Fort Lauderdale, Plantation, Miami, Broward County, South Florida
- WRONG: "At 1270 Harbor Ct..." | RIGHT: "In Pompano Beach..."
- WRONG: "This Harbor Ct project..." | RIGHT: "This Pompano Beach project..."
- If the project name is a street address, refer to it only by city

Generate exactly 3 content ideas for Instagram. For each idea:
1. Suggest Content Type: Carousel, Reel, or Static Post
2. Write a Hook (first line — grabs attention in the feed, max 12 words, no emojis in the hook itself)
3. Write Caption Body (2-4 short paragraphs, conversational, specific to this project, include 1-2 relevant emojis, max 150 words total)
4. Write CTA (one clear call to action, e.g. "DM us 'KITCHEN' to see the full project" or "Save this for your reno inspo")
5. Write 15-20 Hashtags (mix of local #pompanobeach #southfloridahomes, niche #kitchenremodel, and broad #homerenovation)

Format your response as valid JSON array like this:
[
  {{
    "content_type": "Carousel",
    "hook": "...",
    "caption": "...",
    "cta": "...",
    "hashtags": "#tag1 #tag2 ..."
  }},
  ...
]

Only return the JSON array, no extra text."""

    body = json.dumps({
        "model": "claude-opus-4-6",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )

    for attempt in range(3):
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
            raw = resp["content"][0]["text"].strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            ideas = json.loads(raw)
            return ideas
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"  ⏳ Rate limit — waiting {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
                # Need to re-create the request object (body was consumed)
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=body,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    }
                )
            else:
                print(f"  ⚠️  HTTP {e.code} for '{project_name}': {e}")
                return []
        except Exception as e:
            print(f"  ⚠️  Claude API error for '{project_name}': {e}")
            return []
    return []

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    env = load_env()
    api_key = env.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not found in .env")

    print("🔐 Authenticating with Google...")
    creds = get_credentials()
    sheets = get_sheets(creds)

    print(f"📖 Reading '{CATALOG_TAB}'...")
    catalog_rows = read_tab(sheets, CATALOG_TAB)
    if len(catalog_rows) <= 1:
        print("❌ Photo Catalog is empty. Run photo_catalog.py first.")
        return

    print(f"   Found {len(catalog_rows) - 1} catalog rows")

    # Parse + group
    groups = parse_catalog(catalog_rows)
    print(f"   Grouped into {len(groups)} projects: {list(groups.keys())}")

    # Ensure queue tab exists
    ensure_queue_tab(sheets)

    # Get already-queued projects (safe dedup)
    existing = get_queue_projects(sheets)
    print(f"   Already in queue: {existing if existing else 'none'}")

    # Priority order
    ordered = priority_sort(groups)
    today = date.today().isoformat()

    new_rows = []
    processed = 0

    for project in ordered:
        if project in existing:
            print(f"⏭️  Skipping '{project}' — already in queue")
            continue

        photos = groups[project]
        service = photos[0]["service"] if photos else "Renovation"
        filenames = ", ".join(p["filename"] for p in photos if p["filename"])

        print(f"\n✍️  Generating content for: '{project}' ({len(photos)} photos)...")
        ideas = generate_content(project, photos, api_key)

        if not ideas:
            print(f"  ⚠️  No ideas generated, skipping")
            time.sleep(15)
            continue

        # Determine suggested post date (stagger: every 2 days from today)
        from datetime import timedelta
        post_date = date.today() + timedelta(days=2 + processed * 2)

        for idea in ideas:
            new_rows.append([
                today,                                      # Date Created
                project,                                    # Project Name
                service,                                    # Service Type
                filenames,                                  # Photo(s) Used
                idea.get("content_type", ""),               # Content Type
                idea.get("hook", ""),                       # Hook
                idea.get("caption", ""),                    # Caption Body
                idea.get("cta", ""),                        # CTA
                idea.get("hashtags", ""),                   # Hashtags
                "Idea",                                     # Status
                post_date.isoformat(),                      # Suggested Post Date
                "Instagram"                                 # Platform
            ])

        processed += 1
        print(f"  ✅ {len(ideas)} ideas generated")
        time.sleep(8)  # pause between projects to respect rate limits

    if new_rows:
        print(f"\n📝 Writing {len(new_rows)} rows to '{QUEUE_TAB}'...")
        append_rows(sheets, new_rows)
        print(f"✅ Done! {len(new_rows)} content ideas added to the queue.")
        print(f"   View: https://docs.google.com/spreadsheets/d/{SHEET_ID}")
    else:
        print("\n✅ Nothing new to add — all projects already in queue.")

if __name__ == "__main__":
    main()
