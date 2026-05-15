#!/usr/bin/env python3
"""
topic_picker.py — Picks 3 topics for daily carousel creation.
Reads Inspiration Library tab, scores by readiness, returns 2 OPC + 1 Brazil.
Skips topics that need heavy research or have no clear angle.
"""
import json, os, time, urllib.request, urllib.parse

SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
INSPO_TAB = "📥 Inspiration Library"
QUEUE_TAB = "📋 Content Queue"
CATALOG_TAB = "📸 Project Content Catalog"
MIN_SCORE = 8  # Topics below this are not ready — skip regardless of niche
SKIP_STATUSES = ("posted", "skip", "captured", "built", "error", "not identified", "needs research")

_token_cache = {}

def get_token():
    if _token_cache.get("token") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["token"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        raise RuntimeError("No SHEETS_TOKEN set")
    td = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id": td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
    _token_cache["token"] = resp["access_token"]
    _token_cache["exp"] = time.time() + resp.get("expires_in", 3500) - 60
    return resp["access_token"]


def sheet_get(range_str):
    token = get_token()
    enc = urllib.parse.quote(range_str, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req).read()).get("values", [])


def fetch_drive_doc_content(doc_url_or_id: str) -> str:
    """Fetch full text content of a Google Doc given its URL or file ID.
    Used to load the brief doc into the pipeline before carousel generation."""
    import re
    doc_id = doc_url_or_id
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", doc_url_or_id)
    if m:
        doc_id = m.group(1)
    try:
        token = get_token()
        url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        doc = json.loads(urllib.request.urlopen(req, timeout=15).read())
        text_parts = []
        for block in doc.get("body", {}).get("content", []):
            for el in block.get("paragraph", {}).get("elements", []):
                t = el.get("textRun", {}).get("content", "")
                if t:
                    text_parts.append(t)
        return "".join(text_parts).strip()
    except Exception as e:
        print(f"  fetch_drive_doc_content failed ({doc_id[:20]}): {e}")
        return ""


def sheet_update(range_str, values):
    token = get_token()
    enc = urllib.parse.quote(range_str, safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}?valueInputOption=USER_ENTERED"
    payload = json.dumps({"values": values}).encode()
    req = urllib.request.Request(url, data=payload, method="PUT",
                                headers={"Authorization": f"Bearer {token}",
                                         "Content-Type": "application/json"})
    urllib.request.urlopen(req)


def get_used_topics():
    rows = sheet_get(f"'{CATALOG_TAB}'!A:D")
    used = set()
    for row in rows[1:]:
        if len(row) > 0:
            used.add(row[0].lower().strip())
    return used


def get_queued_topics():
    """Topics already in Content Queue (any status) — avoid duplicate inserts."""
    try:
        rows = sheet_get(f"'{QUEUE_TAB}'!B:B")
    except Exception:
        return set()
    return {r[0].lower().strip() for r in rows[1:] if r and r[0].strip()}


def col_letter(n):
    r = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        r = chr(65 + rem) + r
    return r


def insert_queue_row(topic_entry, inspo_status):
    """Insert a new row into Content Queue. Returns the new row number.
    Inspo 'approved' → Queue 'Approved' (auto-builds next run)
    Inspo anything else → Queue 'Draft' (awaits manual approval)
    """
    from datetime import datetime
    today = datetime.utcnow().strftime("%Y-%m-%d")
    niche = topic_entry["niche"]
    # OPC house content auto-approves (no fact-check gate needed).
    # Brazil/USA news require Inspiration status = "approved" explicitly.
    niche = topic_entry.get("niche", "")
    status = "Approved" if (niche == "opc" or inspo_status.strip().lower() == "approved") else "Draft"
    # series_override allows per-topic routing to Verificamos / Fact-Checked / etc.
    series = topic_entry.get("series_override") or (
        "Tip of the Week" if niche == "opc" else ("The Chain" if niche == "usa" else "Quem Decidiu Isso?")
    )

    # Read header to locate columns by name
    hdr_rows = sheet_get(f"'{QUEUE_TAB}'!1:1")
    if not hdr_rows:
        print(f"  Queue insert skipped — can't read headers")
        return None
    header = hdr_rows[0]
    hmap = {h.strip().lower(): i for i, h in enumerate(header)}
    width = len(header)

    row = [""] * width
    def put(col_name, val):
        idx = hmap.get(col_name.lower())
        if idx is not None and idx < width:
            row[idx] = val

    put("date created", today)
    put("project name", topic_entry["topic"])
    put("content type", "Carousel")
    put("status", status)
    put("date status changed", today)
    put("platform", "Instagram")
    put("source", niche.upper())
    put("inspo url", topic_entry.get("url", ""))
    put("date moved to queue", today)
    put("brief / angle", topic_entry.get("brief", ""))
    put("format", series)
    story_id = (
        topic_entry.get("story_id")
        or topic_entry.get("capture_story_id")
        or topic_entry.get("resource_story_id")
        or topic_entry.get("source_story_id")
        or ""
    )
    if story_id:
        for col in ("story_id", "story id", "capture_story_id", "capture story id"):
            if col in hmap:
                put(col, story_id)
                break
    # Propagate series_override and fake_news_route so pipeline routes correctly
    _so = topic_entry.get("series_override", "")
    if _so:
        put("series_override", _so.upper())
    _fnr = topic_entry.get("fake_news_route", "")
    if _fnr:
        put("fake_news_route", _fnr)

    # Append via Sheets API
    enc = urllib.parse.quote(f"'{QUEUE_TAB}'!A:A", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{enc}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
    payload = json.dumps({"values": [row]}).encode()
    token = get_token()
    req = urllib.request.Request(url, data=payload,
                                headers={"Authorization": f"Bearer {token}",
                                         "Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        # updatedRange like "'📋 Content Queue'!A1051:Z1051" → extract row number
        updated = resp.get("updates", {}).get("updatedRange", "")
        row_num = None
        if "!" in updated:
            after = updated.split("!")[1]
            import re
            m = re.search(r"(\d+)", after)
            if m: row_num = int(m.group(1))
        print(f"  Queue row added: [{status}] {topic_entry['topic'][:50]} (row={row_num})")
        return row_num
    except Exception as e:
        print(f"  Queue insert failed: {e}")
        return None


CLIP_COLLECTIONS_TAB = "📋 Clip Collections"

def get_clip_count_for_topic(topic: str) -> int:
    """Return how many clip rows exist in Clip Collections tab for this topic.
    Reads the tab header to find the TOPIC column by name. Returns 0 on any error.
    """
    try:
        rows = sheet_get(f"'{CLIP_COLLECTIONS_TAB}'")
        if len(rows) < 2:
            return 0
        header = [h.strip().lower() for h in rows[0]]
        topic_col = next((i for i, h in enumerate(header)
                          if h in ("topic", "topic / title", "title", "subject")), None)
        if topic_col is None:
            return 0  # no topic column found — can't match any row
        topic_lower = topic.strip().lower()
        count = 0
        for row in rows[1:]:
            cell = row[topic_col].strip().lower() if topic_col < len(row) else ""
            if cell and (cell in topic_lower or topic_lower in cell):
                count += 1
        return count
    except Exception as e:
        print(f"  get_clip_count_for_topic error: {e}")
        return 0


def score_topic(row, header_map, used_topics, queued_topics):
    def v(name):
        idx = header_map.get(name.lower())
        if idx is not None and idx < len(row):
            return row[idx].strip()
        return ""

    url = v("url")
    status = v("status").lower()
    niche = v("niche").lower() if v("niche") else ""
    comments = v("comments").lower()
    # STRICT topic source — never fall back to description/comments.
    # Description-as-topic produced junk rows (Rickroll, "Portuguese discussion about...") 2026-04-17.
    topic = v("topic / title") or v("topic") or v("title")

    if status in SKIP_STATUSES:
        return -1, niche, topic, status
    if v("sibling_of"):
        return -1, niche, topic, status
    if not topic:
        return -1, niche, topic, status
    # Reject junk topics: too short, or look like generic descriptions/placeholders
    JUNK_MARKERS = ("this is ", "a portuguese-language ", "a brazilian ", "research still needed",
                    "transcript contains only", "portuguese-language discussion")
    tlo = topic.lower()
    if len(topic.strip()) < 12 or any(m in tlo for m in JUNK_MARKERS):
        return -1, niche, topic, status

    # Skip if already in Content Queue
    if topic.lower().strip() in queued_topics:
        return -1, niche, topic, status

    post_id_guess = topic[:40].lower().replace(" ", "-")
    if post_id_guess in used_topics:
        return -1, niche, topic, status

    score = 10

    if niche in ("opc", "oak park", "oak park construction"):
        niche = "opc"
        score += 5
    elif niche in ("brazil", "brasil", "news-brazil"):
        niche = "brazil"
        score += 3
    elif niche in ("usa", "news-usa", "united states", "us", "news-us", "america"):
        niche = "usa"
        score += 3
    elif "brazil" in niche and ("usa" in niche or "united" in niche or " us" in niche or "us " in niche):
        # Bilingual row (brazil + usa) — route as Brazil. carousel_builder already emits PT+EN on same slides.
        niche = "brazil"
        score += 3
    else:
        score -= 5

    if url:
        score += 3
    if "tip" in comments or "how" in comments:
        score += 4
    if "research" in comments or "complex" in comments or "develop" in comments:
        score -= 8

    # Pre-approved items always pass
    if status == "approved":
        score = max(score, MIN_SCORE + 1)

    if score < MIN_SCORE:
        return -1, niche, topic, status

    return score, niche, topic, status


def pick_topics(count_opc=2, count_brazil=1, count_usa=1):
    rows = sheet_get(f"'{INSPO_TAB}'")
    if len(rows) < 2:
        print("Inspiration Library empty")
        return []

    header = rows[0]
    header_map = {h.strip().lower(): i for i, h in enumerate(header)}
    used = get_used_topics()
    queued = get_queued_topics()

    opc_candidates = []
    brazil_candidates = []
    usa_candidates = []

    for idx, row in enumerate(rows[1:], start=2):
        score, niche, topic, inspo_status = score_topic(row, header_map, used, queued)
        if score < 0:
            continue
        brief_idx = header_map.get("brief / angle") or header_map.get("comments") or header_map.get("angle") or header_map.get("brief")
        brief_raw = row[brief_idx].strip() if brief_idx is not None and brief_idx < len(row) else ""
        # If the brief field contains a Google Docs URL, fetch the full doc content
        if "docs.google.com/document" in brief_raw or (brief_raw.startswith("https://") and "/d/" in brief_raw):
            brief = fetch_drive_doc_content(brief_raw) or brief_raw
        else:
            brief = brief_raw
        _cn1 = header_map.get("clips_needed")
        _cn2 = header_map.get("clips needed")
        clips_needed_idx = _cn1 if _cn1 is not None else _cn2
        clips_needed_val = row[clips_needed_idx].strip() if clips_needed_idx is not None and clips_needed_idx < len(row) else ""
        def _rv(col_name):
            idx2 = header_map.get(col_name.lower())
            return row[idx2].strip() if idx2 is not None and idx2 < len(row) else ""

        def _rv_any(col_names):
            for col_name in col_names:
                val = _rv(col_name)
                if val:
                    return val
            return ""

        entry = {
            "row_idx": idx,
            "score": score,
            "niche": niche,
            "topic": topic,
            "brief": brief,
            "inspo_status": inspo_status,
            "url": row[header_map.get("url", 0)] if header_map.get("url") is not None and header_map["url"] < len(row) else "",
            "clips_needed": clips_needed_val,
            "series_override": _rv("series_override"),
            "fake_news_route": _rv("fake_news_route"),
            "story_id": _rv_any(("story_id", "story id", "capture_story_id", "capture story id", "resource_story_id", "source_story_id")),
        }
        if niche == "opc":
            opc_candidates.append(entry)
        elif niche == "brazil":
            brazil_candidates.append(entry)
        elif niche == "usa":
            usa_candidates.append(entry)

    opc_candidates.sort(key=lambda x: x["score"], reverse=True)
    brazil_candidates.sort(key=lambda x: x["score"], reverse=True)
    usa_candidates.sort(key=lambda x: x["score"], reverse=True)

    picks = []
    picks.extend(opc_candidates[:count_opc])
    picks.extend(brazil_candidates[:count_brazil])
    picks.extend(usa_candidates[:count_usa])

    target = count_opc + count_brazil + count_usa
    if len(picks) < target:
        remaining = target - len(picks)
        fallback = (opc_candidates[count_opc:] + brazil_candidates[count_brazil:] + usa_candidates[count_usa:])
        fallback.sort(key=lambda x: x["score"], reverse=True)
        picks.extend(fallback[:remaining])

    from datetime import datetime
    now = datetime.utcnow().strftime("%Y-%m-%d")
    status_idx = header_map.get("status")
    date_idx   = header_map.get("date status changed") or header_map.get("date_status_changed")

    for p in picks:
        print(f"  Picked: [{p['niche']}] {p['topic'][:60]} (score={p['score']}, row={p['row_idx']})")
        # Insert into Content Queue with Draft/Approved based on Inspiration status
        queue_row = insert_queue_row(p, p.get("inspo_status", ""))
        p["queue_row_idx"] = queue_row

        if status_idx is not None:
            status_col = col_letter(status_idx + 1)
            # Mark as Built once consumed for Content Queue — canonical 10-state
            # vocab. Dedup against re-pick is handled by get_queued_topics() and
            # the SKIP_STATUSES check (Built is in skip).
            updates = [(f"{status_col}{p['row_idx']}", "Built")]
            if date_idx is not None:
                date_col = col_letter(date_idx + 1)
                updates.append((f"{date_col}{p['row_idx']}", now))
            try:
                for cell, val in updates:
                    sheet_update(f"'{INSPO_TAB}'!{cell}", [[val]])
            except Exception as e:
                print(f"  Flow tracking write failed: {e}")

    return picks


if __name__ == "__main__":
    topics = pick_topics()
    print(json.dumps(topics, indent=2))
