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


# Phase 10 — category mismatch penalty. If a topic is clearly STRUCTURAL
# (concrete, CMU, rebar, foundation, formwork, slab, footing, drainage,
# waterproofing) but the catalog photo's service category is interior
# (Kitchens, Bathrooms, Cabinets, Tile, Countertops, Flooring), heavily
# penalize the match. Same for the reverse — kitchen topics shouldn't grab
# foundation/exterior photos.
STRUCTURAL_TOPIC_TOKENS = {
    "concrete", "cmu", "rebar", "foundation", "formwork", "slab",
    "footing", "drainage", "waterproof", "block wall", "masonry",
    "stem wall", "tie beam", "tilt-up", "post-tension",
    # hardscape / exterior surfaces
    "driveway", "asphalt", "pavers", "paver", "hardscape", "sidewalk",
    "curb", "pool deck", "patio", "pergola", "fence", "roof", "roofing",
    "shingle", "shingles", "window", "windows", "door", "siding", "stucco",
    "exterior", "outdoor",
}
INTERIOR_TOPIC_TOKENS = {
    "kitchen", "cabinet", "countertop", "tile", "shower", "tub",
    "vanity", "backsplash", "bathroom", "flooring", "epoxy",
    "lighting", "fixture", "faucet",
}
STRUCTURAL_SERVICES = {
    "foundations", "concrete", "exterior", "drainage", "outdoor",
    "site work", "structural", "masonry",
    "roofing", "windows", "doors", "hardscape", "paving", "pool",
}
INTERIOR_SERVICES = {
    "kitchens", "kitchen", "bathrooms", "bathroom", "cabinets",
    "tile", "countertops", "flooring", "interiors",
}
# Hard-reject threshold: if top match score is zero keyword hits (quality-only match)
# AND it's an interior service for a structural/exterior topic, reject entirely.
# Prevents kitchen photos appearing on driveway/roof posts.
_HARD_REJECT_MISMATCH = True


def _topic_buckets(topic):
    """Classify topic text into 'structural', 'interior', or None."""
    t = (topic or "").lower()
    if any(tok in t for tok in STRUCTURAL_TOPIC_TOKENS):
        return "structural"
    if any(tok in t for tok in INTERIOR_TOPIC_TOKENS):
        return "interior"
    return None


def _service_bucket(service):
    s = (service or "").lower()
    if any(tok in s for tok in STRUCTURAL_SERVICES):
        return "structural"
    if any(tok in s for tok in INTERIOR_SERVICES):
        return "interior"
    return None


def _category_penalty(topic, service):
    """Return penalty multiplier in [0.0, 1.0]. 1.0 = no penalty, 0.0 = blocked."""
    tb = _topic_buckets(topic)
    sb = _service_bucket(service)
    if tb is None or sb is None:
        return 1.0  # cannot classify either side — don't penalize
    if tb == sb:
        return 1.2  # bonus for category match
    # Clear mismatch: structural topic + interior photo (or reverse).
    return 0.05  # near-zero — only used if literally nothing else scores positive


def _score(row, keywords, phase_filter=None, service_filter=None, topic_text=""):
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
    raw = hits * 10 + quality  # quality breaks ties
    # Phase 10 — category-mismatch penalty using the topic text (not just kws).
    penalty = _category_penalty(topic_text or " ".join(keywords), service)
    return int(raw * penalty)


def match_opc_photo_candidates(topic, phase=None, service_type=None, exclude_keys=None, limit=3, fallback_topic=None):
    """Return top-N catalog candidates for the topic, ordered by score (best first).

    Phase 1+2 (per-build dedup + try-next-on-reject):
      - exclude_keys: set of filename (lowercased) and Drive URL strings already
        used in this carousel. Rows matching either are skipped.
      - limit: max candidates to return.
      - fallback_topic: SH-151 — used when `topic` is blank/empty so we can still
        classify the post bucket. Without this, an empty topic disables the
        category hard-reject and we get kitchen photos on driveway covers.

    Returns: list of dicts (same shape as match_opc_photo). Empty list if none.
    """
    if (not topic or not str(topic).strip()) and fallback_topic:
        print(f"  photo_matcher: SH-151 — empty topic, using fallback_topic='{str(fallback_topic)[:60]}'")
        topic = fallback_topic
    elif not topic or not str(topic).strip():
        print("  photo_matcher: SH-151 — empty topic AND no fallback_topic — refusing to match (would risk category mismatch)")
        return []
    token = _get_token()
    if not token:
        print("  photo_matcher: no SHEETS_TOKEN — skipping real-photo match")
        return []

    try:
        rows = _read_catalog(token)
    except Exception as e:
        print(f"  photo_matcher: catalog read failed — {e}")
        return []

    excl = set()
    for k in (exclude_keys or set()):
        if isinstance(k, str) and k:
            excl.add(k.strip().lower())

    stop = {"the", "a", "an", "of", "for", "to", "in", "on", "with", "and", "or",
            "is", "are", "how", "why", "what", "when", "your", "our", "their"}
    keywords = [w for w in re.sub(r"[^\w\s]", " ", topic.lower()).split() if w not in stop and len(w) > 2]

    scored = []
    for row in rows:
        # Skip rows already used in this build (filename or Drive URL match).
        try:
            fname_key = (row[3] or "").strip().lower()
            drive_key = (row[4] or "").strip().lower()
        except Exception:
            fname_key, drive_key = "", ""
        if (fname_key and fname_key in excl) or (drive_key and drive_key in excl):
            continue

        score = _score(row, keywords, phase_filter=phase, service_filter=service_type, topic_text=topic)
        if score > 0:
            scored.append((score, row))

    if not scored:
        print(f"  photo_matcher: no match for '{topic[:50]}' (min_quality={MIN_QUALITY}, excluded={len(excl)})")
        return []

    scored.sort(key=lambda t: t[0], reverse=True)

    # Hard-reject: prevent interior photos (Kitchens/Bathrooms) showing up on
    # structural/exterior posts (driveway, roof, hardscape). Two trigger paths:
    #   (a) explicit mismatch — topic bucket structural, top photo interior (or reverse)
    #   (b) SH-152 — topic is structural and top photo is interior, even with zero
    #       keyword hits, regardless of how _topic_buckets classified the empty/short topic.
    if _HARD_REJECT_MISMATCH and scored:
        top_score, top_row = scored[0]
        top_svc = (top_row[2] if len(top_row) > 2 else "").strip()
        top_sb = _service_bucket(top_svc)
        tb = _topic_buckets(topic)
        # Path (a): clear cross-bucket mismatch with no keyword hits
        if (
            tb is not None and top_sb is not None and tb != top_sb
            and not keywords
        ):
            print(
                f"  photo_matcher: rejected all {len(scored)} candidate(s) for '{topic[:50]}' "
                f"— topic bucket '{tb}' mismatches top service '{top_svc}' (bucket '{top_sb}'). "
                "Letting image_providers.py fetch a topic-matched stock image instead."
            )
            return []
        # Path (b): structural topic + interior photo, regardless of keyword count
        if tb == "structural" and top_sb == "interior":
            print(
                f"  photo_matcher: SH-152 — rejected {len(scored)} candidate(s) for '{topic[:50]}' "
                f"— structural topic, top service '{top_svc}' is interior. Falling back to stock."
            )
            return []

    out = []
    for score, row in scored[:max(1, int(limit))]:
        _, project, svc, filename, drive_url, description, phase_val, quality_raw, _, _ = row[:10]
        out.append({
            "drive_url": drive_url.strip(),
            "description": description.strip(),
            "service_type": svc.strip(),
            "phase": phase_val.strip(),
            "quality": quality_raw.strip(),
            "filename": filename.strip(),
            "_score": score,
        })
    print(f"  photo_matcher: {len(out)} candidate(s) for '{topic[:50]}' "
          f"(top filename='{out[0]['filename']}', top score={out[0]['_score']}, excluded={len(excl)})")
    return out


def match_opc_photo(topic, phase=None, service_type=None, exclude_keys=None, fallback_topic=None):
    """Compatibility wrapper — returns the single best candidate or None.

    New callers should prefer match_opc_photo_candidates() so they can iterate
    when Vision rejects the top match.
    fallback_topic: SH-151 — used when `topic` is blank so the category guard
    has something to classify on.
    """
    cands = match_opc_photo_candidates(topic, phase=phase, service_type=service_type,
                                       exclude_keys=exclude_keys, limit=1,
                                       fallback_topic=fallback_topic)
    return cands[0] if cands else None


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
        score = _score(row, keywords, topic_text=topic)
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


# ── SH-037: Catalog description audit ────────────────────────────────────────

# Patterns considered generic/stale descriptions — not useful for matching.
_STALE_DESCRIPTION_PATTERNS = re.compile(
    r"^(image|photo|picture|img|dsc|screenshot|file|untitled|none|n/a|na|"
    r"img[_\s]?\d+|dsc[_\s]?\d+|pic[_\s]?\d+|photo[_\s]?\d+)$",
    re.IGNORECASE,
)


def audit_stale_catalog_rows(sheet_service, spreadsheet_id, tab_name):
    """Read catalog rows and identify those with missing or generic descriptions.

    A row is stale if its Description column (col F, index 5) is:
      - blank / whitespace only
      - a generic placeholder like "image", "photo", "IMG_1234", "DSC_5678"

    Args:
        sheet_service: a Google Sheets API service object (unused if we call
                       the internal _read_catalog helper directly). Pass None
                       to use the module-level token+sheet.
        spreadsheet_id: the spreadsheet ID to read from (defaults to SHEET_ID).
        tab_name: the tab name to read from (defaults to CATALOG_TAB).

    Returns:
        list of dicts with keys: row_index (1-based, including header),
        filename, description, drive_url.
        Empty list if catalog is unreachable or all rows are fine.

    NOTE: Does NOT delete or modify any rows — read-only audit.
    """
    token = _get_token()
    if not token:
        print("  photo_matcher.audit_stale_catalog_rows: no token — cannot read catalog")
        return []

    # Allow callers to override the target sheet/tab via args
    sid = spreadsheet_id or SHEET_ID
    tname = tab_name or CATALOG_TAB

    try:
        import urllib.parse as _up
        enc = _up.quote(f"'{tname}'!A:J", safe="!:'")
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
               f"/values/{enc}?majorDimension=ROWS")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        rows = resp.get("values", [])
    except Exception as e:
        print(f"  audit_stale_catalog_rows: catalog read failed — {e}")
        return []

    if not rows:
        return []

    stale = []
    # Row 0 is header; data rows start at index 1 (row_index 2 in Sheets 1-based)
    for i, row in enumerate(rows[1:], start=2):
        # Pad row to at least 6 columns so index access is safe
        padded = row + [""] * max(0, 6 - len(row))
        filename = padded[3].strip()    # col D
        drive_url = padded[4].strip()   # col E
        description = padded[5].strip() # col F (AI Description)

        # Blank description
        if not description:
            stale.append({
                "row_index": i,
                "filename": filename,
                "description": description,
                "drive_url": drive_url,
                "reason": "blank",
            })
            continue

        # Generic placeholder pattern
        if _STALE_DESCRIPTION_PATTERNS.match(description):
            stale.append({
                "row_index": i,
                "filename": filename,
                "description": description,
                "drive_url": drive_url,
                "reason": "generic_placeholder",
            })

    print(f"  audit_stale_catalog_rows: {len(stale)} stale row(s) out of {len(rows)-1} total")
    return stale


def _extract_drive_file_id(url: str) -> str:
    """Extract Drive file ID from a Drive URL (file/d/ID/ or id=ID form)."""
    m = re.search(r"/file/d/([a-zA-Z0-9_-]{10,})", url)
    if m:
        return m.group(1)
    m2 = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", url)
    if m2:
        return m2.group(1)
    return ""


def _describe_image_haiku(file_id: str) -> str:
    """Download image from Drive + call Claude Haiku Vision for a concise description.
    Returns description string or '' on any failure. Non-fatal.
    """
    if not file_id:
        return ""
    anthropic_key = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    sheets_token_raw = os.environ.get("SHEETS_TOKEN", "")
    if not anthropic_key or not sheets_token_raw:
        return ""
    try:
        import io, base64
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload

        td = json.loads(sheets_token_raw)
        creds = Credentials(
            token=td.get("token") or td.get("access_token"),
            refresh_token=td.get("refresh_token"),
            token_uri=td.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=td.get("client_id"), client_secret=td.get("client_secret"),
            scopes=td.get("scopes") or ["https://www.googleapis.com/auth/drive.readonly"],
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        drive = build("drive", "v3", credentials=creds)

        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, drive.files().get_media(
            fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        raw = buf.getvalue()
        if len(raw) < 5000:
            return ""

        # Resize to stay within Anthropic's 5 MB limit
        try:
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(raw)).convert("RGB")
            img.thumbnail((1200, 1200), PILImage.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)
            raw = out.getvalue()
        except Exception:
            pass  # use raw bytes as-is

        img_b64 = base64.b64encode(raw).decode()
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001", "max_tokens": 200,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": (
                    "Describe this construction/renovation photo in 1-2 sentences. "
                    "Be specific: room type, materials visible, work phase (before/during/after), "
                    "key visual details. No markdown, no intro phrase."
                )},
            ]}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=payload,
            headers={"x-api-key": anthropic_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return resp["content"][0]["text"].strip()
    except Exception as exc:
        print(f"  _describe_image_haiku failed (non-fatal): {exc}")
        return ""


def batch_retag_stale_rows(rows, vision_client=None):
    """Re-generate descriptions for stale catalog rows using Claude Haiku Vision.

    Args:
        rows: list of dicts returned by audit_stale_catalog_rows().
        vision_client: unused legacy param (kept for API compatibility).

    Returns:
        list of row_index values successfully re-tagged.
    """
    if not rows:
        print("  batch_retag_stale_rows: no stale rows to retag")
        return []

    anthropic_key = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
    sheets_token = os.environ.get("SHEETS_TOKEN", "")
    if not anthropic_key or not sheets_token:
        print(
            f"  batch_retag_stale_rows: CLAUDE_KEY_4_CONTENT or SHEETS_TOKEN missing — "
            f"logging {len(rows)} stale row(s), skipping re-tag"
        )
        for r in rows:
            print(f"    row {r['row_index']:>4}: {r['filename'][:50]!r:50s} reason={r['reason']}")
        return []

    token = _get_token()
    if not token:
        print("  batch_retag_stale_rows: no Sheets token — cannot write descriptions")
        return []

    tagged = []
    for r in rows:
        file_id = _extract_drive_file_id(r.get("drive_url", ""))
        if not file_id:
            print(f"  batch_retag: row {r['row_index']} — no Drive file ID in URL, skipping")
            continue

        desc = _describe_image_haiku(file_id)
        if not desc:
            print(f"  batch_retag: row {r['row_index']} — description generation failed, skipping")
            continue

        # Write description to col F (index 5, column letter F)
        try:
            cell = f"'📸 Photo Catalog'!F{r['row_index']}"
            body = json.dumps({
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": cell, "values": [[desc]]}],
            }).encode()
            url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
                   f"/values:batchUpdate")
            req = urllib.request.Request(url, data=body, method="POST",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
            tagged.append(r["row_index"])
            print(f"  batch_retag: row {r['row_index']} updated — {desc[:80]!r}")
        except Exception as exc:
            print(f"  batch_retag: row {r['row_index']} write failed (non-fatal): {exc}")
        time.sleep(1)  # rate-limit: ~1 req/s to Anthropic

    print(f"  batch_retag_stale_rows: {len(tagged)}/{len(rows)} rows re-tagged")
    return tagged


# ── SH-040: Image relevance validator ────────────────────────────────────────

# Known bad URL patterns — images at these hosts or with these path fragments
# are stock-watermark or placeholder images and should be rejected immediately.
_BAD_URL_PATTERNS = [
    "watermark",
    "placeholder",
    "default",
    "no-image",
    "noimage",
    "blank",
    "sample",
    "dummy",
    "lorem",
    "gettyimages.com",
    "istockphoto.com",
    "shutterstock.com",  # direct CDN URLs often watermarked
]

# Small-dimension patterns in URL: e.g. /100x100/, ?w=50, _200x200_
_SMALL_DIM_RE = re.compile(
    r"[/_](\d{1,3})x(\d{1,3})[/_?]"
)


def _url_looks_watermarked_or_tiny(url: str) -> bool:
    """Return True if the URL strongly suggests a watermarked or tiny placeholder."""
    low = url.lower()
    if any(pat in low for pat in _BAD_URL_PATTERNS):
        return True
    m = _SMALL_DIM_RE.search(url)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w < 200 or h < 200:
            return True
    return False


def validate_image_relevance(image_url: str, query: str, topic: str) -> bool:
    """Validate that a sourced image is worth using before returning it to the pipeline.

    Performs checks in escalating cost order:
      1. URL heuristic — rejects known bad patterns (watermark domains, tiny dimensions,
         placeholder filenames). Zero network cost.
      2. If VISION_API_KEY env var is set, calls Google Cloud Vision API to confirm
         relevance score ≥ 0.6 using label annotations matched against query keywords.
         Falls back to True (accept) if Vision API call fails, so this is never blocking.

    Args:
        image_url: the URL or local file path of the image to validate.
        query:     the search query / description used to source this image.
        topic:     the broader carousel topic (used for Vision keyword matching).

    Returns:
        True  → accept (use this image)
        False → reject (skip to next tier)
    """
    if not image_url:
        return False

    # Step 1: URL/filename heuristic — zero cost
    if _url_looks_watermarked_or_tiny(image_url):
        print(f"  validate_image_relevance: REJECT (bad URL pattern) — {image_url[:80]}")
        return False

    # Step 2: Vision API relevance check (only if key is wired)
    vision_key = os.environ.get("VISION_API_KEY", "")
    if not vision_key:
        # No key — accept after passing heuristic
        return True

    try:
        # Determine if image_url is a local path or a remote URL
        is_local = not image_url.startswith("http")
        if is_local:
            import base64
            with open(image_url, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            image_field = {"content": img_b64}
        else:
            image_field = {"source": {"type": "URL", "imageUri": image_url}}

        vision_payload = json.dumps({
            "requests": [{
                "image": image_field,
                "features": [{"type": "LABEL_DETECTION", "maxResults": 10}],
            }]
        }).encode()
        req = urllib.request.Request(
            f"https://vision.googleapis.com/v1/images:annotate?key={vision_key}",
            data=vision_payload,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        labels = resp.get("responses", [{}])[0].get("labelAnnotations", [])

        # Build keyword set from query + topic
        stop = {"the", "a", "an", "of", "for", "to", "in", "on", "with", "and", "or",
                "is", "are", "how", "why", "what", "when", "your", "our", "their"}
        keywords = set(
            w for w in re.sub(r"[^\w\s]", " ", (query + " " + topic).lower()).split()
            if w not in stop and len(w) > 2
        )

        # Check if any Vision label (score ≥ 0.6) matches a keyword
        for label in labels:
            score = label.get("score", 0)
            description = label.get("description", "").lower()
            if score >= 0.6 and any(kw in description or description in kw for kw in keywords):
                print(
                    f"  validate_image_relevance: ACCEPT (Vision match '{label['description']}' "
                    f"score={score:.2f}) — {image_url[:60]}"
                )
                return True

        print(
            f"  validate_image_relevance: REJECT (no Vision label matches query) — "
            f"labels={[l['description'] for l in labels[:5]]} query='{query[:40]}'"
        )
        return False

    except Exception as e:
        # Vision call failed — non-blocking, accept the image
        print(f"  validate_image_relevance: Vision API error (non-fatal, accepting) — {e}")
        return True
