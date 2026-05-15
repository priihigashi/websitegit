"""Tests for scripts/capture/clips_manifest.py — shape + upsert behavior."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "capture"))
import clips_manifest


def test_make_entry_shape():
    e = clips_manifest.make_entry(
        source_url="https://x/a",
        local_path="/tmp/clips/foo.mp4",
        duration_sec=12.4,
        status="STAGED",
        flow="A",
        title="Test",
        target_slide=1,
    )
    for key in ("source_url", "local_path", "drive_file_id", "drive_view_link",
                "duration_sec", "suggested_cut_start", "target_slide", "status",
                "title", "search_query", "flow", "media_kind", "added_at"):
        assert key in e, f"missing key {key}"
    assert e["status"] == "STAGED"
    assert e["flow"] == "A"
    assert e["duration_sec"] == 12.4


def test_save_and_load_roundtrip(tmp_path):
    e1 = clips_manifest.make_entry(source_url="https://x/a", status="STAGED")
    e2 = clips_manifest.make_entry(source_url="https://x/b", status="CANDIDATE", flow="B")
    clips_manifest.save(tmp_path, [e1, e2])
    loaded = clips_manifest.load(tmp_path)
    assert len(loaded) == 2
    assert loaded[0]["source_url"] == "https://x/a"
    assert loaded[1]["status"] == "CANDIDATE"


def test_upsert_inserts_new(tmp_path):
    e = clips_manifest.make_entry(source_url="https://x/a", status="STAGED")
    out = clips_manifest.upsert(tmp_path, e)
    assert len(out) == 1
    assert out[0]["source_url"] == "https://x/a"
    # File written
    assert (tmp_path / "clips.json").exists()


def test_upsert_updates_existing(tmp_path):
    a = clips_manifest.make_entry(source_url="https://x/a", status="STAGED")
    clips_manifest.upsert(tmp_path, a)
    b = clips_manifest.make_entry(source_url="https://x/a", status="APPROVED",
                                  drive_file_id="newid")
    out = clips_manifest.upsert(tmp_path, b)
    assert len(out) == 1
    assert out[0]["status"] == "APPROVED"
    assert out[0]["drive_file_id"] == "newid"
    assert "updated_at" in out[0]


def test_upsert_same_url_different_story_keeps_both(tmp_path):
    a = clips_manifest.make_entry(source_url="https://x/a", story_id="S1", status="STAGED")
    b = clips_manifest.make_entry(source_url="https://x/a", story_id="S2", status="STAGED")
    out = clips_manifest.upsert_many(tmp_path, [a, b])

    assert len(out) == 2
    assert {c["story_id"] for c in out} == {"S1", "S2"}


def test_upsert_many(tmp_path):
    entries = [
        clips_manifest.make_entry(source_url="https://x/a", status="STAGED"),
        clips_manifest.make_entry(source_url="https://x/b", status="CANDIDATE", flow="B"),
        clips_manifest.make_entry(source_url="https://x/c", status="CANDIDATE", flow="B"),
    ]
    out = clips_manifest.upsert_many(tmp_path, entries)
    assert len(out) == 3
    # Re-upsert same set with updated status
    updated = [clips_manifest.make_entry(source_url="https://x/a", status="APPROVED")]
    out2 = clips_manifest.upsert_many(tmp_path, updated)
    assert len(out2) == 3
    a_entry = next(c for c in out2 if c["source_url"] == "https://x/a")
    assert a_entry["status"] == "APPROVED"


def test_load_missing_file_returns_empty(tmp_path):
    assert clips_manifest.load(tmp_path) == []


def test_load_malformed_returns_empty(tmp_path):
    (tmp_path / "clips.json").write_text("not json at all")
    assert clips_manifest.load(tmp_path) == []
