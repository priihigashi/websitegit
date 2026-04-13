"""
runner.py — Module isolation wrapper for the 4AM agent.
Every module runs through run_module() — no single failure can kill the whole run.
Failures are written to .github/agent_state/module_failures.json for self_healer.
"""
import os, time, traceback, json
from datetime import datetime
import pytz

et          = pytz.timezone("America/New_York")
_results    = {}
STATE_DIR   = ".github/agent_state"
FAILURES_FILE = f"{STATE_DIR}/module_failures.json"


def run_module(name, fn, *args, **kwargs):
    """Run fn(*args) in isolation. Never raises. Returns (ok: bool, result: any)."""
    start = time.time()
    try:
        result = fn(*args, **kwargs)
        dur = round(time.time() - start, 1)
        _results[name] = {"status": "ok", "duration_s": dur}
        print(f"[runner] ✓ {name} ({dur}s)")
        return True, result
    except Exception as e:
        dur = round(time.time() - start, 1)
        _results[name] = {
            "status":    "fail",
            "error":     str(e),
            "traceback": traceback.format_exc(),
            "duration_s": dur,
            "ts":        datetime.now(et).isoformat(),
        }
        print(f"[runner] ✗ {name} FAILED ({dur}s): {e}")
        _persist()
        return False, None


def get_results():
    return dict(_results)


def failed_modules():
    return {k: v for k, v in _results.items() if v["status"] == "fail"}


def summary_line():
    total = len(_results)
    ok    = sum(1 for v in _results.values() if v["status"] == "ok")
    fail  = total - ok
    names_failed = [k for k, v in _results.items() if v["status"] == "fail"]
    if fail == 0:
        return f"All {total} modules OK"
    return f"{ok}/{total} modules OK — failed: {', '.join(names_failed)}"


def _persist():
    os.makedirs(STATE_DIR, exist_ok=True)
    failed = {k: v for k, v in _results.items() if v["status"] == "fail"}
    with open(FAILURES_FILE, "w") as f:
        json.dump(failed, f, indent=2)
