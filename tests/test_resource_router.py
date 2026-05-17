import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "capture"))

import resource_router as rr
import clips_manifest
from resource_router import build_resource_manifest, route_capture_resources


def test_build_resource_manifest_from_note_links():
    manifest = build_resource_manifest(
        story_id="BCI-001",
        project="brazil",
        seed_url="https://example.com/seed",
        notes=(
            "Use https://www.instagram.com/reel/ABC123/ as the hook on slide 1. "
            "Use https://www.youtube.com/watch?v=xyz987 main point on slide 4."
        ),
        transcript="",
    )

    assert manifest["story_id"] == "BCI-001"
    assert manifest["intent"]["clip_required"] is True
    assert len(manifest["items"]) == 2
    assert len(manifest["jobs"]) == 2
    assert manifest["items"][0]["local_path"].startswith("resources/clips/")
    assert manifest["jobs"][0]["type"] == "download_note_link"


def test_route_capture_resources_writes_manifest(tmp_path):
    manifest = route_capture_resources(
        story_id="BCI-002",
        project="brazil",
        notes="No links. Go research on this topic and bring videos about this senator.",
        transcript="",
        output_dir=tmp_path,
    )

    path = manifest["manifest_path"]
    assert os.path.exists(path)
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["story_id"] == "BCI-002"
    assert data["jobs"][0]["type"] == "research_videos"
    assert data["jobs"][0]["request"]["target"] == "Clip Collections"


def test_execute_resource_jobs_flow_a_downloads_uploads_writes_manifest(monkeypatch, tmp_path):
    """Flow A: URL in notes → download → upload → clips.json STAGED entry."""
    # 1. Build manifest from notes containing a URL
    manifest = build_resource_manifest(
        story_id="BCI-A1",
        project="brazil",
        notes="Use https://www.instagram.com/reel/ABC123/ as the hook on slide 1.",
    )
    assert manifest["jobs"][0]["type"] == "download_note_link"

    # 2. Fake video_downloader.download_url to return a successful result
    def fake_download_url(url, *, staging, filename_hint="", **kw):
        Path(staging).mkdir(parents=True, exist_ok=True)
        local = Path(staging) / f"{filename_hint or 'clip'}.mp4"
        local.write_bytes(b"fake")
        return {
            "ok": True,
            "source_url": url,
            "local_path": str(local),
            "duration_sec": 7.2,
            "title": "Fake Reel",
            "error": "",
        }

    monkeypatch.setattr(rr.video_downloader, "download_url", fake_download_url)
    monkeypatch.setattr(rr.video_downloader, "staging_dir_for",
                        lambda story="", subdir="": tmp_path / "stage")

    # 3. Disable Drive + email side-effects
    monkeypatch.setattr(rr, "_get_drive_service", lambda: None)
    monkeypatch.setattr(rr, "upload_clip_to_drive",
                        lambda *a, **kw: {"id": "FAKE_DRIVE_ID", "webViewLink": "https://drive/x"})
    monkeypatch.setattr(rr, "_capture_folder_for", lambda p: "FAKE_FOLDER_ID")
    monkeypatch.setattr(rr, "_ensure_subfolder", lambda parent, name, **kw: parent)
    monkeypatch.setattr(rr, "_trigger_send_email", lambda **kw: True)

    # 4. Execute
    result = rr.execute_resource_jobs(
        manifest, project="brazil",
        clips_dir=tmp_path / "stage",
        send_emails=False,
    )

    assert result["jobs"][0]["status"] == "done"
    # clips.json written with STAGED status
    clips = clips_manifest.load(tmp_path / "stage")
    assert len(clips) == 1
    assert clips[0]["status"] == "STAGED"
    assert clips[0]["flow"] == "A"
    assert clips[0]["drive_file_id"] == "FAKE_DRIVE_ID"
    assert clips[0]["duration_sec"] == 7.2
    assert clips[0]["story_id"] == "BCI-A1"
    assert clips[0]["target_slide"] == 1


def test_execute_resource_jobs_flow_b_emails_candidates(monkeypatch, tmp_path):
    """Flow B: research keyword → ytsearch → top 3 → CANDIDATE entries + email."""
    manifest = build_resource_manifest(
        story_id="BCI-B1",
        project="brazil",
        notes="Need more videos for this senator — find videos about his rachadinha case.",
    )
    # Should have a research_videos job
    types = [j["type"] for j in manifest["jobs"]]
    assert "research_videos" in types

    # Fake ytsearch results
    fake_results = []
    for i in (1, 2, 3):
        fp = tmp_path / "stage" / "search" / f"candidate_{i:02d}.mp4"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(b"x")
        fake_results.append({
            "ok": True,
            "source_url": f"https://www.youtube.com/watch?v=v{i}",
            "local_path": str(fp),
            "duration_sec": 15.0 + i,
            "title": f"Candidate {i}",
            "error": "",
        })

    monkeypatch.setattr(rr.video_downloader, "search_youtube",
                        lambda q, **kw: fake_results)
    monkeypatch.setattr(rr.video_downloader, "staging_dir_for",
                        lambda story="", subdir="": tmp_path / "stage")

    monkeypatch.setattr(rr, "_get_drive_service", lambda: None)
    monkeypatch.setattr(rr, "upload_clip_to_drive",
                        lambda *a, **kw: {"id": "FAKE", "webViewLink": "https://drive/x"})
    monkeypatch.setattr(rr, "_capture_folder_for", lambda p: "FAKE_FOLDER_ID")
    monkeypatch.setattr(rr, "_ensure_subfolder", lambda parent, name, **kw: parent)

    sent = {}
    def fake_email(**kw):
        sent.update(kw)
        return True
    monkeypatch.setattr(rr, "_trigger_send_email", fake_email)

    result = rr.execute_resource_jobs(
        manifest, project="brazil",
        clips_dir=tmp_path / "stage",
        send_emails=True,
    )

    # All 3 candidates landed in clips.json with CANDIDATE status
    clips = clips_manifest.load(tmp_path / "stage")
    assert len(clips) == 3
    assert all(c["status"] == "CANDIDATE" for c in clips)
    assert all(c["flow"] == "B" for c in clips)

    # Email was triggered with 3 candidates referenced in body
    assert sent.get("subject", "").startswith("[ResourceRouter]")
    body = sent.get("body", "")
    assert "Candidate 1" in body
    assert "v3" in body  # third candidate URL
    assert result["execution"]["candidates_emailed"] == 3


def test_execute_resource_jobs_no_jobs_is_safe(monkeypatch, tmp_path):
    """Notes with no URL and no research keyword → no jobs → safe no-op."""
    manifest = build_resource_manifest(
        story_id="BCI-Z",
        project="brazil",
        notes="Just a regular caption with no links and no research request.",
    )
    assert manifest["jobs"] == []
    monkeypatch.setattr(rr.video_downloader, "staging_dir_for",
                        lambda story="", subdir="": tmp_path / "stage")
    monkeypatch.setattr(rr, "_get_drive_service", lambda: None)
    monkeypatch.setattr(rr, "_capture_folder_for", lambda p: "")
    result = rr.execute_resource_jobs(manifest, project="brazil", send_emails=False)
    assert "execution" in result
    assert result["execution"]["candidates_emailed"] == 0


def test_clip_analysis_uses_llm_cascade(monkeypatch):
    calls = {}

    def fake_cascade(prompt, **kwargs):
        calls["prompt"] = prompt
        calls["kwargs"] = kwargs
        return json.dumps({
            "clip_role": "hook",
            "best_moment": "00:03-00:08",
            "story_use": "Use this as the opening proof clip.",
            "carousel_fit": "cover",
            "confidence": 0.91,
        })

    monkeypatch.setattr(rr, "_llm_text_cascade", fake_cascade)
    out = rr._analyze_clip_with_haiku(
        {"title": "Apology video", "duration_sec": 12, "source_url": "https://example.com/v"},
        "I apologize for what happened.",
    )

    assert out["clip_role"] == "hook"
    assert out["carousel_fit"] == "cover"
    assert calls["kwargs"]["model_tier"] == "haiku"
    assert calls["kwargs"]["context"] == "resource_router.clip_intel"
