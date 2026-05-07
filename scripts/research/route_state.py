"""route_state.py — fallback_mode + per-route status singleton (SH-104).

Three modes:
  auto                    — try paid primary (Apify, Anthropic), cascade on failure  [default]
  strict                  — fail the run if a paid primary is unavailable
  no_paid_anthropic_apify — skip paid routes entirely; OpenAI + web/YT/manual only

Per-route status values:
  untried | used | unavailable | skipped | failed

`manual_candidates` is an integer count rather than a status string.

The singleton is initialised lazily from FALLBACK_MODE env var on first
get_state() call. reset_state() exists for test reuse.

route_failures entries are non-fatal — they document which route/stage a run
had to fall away from. They are independent from PIPELINE_FAILURES (which
DOES flip the workflow exit code). A pipeline implementer who wants the
failure to also surface in the 🚨 Pipeline Failures sheet should pass an
`on_failure` callback that writes there without appending to the fatal list.

Important SH-104 distinction: one Apify actor/stage can fail because of a bad
schema, empty dataset, or proxy soft-fail while another Apify route remains
usable. Only auth/billing/provider-access/limit failures should disable the
whole Apify route.
"""

from __future__ import annotations
import os
import threading

VALID_MODES = ("auto", "strict", "no_paid_anthropic_apify")

_TRACKED_ROUTES = (
    "apify", "anthropic", "openai", "serpapi", "duckduckgo", "youtube",
)


class RouteState:
    def __init__(self, fallback_mode: str = "auto"):
        if fallback_mode not in VALID_MODES:
            fallback_mode = "auto"
        self.fallback_mode = fallback_mode
        self._lock = threading.Lock()

        skip_paid = (fallback_mode == "no_paid_anthropic_apify")
        self.route_status: dict = {r: "untried" for r in _TRACKED_ROUTES}
        if skip_paid:
            self.route_status["apify"] = "skipped"
            self.route_status["anthropic"] = "skipped"
        self.route_status["manual_candidates"] = 0
        self.route_failures: list[dict] = []

    # ── gates ────────────────────────────────────────────────────────────────

    def should_try_apify(self) -> bool:
        return self.route_status["apify"] not in ("skipped", "failed", "unavailable")

    def should_try_anthropic(self) -> bool:
        return self.route_status["anthropic"] not in ("skipped", "failed", "unavailable")

    def should_try_openai(self) -> bool:
        return self.route_status["openai"] not in ("skipped", "failed")

    def is_strict(self) -> bool:
        return self.fallback_mode == "strict"

    # ── markers ──────────────────────────────────────────────────────────────

    def mark_used(self, route: str):
        with self._lock:
            if route in _TRACKED_ROUTES:
                self.route_status[route] = "used"

    def mark_unavailable(self, route: str, reason: str = "no_credentials"):
        with self._lock:
            if route in _TRACKED_ROUTES and self.route_status[route] == "untried":
                self.route_status[route] = "unavailable"
                self.route_failures.append({
                    "route": route, "stage": "init", "reason": reason[:300],
                })

    def mark_failed(self, route: str, stage: str, reason,
                    on_failure=None, disable_route: bool = True) -> None:
        with self._lock:
            if route in _TRACKED_ROUTES and disable_route:
                self.route_status[route] = "failed"
            self.route_failures.append({
                "route": route, "stage": stage, "reason": str(reason)[:300],
            })
        if on_failure:
            try:
                on_failure(f"{route}:{stage}", reason)
            except Exception:
                pass

    def mark_stage_failed(self, route: str, stage: str, reason,
                          on_failure=None) -> None:
        """Record a route/stage failure without disabling sibling stages.

        Example: Apify hashtag discovery may 400 on actor input while Apify
        directUrl lookup is still healthy and should remain available for
        Instagram transcription.
        """
        self.mark_failed(
            route, stage, reason, on_failure=on_failure, disable_route=False,
        )

    def increment_manual(self, n: int = 1):
        with self._lock:
            self.route_status["manual_candidates"] = (
                int(self.route_status.get("manual_candidates", 0)) + n
            )

    # ── snapshot ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "fallback_mode": self.fallback_mode,
                "route_status": dict(self.route_status),
                "route_failures": list(self.route_failures),
            }


_STATE: RouteState | None = None


def get_state() -> RouteState:
    global _STATE
    if _STATE is None:
        mode = (os.environ.get("FALLBACK_MODE", "") or "auto").strip().lower()
        _STATE = RouteState(mode)
    return _STATE


def reset_state(fallback_mode: str | None = None) -> RouteState:
    """Re-init the singleton. Used by tests; also called by the runner so a
    second run inside the same process gets a clean slate."""
    global _STATE
    if fallback_mode is None:
        fallback_mode = (os.environ.get("FALLBACK_MODE", "") or "auto").strip().lower()
    _STATE = RouteState(fallback_mode)
    return _STATE
