"""
photo_matcher.py — Match OPC carousel topics to real jobsite photos.

Reads the 📸 Photo Catalog tab in Ideas & Inbox and returns the Drive URL
of the best-matching photo for a given topic + optional filters.

Column layout (1-indexed, matches photo_catalog_cloud.py):
  A=Date Added  B=Project Name  C=Service Type  D=Filename  E=Drive URL
  F=AI Description  G=Phase  H=Quality ⭐  I=Enhanced?  J=Used In Post?
"""
import os
import re
import time
import urllib.request
import urllib.parse
import json


SHEET_ID    = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
CATALOG_TAB = "📸 Photo Catalog"
MIN_QUALITY = 4

# OPC_MATERIAL_REFERENCE — curated fallback search terms when no catalog photo matches.
# These become query strings for stock-photo providers (Pexels/Pixabay/Wikimedia).
# NOT a live scraper — static constants only. One category per material type.
OPC_MATERIAL_REFERENCE = {
    "paint": [
        "benjamin moore white dove OC-17 interior wall",
        "sherwin williams agreeable gray SW 7029 living room",
        "benjamin moore hale navy HC-154 cabinet paint",
        "sherwin williams accessible beige SW 7036 bedroom",
        "interior wall paint roller application professional",
    ],
    "flooring": [
        "hardwood oak flooring installation",
        "luxury vinyl plank LVP flooring close up",
        "porcelain tile floor installation South Florida",
        "engineered hardwood flooring residential",
        "tile grout flooring detail",
    ],
    "tile": [
        "subway tile kitchen backsplash installation",
        "marble tile bathroom floor pattern",
        "hexagon mosaic tile shower wall",
        "large format porcelain tile installation",
        "handmade ceramic tile backsplash close up",
    ],
    "countertop": [
        "quartz countertop kitchen white veined",
        "granite countertop dark stone kitchen",
        "butcher block wood countertop detail",
        "marble countertop bathroom vanity",
        "quartzite slab countertop installation",
    ],
    "lumber": [
        "pressure treated lumber stack construction",
        "2x4 framing lumber construction site",
        "cedar wood siding exterior installation",
        "engineered LVL beam installation framing",
        "plywood subfloor installation residential",
    ],
    "roofing": [
        "asphalt shingle roof installation South Florida",
        "metal standing seam roof residential",
        "flat roof membrane installation commercial",
        "tile roof clay residential Florida",
        "roof underlayment installation close up",
    ],
    "windows_doors": [
        "impact window installation South Florida hurricane",
        "french door exterior installation residential",
        "sliding glass door installation patio",
        "casement window installation modern home",
        "front door entry replacement installation",
    ],
    "concrete": [
        "concrete slab pouring residential foundation",
        "stamped concrete driveway pattern",
        "concrete block wall construction",
        "polished concrete floor residential",
        "concrete formwork construction site",
    ],
    "insulation": [
        "spray foam insulation attic application",
        "batt insulation wall cavity residential",
        "rigid foam board insulation exterior",
        "blown in insulation attic residential",
    ],
    "cabinets": [
        "white shaker kitchen cabinet installation",
        "kitchen cabinet door hardware close up",
        "custom cabinet box installation kitchen",
        "wood cabinet finish grain detail",
        "soft close cabinet hinge detail",
    ],
}


_token_cache = {}

def _get_token():
    """Refresh SHEETS_TOKEN refresh credential into a live access token.

    SHEETS_TOKEN is the refresh credential JSON (client_id/client_secret/refresh_token),
    not a live access token. The previous version returned the empty access_token field
    and silently dropped every catalog read, sending the pipeline to DALL-E fallback.
    """
    if _token_cache.get("t") and time.time() < _token_cache.get("exp", 0):
        return _token_cache["t"]
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        return ""
    try:
        td = json.loads(raw)
        data = urllib.parse.urlencode({
            "client_id": td["client_id"],
            "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data),
            timeout=10).read())
        _token_cache["t"] = resp["access_token"]
        _token_cache["exp"] = time.time() + resp.get("expires_in", 3500) - 60
        return resp["access_token"]
    except Exception as e:
        print(f"  photo_matcher: SHEETS_TOKEN refresh failed — {e}")
        return ""


def _read_catalog(token):
    import urllib.parse
    enc = urllib.parse.quote(f"'{CATALOG_TAB}'!A:J", safe="!:'")
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
           f"/values/{enc}?majorDimension=ROWS")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
    rows = resp.get("values", [])
    if not rows:
        return []
    # skip header row
    return rows[1:]


def _score(row, keywords, phase_filter=None, service_filter=None):
    """Return a match score (higher = better). 0 = disqualified."""
    if len(row) < 10:
        return 0
    _, project, service, filename, drive_url, description, phase, quality_raw, _, used = row[:10]

    if used.strip().lower() in ("yes", "true", "1"):
        return 0
    try:
        quality = int(float(quality_raw.strip().replace("⭐", "").strip() or "0"))
    except (ValueError, AttributeError):
        quality = 0
    if quality < MIN_QUALITY:
        return 0
    if phase_filter and phase.strip().lower() != phase_filter.lower():
        return 0
    if service_filter and service_filter.lower() not in service.strip().lower():
        return 0
    if not drive_url.strip().startswith("http"):
        return 0

    haystack = f"{service} {description} {filename} {project}".lower()
    hits = sum(1 for kw in keywords if kw.lower() in haystack)
    return hits * 10 + quality  # quality breaks ties


def match_opc_photo(topic, phase=None, service_type=None):
    """Return (drive_url, description, service_type) for the best catalog match.

    Args:
        topic: the carousel topic text (e.g. "kitchen cabinet painting tips")
        phase: optional "before" | "during" | "after" filter
        service_type: optional service filter (e.g. "Kitchens")

    Returns:
        dict with keys drive_url, description, service_type, quality — or None if no match.
    """
    token = _get_token()
    if not token:
        print("  photo_matcher: no SHEETS_TOKEN — skipping real-photo match")
        return None

    try:
        rows = _read_catalog(token)
    except Exception as e:
        print(f"  photo_matcher: catalog read failed — {e}")
        return None

    # Extract meaningful keywords from topic (drop common stop words)
    stop = {"the", "a", "an", "of", "for", "to", "in", "on", "with", "and", "or",
            "is", "are", "how", "why", "what", "when", "your", "our", "their"}
    keywords = [w for w in re.sub(r"[^\w\s]", " ", topic.lower()).split() if w not in stop and len(w) > 2]

    best_score = 0
    best_row = None
    for row in rows:
        score = _score(row, keywords, phase_filter=phase, service_filter=service_type)
        if score > best_score:
            best_score = score
            best_row = row

    if not best_row or best_score == 0:
        print(f"  photo_matcher: no match for '{topic[:50]}' (min_quality={MIN_QUALITY})")
        return None

    _, project, svc, filename, drive_url, description, phase_val, quality_raw, _, _ = best_row[:10]
    result = {
        "drive_url": drive_url.strip(),
        "description": description.strip(),
        "service_type": svc.strip(),
        "phase": phase_val.strip(),
        "quality": quality_raw.strip(),
        "filename": filename.strip(),
    }
    print(f"  photo_matcher: matched '{filename}' ({svc}, q={quality_raw}, score={best_score})")
    return result


def match_before_after_pair(topic):
    """Return (before_url, after_url) from the same project/service for before-after posts."""
    token = _get_token()
    if not token:
        return None, None
    try:
        rows = _read_catalog(token)
    except Exception:
        return None, None

    stop = {"the", "a", "an", "of", "for", "to", "in", "on", "with", "and", "or",
            "is", "are", "how", "why", "what", "when", "your", "our", "their"}
    keywords = [w for w in re.sub(r"[^\w\s]", " ", topic.lower()).split() if w not in stop and len(w) > 2]

    before_url = after_url = None
    best_before = best_after = 0

    for row in rows:
        score = _score(row, keywords)
        if score == 0:
            continue
        phase = row[6].strip().lower() if len(row) > 6 else ""
        url = row[4].strip() if len(row) > 4 else ""
        if "before" in phase and score > best_before:
            best_before = score
            before_url = url
        elif "after" in phase and score > best_after:
            best_after = score
            after_url = url

    if before_url or after_url:
        print(f"  photo_matcher: before/after pair found (scores: before={best_before}, after={best_after})")
    return before_url, after_url
