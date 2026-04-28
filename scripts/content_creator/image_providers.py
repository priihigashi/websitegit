#!/usr/bin/env python3
"""
image_providers.py — Shared image fetching + generation for the carousel pipeline.

Imported by: carousel_builder.py, fix_existing_images.py, carousel_reviewer.py

Real-photo cascade (always tried first — free, no hallucination):
  Wikimedia Commons CC → Pexels → Pixabay

AI generation cascade (fallback when real photos fail):
  1. NB2       — inference.sh, Gemini 3.1 Flash Image Preview (PRI_OP_INFSH_API_KEY)
  2. Seedream 4.5 — Replicate, ByteDance (PRI_OP_REPLICATE_API_KEY, ~$0.003/img)
  3. DALL-E 3  — OpenAI (OPENAI_API_KEY, $0.04/img)
  4. Seedream 5.0 — Replicate, ByteDance lite (same key, ~$0.003/img)
  5. Gemini Imagen 4 — direct Google API (GEMINI_API_KEY, needs paid plan)
  6. SDXL      — Replicate last resort (same key, ~$0.002/img)

CLI flag (passed through from caller):
  --provider nb2 | seedream-4.5 | dall-e-3 | seedream-5.0 | gemini | sdxl
  Without flag → full cascade auto-fallthrough.

File naming convention:
  {content_word}_{provider_slug}_slide{N}.png
  e.g. driveway_nb2_slide2.png  |  kitchen_seedream45_slide3.png
"""
import base64, json, os, re, time, urllib.request, urllib.parse
from pathlib import Path
from typing import Optional, Tuple

# ── Env vars ──────────────────────────────────────────────────────────────────
INFSH_KEY     = os.environ.get("PRI_OP_INFSH_API_KEY", "")
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY    = os.environ.get("GEMINI_API_KEY", "")
PEXELS_KEY    = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY   = os.environ.get("PIXABAY_API_KEY", "")
REPLICATE_KEY = os.environ.get("PRI_OP_REPLICATE_API_KEY", "")

# ── Provider name constants ───────────────────────────────────────────────────
PROVIDER_NB2        = "nb2"
PROVIDER_SEEDREAM45 = "seedream-4.5"
PROVIDER_DALLE3     = "dall-e-3"
PROVIDER_SEEDREAM50 = "seedream-5.0"
PROVIDER_GEMINI     = "gemini"
PROVIDER_SDXL       = "sdxl"

DEFAULT_AI_CASCADE = [
    PROVIDER_NB2,
    PROVIDER_SEEDREAM45,
    PROVIDER_DALLE3,
    PROVIDER_SEEDREAM50,
    PROVIDER_GEMINI,
    PROVIDER_SDXL,
]

# Words too generic to use as the content word in a filename
_SKIP_WORDS = {
    "a", "an", "the", "of", "in", "at", "for", "and", "or", "is", "are",
    "was", "were", "with", "from", "this", "that", "photo", "image", "view",
    "scene", "picture", "construction", "house", "home", "building",
    "renovation", "contractor", "outdoor", "indoor", "work", "project",
    "general", "real", "new", "old", "large", "small", "high", "low",
}


# ── Filename utilities ────────────────────────────────────────────────────────

def _content_word(query: str) -> str:
    """Extract 1–2 meaningful nouns from a query to use in the filename."""
    words = re.sub(r"[^a-z0-9\s]", "", query.lower()).split()
    meaningful = [w for w in words if w not in _SKIP_WORDS and len(w) > 3]
    return "_".join(meaningful[:2]) if meaningful else "image"


def make_filename(query: str, provider: str, slide_num) -> str:
    """Build a filename like driveway_nb2_slide2.png."""
    word = _content_word(query)
    prov = re.sub(r"[^a-z0-9]", "", provider.lower())   # dall-e-3 → dalle3
    return f"{word}_{prov}_slide{slide_num}.png"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _dest(work_dir: str, filename: str) -> Path:
    p = Path(work_dir) / "resources" / "images" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _cached(dest: Path) -> bool:
    return dest.exists() and dest.stat().st_size > 5000


def _rel(filename: str) -> str:
    return f"resources/images/{filename}"


def _replicate_run(slug_or_version: str, input_dict: dict,
                   work_dir: str, filename: str,
                   timeout: int = 120, label: str = "") -> str:
    """Shared Replicate helper — create prediction, poll, download output[0]."""
    if not REPLICATE_KEY:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        if "/" in slug_or_version and len(slug_or_version) < 80:
            api_url = f"https://api.replicate.com/v1/models/{slug_or_version}/predictions"
            payload = json.dumps({"input": input_dict}).encode()
        else:
            api_url = "https://api.replicate.com/v1/predictions"
            payload = json.dumps({"version": slug_or_version, "input": input_dict}).encode()
        req = urllib.request.Request(
            api_url, data=payload,
            headers={"Authorization": f"Bearer {REPLICATE_KEY}",
                     "Content-Type": "application/json",
                     "Prefer": "wait=60"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        status = resp.get("status", "")
        pid = resp.get("id", "")
        started = time.time()
        while status in ("starting", "processing") and (time.time() - started) < timeout:
            time.sleep(2)
            poll = urllib.request.Request(
                f"https://api.replicate.com/v1/predictions/{pid}",
                headers={"Authorization": f"Bearer {REPLICATE_KEY}"},
            )
            resp = json.loads(urllib.request.urlopen(poll, timeout=15).read())
            status = resp.get("status", "")
        if status != "succeeded":
            print(f"  Replicate {label} status={status} (non-fatal)")
            return ""
        output = resp.get("output")
        if isinstance(output, list):
            output = output[0] if output else ""
        if not output:
            return ""
        with urllib.request.urlopen(output, timeout=30) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest.write_bytes(raw)
        print(f"  {filename} ({len(raw)//1024}KB) ← {label}")
        return _rel(filename)
    except Exception as e:
        print(f"  Replicate {label} failed (non-fatal): {e}")
        return ""


# ── Real-photo providers ──────────────────────────────────────────────────────

def fetch_wikimedia(query: str, work_dir: str, filename: str) -> str:
    """Search Wikimedia Commons for a CC-licensed photo. Returns rel path or ''."""
    if not query:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        # Wikipedia REST thumbnail first (fast for named subjects)
        wiki_name = urllib.parse.quote(query.replace(" ", "_"))
        thumb_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_name}"
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "carousel-builder/1.0"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=8).read())
            img_url = (data.get("thumbnail") or {}).get("source", "")
            if img_url:
                with urllib.request.urlopen(img_url, timeout=20) as r:
                    raw = r.read()
                if len(raw) > 5000:
                    dest.write_bytes(raw)
                    print(f"  {filename} ← Wikipedia REST '{query[:50]}'")
                    return _rel(filename)
        except Exception:
            pass

        # Wikimedia Commons search fallback
        q = urllib.parse.quote_plus(query[:100])
        search_url = (
            f"https://commons.wikimedia.org/w/api.php?action=query&list=search"
            f"&srsearch={q}&srnamespace=6&srlimit=5&format=json"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "carousel-builder/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        results = data.get("query", {}).get("search", [])
        for result in results:
            title = result.get("title", "")
            if not any(title.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                continue
            info_url = (
                f"https://commons.wikimedia.org/w/api.php?action=query"
                f"&titles={urllib.parse.quote(title)}&prop=imageinfo"
                f"&iiprop=url&iiurlwidth=1080&format=json"
            )
            req2 = urllib.request.Request(info_url, headers={"User-Agent": "carousel-builder/1.0"})
            info = json.loads(urllib.request.urlopen(req2, timeout=10).read())
            for page in info.get("query", {}).get("pages", {}).values():
                img_url = (page.get("imageinfo") or [{}])[0].get("thumburl", "")
                if not img_url:
                    continue
                with urllib.request.urlopen(img_url, timeout=20) as r:
                    raw = r.read()
                if len(raw) > 5000:
                    dest.write_bytes(raw)
                    print(f"  {filename} ← Wikimedia Commons '{query[:50]}'")
                    return _rel(filename)
        return ""
    except Exception as e:
        print(f"  Wikimedia fetch failed (non-fatal): {e}")
        return ""


def fetch_pexels(query: str, work_dir: str, filename: str) -> str:
    """Search Pexels for a royalty-free portrait photo. Returns rel path or ''."""
    if not PEXELS_KEY or not query:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        q = urllib.parse.quote_plus(query[:100])
        url = f"https://api.pexels.com/v1/search?query={q}&per_page=3&orientation=portrait"
        req = urllib.request.Request(url, headers={"Authorization": PEXELS_KEY})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        photos = data.get("photos", [])
        if not photos:
            return ""
        img_url = photos[0]["src"]["large"]
        with urllib.request.urlopen(img_url, timeout=20) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest.write_bytes(raw)
        print(f"  {filename} ← Pexels '{query[:50]}'")
        return _rel(filename)
    except Exception as e:
        print(f"  Pexels fetch failed (non-fatal): {e}")
        return ""


def fetch_pixabay(query: str, work_dir: str, filename: str) -> str:
    """Search Pixabay for a royalty-free vertical photo. Returns rel path or ''."""
    if not PIXABAY_KEY or not query:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        q = urllib.parse.quote_plus(query[:100])
        url = (
            f"https://pixabay.com/api/?key={PIXABAY_KEY}&q={q}"
            f"&image_type=photo&orientation=vertical&per_page=3&safesearch=true"
        )
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
        hits = data.get("hits", [])
        if not hits:
            return ""
        img_url = hits[0].get("largeImageURL") or hits[0].get("webformatURL", "")
        if not img_url:
            return ""
        with urllib.request.urlopen(img_url, timeout=20) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest.write_bytes(raw)
        print(f"  {filename} ← Pixabay '{query[:50]}'")
        return _rel(filename)
    except Exception as e:
        print(f"  Pixabay fetch failed (non-fatal): {e}")
        return ""


def fetch_real_photo(query: str, work_dir: str, filename: str) -> Tuple[str, str]:
    """Try Wikimedia → Pexels → Pixabay. Returns (rel_path, provider) or ('', '')."""
    path = fetch_wikimedia(query, work_dir, filename)
    if path:
        return path, "wikimedia"
    path = fetch_pexels(query, work_dir, filename)
    if path:
        return path, "pexels"
    path = fetch_pixabay(query, work_dir, filename)
    if path:
        return path, "pixabay"
    return "", ""


# ── AI providers ──────────────────────────────────────────────────────────────

def _nb2(prompt: str, work_dir: str, filename: str) -> str:
    """NB2 via inference.sh — Gemini 3.1 Flash Image Preview."""
    if not INFSH_KEY or not prompt:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        from inferencesh import Inferencesh
        client = Inferencesh(api_key=INFSH_KEY)
        result = client.run({
            "app": "google/gemini-3-1-flash-image-preview@0c7ma1ex",
            "input": {"prompt": prompt[:1000]},
        })
        output = result.get("output") if isinstance(result, dict) else getattr(result, "output", None)
        if isinstance(output, list):
            output = output[0] if output else None
        if not output:
            return ""
        with urllib.request.urlopen(str(output), timeout=30) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest.write_bytes(raw)
        print(f"  {filename} ({len(raw)//1024}KB) ← NB2/Gemini 3.1 Flash")
        return _rel(filename)
    except Exception as e:
        print(f"  NB2 failed (non-fatal): {e}")
        return ""


def _seedream45(prompt: str, work_dir: str, filename: str) -> str:
    return _replicate_run(
        "bytedance/seedream-4.5",
        {"prompt": prompt[:1000], "aspect_ratio": "4:5"},
        work_dir, filename, timeout=120, label="Seedream 4.5",
    )


def _dalle3(prompt: str, work_dir: str, filename: str) -> str:
    if not OPENAI_KEY or not prompt:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        payload = json.dumps({
            "model": "dall-e-3", "prompt": prompt[:1000],
            "n": 1, "size": "1024x1024", "quality": "standard",
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations", data=payload,
            headers={"Authorization": f"Bearer {OPENAI_KEY}",
                     "Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        img_url = resp["data"][0]["url"]
        with urllib.request.urlopen(img_url, timeout=30) as r:
            raw = r.read()
        if len(raw) < 5000:
            return ""
        dest.write_bytes(raw)
        print(f"  {filename} ({len(raw)//1024}KB) ← DALL-E 3")
        return _rel(filename)
    except Exception as e:
        print(f"  DALL-E 3 failed (non-fatal): {e}")
        return ""


def _seedream50(prompt: str, work_dir: str, filename: str) -> str:
    return _replicate_run(
        "bytedance/seedream-5",
        {"prompt": prompt[:1000], "aspect_ratio": "4:5"},
        work_dir, filename, timeout=120, label="Seedream 5.0",
    )


def _gemini_imagen(prompt: str, work_dir: str, filename: str) -> str:
    """Gemini Imagen 4 — requires paid Google AI plan."""
    if not GEMINI_KEY or not prompt:
        return ""
    dest = _dest(work_dir, filename)
    if _cached(dest):
        return _rel(filename)
    try:
        payload = json.dumps({
            "instances": [{"prompt": prompt[:1000]}],
            "parameters": {"sampleCount": 1, "aspectRatio": "4:5",
                           "outputMimeType": "image/jpeg"},
        }).encode()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/imagen-4.0-generate-001:predict?key={GEMINI_KEY}"
        )
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        b64 = resp.get("predictions", [{}])[0].get("bytesBase64Encoded", "")
        if not b64:
            return ""
        raw = base64.b64decode(b64)
        if len(raw) < 5000:
            return ""
        dest.write_bytes(raw)
        print(f"  {filename} ({len(raw)//1024}KB) ← Gemini Imagen 4")
        return _rel(filename)
    except Exception as e:
        print(f"  Gemini Imagen failed (non-fatal): {e}")
        return ""


def _sdxl(prompt: str, work_dir: str, filename: str) -> str:
    return _replicate_run(
        "7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc",
        {"prompt": prompt[:1000], "width": 864, "height": 1080,
         "num_inference_steps": 25, "guidance_scale": 7.5},
        work_dir, filename, timeout=180, label="SDXL",
    )


_AI_PROVIDER_FN = {
    PROVIDER_NB2:        _nb2,
    PROVIDER_SEEDREAM45: _seedream45,
    PROVIDER_DALLE3:     _dalle3,
    PROVIDER_SEEDREAM50: _seedream50,
    PROVIDER_GEMINI:     _gemini_imagen,
    PROVIDER_SDXL:       _sdxl,
}


def generate_ai_image(
    prompt: str,
    work_dir: str,
    filename: str,
    provider: Optional[str] = None,
) -> Tuple[str, str]:
    """Run the AI cascade (or a single pinned provider).
    Returns (rel_path, provider_used) or ('', '')."""
    cascade = [provider] if provider else DEFAULT_AI_CASCADE
    for prov in cascade:
        fn = _AI_PROVIDER_FN.get(prov)
        if not fn:
            print(f"  Unknown provider '{prov}', skipping")
            continue
        result = fn(prompt, work_dir, filename)
        if result:
            return result, prov
    return "", ""


# ── Full cascade: real photos first, AI fallback ──────────────────────────────

def fetch_image(
    prompt: str,
    query: str,
    slide_num,
    work_dir: str = ".",
    provider: Optional[str] = None,
    subject_type: str = "place",
) -> Tuple[str, str, str]:
    """Full image fetch for one slide slot.

    Args:
        prompt:       AI generation prompt (used only if real photos fail)
        query:        Real-photo search query (Wikimedia / Pexels / Pixabay)
        slide_num:    Slide number (used in filename)
        work_dir:     Local working directory
        provider:     Pin to one AI provider; None = full cascade
        subject_type: 'person' skips stock + AI (bio-card rule); 'place'/'event' = normal

    Returns:
        (rel_path, provider_used, source_type)
        source_type: 'cc' | 'stock' | 'ai' | ''
    """
    prov_slug = provider or DEFAULT_AI_CASCADE[0]
    filename = make_filename(query or prompt[:40], prov_slug, slide_num)

    # Real-photo tiers — always free, no hallucination
    if query:
        real_path, real_prov = fetch_real_photo(query, work_dir, filename)
        if real_path:
            src_type = "cc" if real_prov == "wikimedia" else "stock"
            return real_path, real_prov, src_type

    # Named-person rule: never AI-generate a face
    if subject_type == "person":
        return "", "", ""

    # AI cascade
    if prompt:
        ai_path, ai_prov = generate_ai_image(prompt, work_dir, filename, provider)
        if ai_path:
            return ai_path, ai_prov, "ai"

    return "", "", ""
