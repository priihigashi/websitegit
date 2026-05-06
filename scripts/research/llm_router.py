"""llm_router.py — Anthropic Haiku → OpenAI cascade for SH-104.

Honors `route_state.get_state()`:
  - fallback_mode == "no_paid_anthropic_apify": skip Anthropic entirely
  - fallback_mode == "strict":                  raise if primary unavailable
  - fallback_mode == "auto" (default):          try Anthropic, cascade on failure

Public API:
  llm_json(prompt, max_tokens=1500, system="", on_failure=None) -> dict
      Run prompt -> parsed JSON dict. Returns {} if every route fails AND
      mode is not strict. on_failure(stage, error) is invoked on each
      route-level failure so the caller can also log to 🚨 Pipeline Failures.

Cost note:
  - Anthropic Haiku ≈ $0.80/M in, $4/M out (preferred — cheaper for SH-104).
  - OpenAI gpt-4o-mini ≈ $0.15/M in, $0.60/M out (fallback when Anthropic
    quota/auth fails — still very cheap).
"""

from __future__ import annotations
import json
import os
import re
from pathlib import Path
import sys

# Ensure sibling imports work regardless of how this is loaded.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from route_state import get_state  # noqa: E402

# Anthropic key — pipeline previously used CLAUDE_KEY_4_CONTENT for billing
# isolation. Fall back to ANTHROPIC_API_KEY if the dedicated key isn't set.
CLAUDE_KEY = (os.environ.get("CLAUDE_KEY_4_CONTENT", "")
              or os.environ.get("ANTHROPIC_API_KEY", ""))
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

CLAUDE_MODEL = os.environ.get("SH104_CLAUDE_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.environ.get("SH104_OPENAI_MODEL", "gpt-4o-mini")


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"```\s*$", "", raw)
    return raw.strip()


def _is_quota_or_auth_error(msg: str) -> bool:
    """Heuristic: should we cascade to OpenAI? True for quota/auth/rate, NOT
    for parse errors (those are model-output bugs, both providers may hit
    them — we let OpenAI try anyway in non-strict mode)."""
    m = (msg or "").lower()
    return any(t in m for t in (
        "401", "403", "402", "429",
        "quota", "credit", "billing", "rate limit",
        "insufficient", "unauthorized", "authentication",
        "permission", "exceeded", "overload",
    ))


# ── core ─────────────────────────────────────────────────────────────────────

def llm_json(prompt: str, *, max_tokens: int = 1500,
             system: str = "", on_failure=None) -> dict:
    """Anthropic Haiku → OpenAI gpt-4o-mini cascade. Returns parsed JSON dict.

    Returns {} when both routes fail AND mode is not strict.
    Raises RuntimeError("strict_mode_..." ) in strict mode when primary fails.
    """
    state = get_state()

    # ── Tier 1: Anthropic ────────────────────────────────────────────────────
    if state.should_try_anthropic():
        if not CLAUDE_KEY:
            state.mark_unavailable("anthropic", "no_api_key")
        else:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=CLAUDE_KEY)
                kwargs = {
                    "model": CLAUDE_MODEL,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if system:
                    kwargs["system"] = system
                msg = client.messages.create(**kwargs)
                raw = msg.content[0].text if msg.content else ""
                data = json.loads(_strip_fences(raw))
                if isinstance(data, dict):
                    state.mark_used("anthropic")
                    return data
                state.mark_failed(
                    "anthropic", "llm_json", "non_dict_response",
                    on_failure=on_failure,
                )
            except Exception as e:
                err = str(e)[:300]
                state.mark_failed("anthropic", "llm_json", err, on_failure=on_failure)
                if state.is_strict():
                    raise RuntimeError(f"strict_mode_anthropic_failed: {err}") from e

    # Strict mode: don't cascade if Anthropic was supposed to work
    if state.is_strict() and state.route_status["anthropic"] in ("failed", "unavailable"):
        raise RuntimeError("strict_mode_anthropic_unavailable")

    # ── Tier 2: OpenAI ───────────────────────────────────────────────────────
    if not OPENAI_KEY:
        state.mark_unavailable("openai", "no_api_key")
        return {}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_KEY)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        # Nudge model to emit pure JSON; response_format below enforces it.
        json_user = (prompt + "\n\nReturn ONLY a single valid JSON object. No prose.")
        messages.append({"role": "user", "content": json_user})

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        data = json.loads(_strip_fences(raw) or "{}")
        if isinstance(data, dict):
            state.mark_used("openai")
            return data
        state.mark_failed("openai", "llm_json", "non_dict_response",
                          on_failure=on_failure)
        return {}
    except Exception as e:
        err = str(e)[:300]
        state.mark_failed("openai", "llm_json", err, on_failure=on_failure)
        if state.is_strict():
            raise RuntimeError(f"strict_mode_openai_failed: {err}") from e
        return {}
