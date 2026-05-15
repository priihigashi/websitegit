#!/usr/bin/env python3
"""
clips_manifest.py
=================
Reader/writer for `resources/clips/clips.json` — the per-carousel clip manifest
produced by the Video Resource Downloader flow.

Shape (one entry per clip):

    {
      "source_url":         "https://...",
      "story_id":           "NWS-001",
      "local_path":         "/tmp/clips/.../candidate_01_xyz.mp4",
      "drive_file_id":      "1abc...",
      "drive_view_link":    "https://drive.google.com/...",
      "duration_sec":       12.4,
      "media_kind":         "video" | "image",
      "suggested_cut_start": null,
      "target_slide":       null,
      "status":             "STAGED" | "CANDIDATE" | "APPROVED" | "REJECTED" | "DOWNLOADED",
      "title":              "",
      "added_at":           "2026-05-14T17:30:00+00:00",
      "flow":               "A" | "B",
      "search_query":       ""        # only set for Flow B candidates
    }

File lives next to other manifest files. Upserted by (story_id, source_url)
when story_id is present, otherwise source_url, so re-runs do not duplicate rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_FILENAME = "clips.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path(clips_dir: Path | str) -> Path:
    return Path(clips_dir) / DEFAULT_FILENAME


def load(clips_dir: Path | str) -> list[dict]:
    """Load existing clips.json (returns [] if missing or malformed)."""
    p = manifest_path(clips_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "clips" in data:  # legacy envelope
        return list(data.get("clips") or [])
    if isinstance(data, list):
        return data
    return []


def save(clips_dir: Path | str, clips: list[dict]) -> Path:
    """Atomic write — newlines + indent for git-friendly diffs."""
    p = manifest_path(clips_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clips, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def make_entry(
    *,
    source_url: str,
    story_id: str = "",
    local_path: str = "",
    duration_sec: float = 0.0,
    status: str = "STAGED",
    flow: str = "A",
    title: str = "",
    search_query: str = "",
    target_slide: Any = None,
    suggested_cut_start: Any = None,
    drive_file_id: str = "",
    drive_view_link: str = "",
    error: str = "",
    media_kind: str = "video",
) -> dict:
    """Build a single manifest entry matching the documented shape."""
    return {
        "source_url": source_url,
        "story_id": story_id or "",
        "local_path": local_path,
        "drive_file_id": drive_file_id,
        "drive_view_link": drive_view_link,
        "duration_sec": float(duration_sec or 0.0),
        "media_kind": media_kind or "video",
        "suggested_cut_start": suggested_cut_start,
        "target_slide": target_slide,
        "status": status,
        "title": title or "",
        "search_query": search_query or "",
        "flow": flow,
        "added_at": _now_iso(),
        "error": error or "",
    }


def upsert(
    clips_dir: Path | str,
    entry: dict,
    *,
    key: str = "source_url",
) -> list[dict]:
    """Insert or update a single entry by `key`. Persists and returns full list."""
    clips = load(clips_dir)
    entry_key = _entry_key(entry, key)
    matched = False
    for i, existing in enumerate(clips):
        if entry_key and _entry_key(existing, key) == entry_key:
            merged = dict(existing)
            merged.update({k: v for k, v in entry.items() if v not in (None, "")})
            # Preserve the older added_at; record updated_at separately
            merged["added_at"] = existing.get("added_at") or entry.get("added_at")
            merged["updated_at"] = _now_iso()
            clips[i] = merged
            matched = True
            break
    if not matched:
        clips.append(entry)
    save(clips_dir, clips)
    return clips


def upsert_many(clips_dir: Path | str, entries: Iterable[dict], *, key: str = "source_url") -> list[dict]:
    """Bulk upsert — single read/write."""
    clips = load(clips_dir)
    by_key = {_entry_key(c, key): i for i, c in enumerate(clips) if _entry_key(c, key)}
    for entry in entries:
        k = _entry_key(entry, key)
        if k and k in by_key:
            idx = by_key[k]
            merged = dict(clips[idx])
            merged.update({kk: vv for kk, vv in entry.items() if vv not in (None, "")})
            merged["added_at"] = clips[idx].get("added_at") or entry.get("added_at")
            merged["updated_at"] = _now_iso()
            clips[idx] = merged
        else:
            clips.append(entry)
            if k:
                by_key[k] = len(clips) - 1
    save(clips_dir, clips)
    return clips


def _entry_key(entry: dict, key: str):
    if not isinstance(entry, dict):
        return ""
    if key == "source_url" and entry.get("story_id") and entry.get("source_url"):
        return (entry.get("story_id"), entry.get("source_url"))
    return entry.get(key)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--show", action="store_true")
    args = p.parse_args()
    if args.show:
        print(json.dumps(load(args.dir), indent=2))
