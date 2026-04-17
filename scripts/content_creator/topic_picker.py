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
SKIP_STATUSES = ("done", "posted", "skip", "captured", "archived", "classified", "built", "scheduled")

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
    status = "Approved" if inspo_status.strip().lower() == "approved" else "Draft"
    series = "Tip of the Week" if niche == "opc" else "Quem Decidiu Isso?"

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
    topic = v("topic / title") or v("topic") or v("title") or v("description") or v("comments")

    if status in SKIP_STATUSES:
        return -1, niche, topic, status
    if not topic:
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


def pick_topics(count_opc=2, count_brazil=1):
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

    for idx, row in enumerate(rows[1:], start=2):
        score, niche, topic, inspo_status = score_topic(row, header_map, used, queued)
        if score < 0:
            continue
        brief_idx = header_map.get("brief / angle") or header_map.get("comments") or header_map.get("angle") or header_map.get("brief")
        brief = row[brief_idx].strip() if brief_idx is not None and brief_idx < len(row) else ""
        entry = {
            "row_idx": idx,
            "score": score,
            "niche": niche,
            "topic": topic,
            "brief": brief,
            "inspo_status": inspo_status,
            "url": row[header_map.get("url", 0)] if header_map.get("url") is not None and header_map["url"] < len(row) else "",
        }
        if niche == "opc":
            opc_candidates.append(entry)
        elif niche == "brazil":
            brazil_candidates.append(entry)

    opc_candidates.sort(key=lambda x: x["score"], reverse=True)
    brazil_candidates.sort(key=lambda x: x["score"], reverse=True)

    picks = []
    picks.extend(opc_candidates[:count_opc])
    picks.extend(brazil_candidates[:count_brazil])

    if len(picks) < count_opc + count_brazil:
        remaining = count_opc + count_brazil - len(picks)
        fallback = (opc_candidates[count_opc:] + brazil_candidates[count_brazil:])
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
            updates = [(f"{status_col}{p['row_idx']}", "CLASSIFIED")]
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
