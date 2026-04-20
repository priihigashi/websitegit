"""
Shared LLM + image-gen fallback module for every script in oak-park-ai-hub.

Priscila's rule: no flow ever stops because one provider is out of credit.
Every text task cascades Claude → OpenAI → Gemini.
Every image task cascades Gemini → DALL-E → Replicate.

Each tier, on a quota/billing/auth error, calls _quota_errors.classify_error
so the failure becomes a specific sheet message + an email with the fix URL
(same pattern as the capture pipeline). Non-quota exceptions just log and
fall through to the next tier so the flow keeps moving.

Public API:
    llm_text(prompt, *, model_tier="sonnet"|"haiku", max_tokens, system=None,
             temperature=0.7, context="", url="") -> str
    llm_image(prompt, *, size="1024x1024", context="", url="") -> bytes

Import this from ANY script:
    import sys, pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent / "capture"))
    from _llm_fallback import llm_text, llm_image
"""
from __future__ import annotations

import os
import base64
from typing import Optional

# Re-use the quota classifier + alert emailer from the capture module.
try:
    from _quota_errors import classify_error, send_quota_alert_email
except Exception:
    def classify_error(_t):            return None
    def send_quota_alert_email(*a, **k): pass

# ─── KEYS ─────────────────────────────────────────────────────────────────────
CLAUDE_KEY  = os.environ.get("CLAUDE_KEY_4_CONTENT") or os.environ.get("ANTHROPIC_API_KEY") or ""
OPENAI_KEY  = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "")
REPLICATE_K = os.environ.get("PRI_OP_REPLICATE_API_KEY") or os.environ.get("REPLICATE_API_TOKEN") or ""

# Model mapping — same reasoning strength across tiers.
_MODEL_MAP = {
    "sonnet": {
        "claude": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "gemini": "gemini-1.5-pro",
    },
    "haiku": {
        "claude": "claude-haiku-4-5-20251001",
        "openai": "gpt-4o-mini",
        "gemini": "gemini-1.5-flash",
    },
}


# ─── TEXT CASCADE ─────────────────────────────────────────────────────────────

def _try_claude(prompt: str, *, model: str, max_tokens: int, system: Optional[str], temperature: float) -> str:
    if not CLAUDE_KEY:
        raise RuntimeError("CLAUDE_KEY_4_CONTENT not set")
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    kwargs = {
        "model":       model,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "messages":    [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return resp.content[0].text


def _try_openai(prompt: str, *, model: str, max_tokens: int, system: Optional[str], temperature: float) -> str:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=messages,
    )
    return resp.choices[0].message.content or ""


def _try_gemini(prompt: str, *, model: str, max_tokens: int, system: Optional[str], temperature: float) -> str:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_KEY)
    full_prompt = (system + "\n\n" + prompt) if system else prompt
    m = genai.GenerativeModel(model)
    resp = m.generate_content(
        full_prompt,
        generation_config={
            "temperature":       temperature,
            "max_output_tokens": max_tokens,
        },
    )
    return resp.text or ""


def llm_text(
    prompt: str,
    *,
    model_tier: str = "sonnet",
    max_tokens: int = 2000,
    system: Optional[str] = None,
    temperature: float = 0.7,
    context: str = "",
    url: str = "",
) -> str:
    """Claude → OpenAI → Gemini cascade. Returns the text. Raises if all 3 fail."""
    if model_tier not in _MODEL_MAP:
        model_tier = "sonnet"
    models = _MODEL_MAP[model_tier]
    last_err = None

    # Tier 1 — Claude (primary)
    try:
        out = _try_claude(prompt, model=models["claude"], max_tokens=max_tokens, system=system, temperature=temperature)
        if out and out.strip():
            return out
        raise RuntimeError("Claude returned empty response")
    except Exception as e:
        last_err = e
        err_text = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            print(f"  [llm_text] Claude → {classified['service']}:{classified['type']} — falling back to OpenAI")
            send_quota_alert_email(classified, context=context or "llm_text(Claude)", url=url)
        else:
            print(f"  [llm_text] Claude failed ({err_text}) — falling back to OpenAI")

    # Tier 2 — OpenAI
    try:
        out = _try_openai(prompt, model=models["openai"], max_tokens=max_tokens, system=system, temperature=temperature)
        if out and out.strip():
            return out
        raise RuntimeError("OpenAI returned empty response")
    except Exception as e:
        last_err = e
        err_text = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            print(f"  [llm_text] OpenAI → {classified['service']}:{classified['type']} — falling back to Gemini")
            send_quota_alert_email(classified, context=context or "llm_text(OpenAI)", url=url)
        else:
            print(f"  [llm_text] OpenAI failed ({err_text}) — falling back to Gemini")

    # Tier 3 — Gemini
    try:
        out = _try_gemini(prompt, model=models["gemini"], max_tokens=max_tokens, system=system, temperature=temperature)
        if out and out.strip():
            return out
        raise RuntimeError("Gemini returned empty response")
    except Exception as e:
        last_err = e
        err_text = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            send_quota_alert_email(classified, context=context or "llm_text(Gemini)", url=url)
        print(f"  [llm_text] Gemini failed ({err_text})")

    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}") from last_err


# ─── IMAGE CASCADE ────────────────────────────────────────────────────────────
# Priscila's rule: Gemini FIRST (best quality + free quota), DALL-E 2nd, Replicate 3rd.

def _try_gemini_image(prompt: str, size: str) -> bytes:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_KEY)
    # Gemini 2.x image generation model. Returns inline image data.
    m = genai.GenerativeModel("gemini-2.0-flash-exp-image-generation")
    resp = m.generate_content([prompt])
    for part in resp.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            data = inline.data
            if isinstance(data, str):
                return base64.b64decode(data)
            return data
    raise RuntimeError("Gemini image response contained no inline_data")


def _try_dalle_image(prompt: str, size: str) -> bytes:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    import urllib.request
    client = OpenAI(api_key=OPENAI_KEY)
    resp = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size=size if size in ("1024x1024", "1024x1792", "1792x1024") else "1024x1024",
        quality="standard",
        n=1,
    )
    img_url = resp.data[0].url
    return urllib.request.urlopen(img_url).read()


def _try_replicate_image(prompt: str, size: str) -> bytes:
    if not REPLICATE_K:
        raise RuntimeError("PRI_OP_REPLICATE_API_KEY not set")
    import replicate
    import urllib.request
    os.environ.setdefault("REPLICATE_API_TOKEN", REPLICATE_K)
    client = replicate.Client(api_token=REPLICATE_K)
    # Stable Diffusion XL — cheap, fast, reliable fallback.
    output = client.run(
        "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
        input={"prompt": prompt, "width": 1024, "height": 1024},
    )
    if isinstance(output, list) and output:
        img_url = output[0]
    else:
        img_url = str(output)
    return urllib.request.urlopen(img_url).read()


def llm_image(prompt: str, *, size: str = "1024x1024", context: str = "", url: str = "") -> bytes:
    """Gemini → DALL-E → Replicate cascade. Returns raw image bytes."""
    last_err = None

    # Tier 1 — Gemini (best + free quota)
    try:
        return _try_gemini_image(prompt, size)
    except Exception as e:
        last_err = e
        err_text = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            print(f"  [llm_image] Gemini → {classified['service']}:{classified['type']} — falling back to DALL-E")
            send_quota_alert_email(classified, context=context or "llm_image(Gemini)", url=url)
        else:
            print(f"  [llm_image] Gemini image failed ({err_text}) — falling back to DALL-E")

    # Tier 2 — DALL-E 3
    try:
        return _try_dalle_image(prompt, size)
    except Exception as e:
        last_err = e
        err_text = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            print(f"  [llm_image] DALL-E → {classified['service']}:{classified['type']} — falling back to Replicate")
            send_quota_alert_email(classified, context=context or "llm_image(DALL-E)", url=url)
        else:
            print(f"  [llm_image] DALL-E failed ({err_text}) — falling back to Replicate")

    # Tier 3 — Replicate SDXL
    try:
        return _try_replicate_image(prompt, size)
    except Exception as e:
        last_err = e
        err_text = f"{type(e).__name__}: {e}"
        classified = classify_error(err_text)
        if classified:
            send_quota_alert_email(classified, context=context or "llm_image(Replicate)", url=url)
        print(f"  [llm_image] Replicate failed ({err_text})")

    raise RuntimeError(f"All image providers failed. Last error: {last_err}") from last_err
