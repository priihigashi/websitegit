#!/usr/bin/env python3
"""
topic_picker.py — Picks 3 topics for daily carousel creation.
Reads Inspiration Library tab, scores by readiness, returns 2 OPC + 1 Brazil.
Skips topics that need heavy research or have no clear angle.
"""
import json, os, time, urllib.request, urllib.parse, random

SHEET_ID = os.environ.get("CONTENT_SHEET_ID", "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU")
INSPO_TAB = "📥 Inspiration Library"
CATALOG_TAB = "📸 Project Content Catalog"

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


def score_topic(row, header_map, used_topics):
    def v(name):
        idx = header_map.get(name.lower())
        if idx is not None and idx < len(row):
            return row[idx].strip()
        return ""

    url = v("url")
    status = v("status").lower()
    niche = v("niche").lower() if v("niche") else ""
    comments = v("comments").lower()
    topic = v("topic") or v("title") or v("comments")

    if status in ("done", "posted", "skip", "captured", "archived"):
        return -1, niche, topic
    if not topic:
        return -1, niche, topic

    post_id_guess = topic[:40].lower().replace(" ", "-")
    if post_id_guess in used_topics:
        return -1, niche, topic

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

    score += random.randint(0, 3)

    return score, niche, topic


def pick_topics(count_opc=2, count_brazil=1):
    rows = sheet_get(f"'{INSPO_TAB}'")
    if len(rows) < 2:
        print("Inspiration Library empty")
        return []

    header = rows[0]
    header_map = {h.strip().lower(): i for i, h in enumerate(header)}
    used = get_used_topics()

    opc_candidates = []
    brazil_candidates = []

    for idx, row in enumerate(rows[1:], start=2):
        score, niche, topic = score_topic(row, header_map, used)
        if score < 0:
            continue
        entry = {
            "row_idx": idx,
            "score": score,
            "niche": niche,
            "topic": topic,
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

    for p in picks:
        print(f"  Picked: [{p['niche']}] {p['topic'][:60]} (score={p['score']}, row={p['row_idx']})")

    return picks


if __name__ == "__main__":
    topics = pick_topics()
    print(json.dumps(topics, indent=2))
