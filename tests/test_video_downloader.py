"""Tests for scripts/capture/video_downloader.py.

These tests do NOT actually invoke yt-dlp — they verify the helper logic
(URL classification, cookie argument building, staging dir creation) and
exercise the subprocess wrapper via monkeypatching.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "capture"))
import video_downloader as vd


def test_is_youtube():
    assert vd._is_youtube("https://www.youtube.com/watch?v=abc")
    assert vd._is_youtube("https://youtu.be/abc")
    assert not vd._is_youtube("https://www.instagram.com/reel/abc/")


def test_is_instagram_and_tiktok():
    assert vd._is_instagram("https://www.instagram.com/reel/abc/")
    assert vd._is_tiktok("https://www.tiktok.com/@x/video/123")


def test_staging_dir_for_creates_path(tmp_path, monkeypatch):
    monkeypatch.setattr(vd, "DEFAULT_STAGING_ROOT", tmp_path / "clips")
    d = vd.staging_dir_for("BCI-001", "search")
    assert d.exists()
    assert d.is_dir()
    assert "BCI-001" in str(d)
    assert "search" in str(d)


def test_staging_dir_sanitizes(tmp_path, monkeypatch):
    monkeypatch.setattr(vd, "DEFAULT_STAGING_ROOT", tmp_path / "clips")
    d = vd.staging_dir_for("BCI/../bad id", "")
    assert "BCI" in d.name
    assert "/" not in d.name[1:]  # leading / from path, but no embedded slashes


def test_cookie_args_no_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "_YT_COOKIES_FILE", tmp_path / "no_yt")
    monkeypatch.setattr(vd, "_IG_COOKIES_FILE", tmp_path / "no_ig")
    monkeypatch.setenv("CI", "1")
    monkeypatch.delenv("PRI_OP_YT_COOKIES", raising=False)
    monkeypatch.delenv("PRI_OP_IG_COOKIES", raising=False)
    # In CI mode with no cookies → empty
    assert vd._cookie_args("https://www.youtube.com/watch?v=x") == []
    assert vd._cookie_args("https://www.instagram.com/reel/x/") == []


def test_download_url_handles_missing_yt_dlp(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "_which", lambda c: "")
    res = vd.download_url("https://example.com/x", staging=tmp_path)
    assert res["ok"] is False
    assert "yt-dlp not installed" in res["error"]


def test_instagram_download_uses_apify_before_yt_dlp(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "_which", lambda c: "")

    def fake_apify(url, staging, filename_hint):
        local = Path(staging) / f"{filename_hint}_apify.mp4"
        local.write_bytes(b"fake-video" * 20000)
        return {
            "ok": True,
            "source_url": url,
            "local_path": str(local),
            "duration_sec": 9.0,
            "title": "Apify Reel",
            "error": "",
        }

    monkeypatch.setattr(vd, "_download_via_apify_instagram", fake_apify)
    res = vd.download_url("https://www.instagram.com/reel/ABC123/", staging=tmp_path)

    assert res["ok"] is True
    assert res["title"] == "Apify Reel"
    assert res["local_path"].endswith("_apify.mp4")


def test_download_url_handles_subprocess_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "_which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(vd, "_cookie_args", lambda url, **kw: [])

    class FakeProc:
        returncode = 1
        stderr = "ERROR: video unavailable"
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
    res = vd.download_url("https://www.youtube.com/watch?v=fail", staging=tmp_path)
    assert res["ok"] is False
    assert "video unavailable" in res["error"]


def test_download_url_finds_output(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "_which", lambda c: "/usr/bin/" + c)
    monkeypatch.setattr(vd, "_cookie_args", lambda url, **kw: [])
    monkeypatch.setattr(vd, "_probe_duration", lambda p: 18.5)

    fake_file = tmp_path / "candidate_01_abc.mp4"
    fake_file.write_bytes(b"fake-video-bytes")

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
    res = vd.download_url(
        "https://www.youtube.com/watch?v=abc",
        staging=tmp_path,
        filename_hint="candidate_01",
    )
    assert res["ok"] is True
    assert res["local_path"] == str(fake_file)
    assert res["duration_sec"] == 18.5


def test_search_youtube_handles_empty_query(tmp_path):
    res = vd.search_youtube("", staging=tmp_path)
    assert len(res) == 1
    assert res[0]["ok"] is False
    assert "empty query" in res[0]["error"]


def test_search_youtube_parses_list_output(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "_which", lambda c: "/usr/bin/" + c)

    list_output = (
        "https://www.youtube.com/watch?v=v1\tTitle One\t12\n"
        "https://www.youtube.com/watch?v=v2\tTitle Two\t30\n"
        "https://www.youtube.com/watch?v=v3\tTitle Three\t45\n"
    )

    class FakeListProc:
        returncode = 0
        stderr = ""
        stdout = list_output

    called = {"count": 0, "urls": []}

    def fake_run(cmd, **kw):
        # First call is the "list" command; subsequent are individual download_url calls
        if "--skip-download" in cmd:
            return FakeListProc()
        called["count"] += 1
        called["urls"].append(cmd[-1])

        class FakeDlProc:
            returncode = 0
            stderr = ""
            stdout = ""
        return FakeDlProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(vd, "_cookie_args", lambda url, **kw: [])

    # Pre-create the expected output files so the file-find succeeds
    for i in (1, 2, 3):
        (tmp_path / f"candidate_{i:02d}_v{i}.mp4").write_bytes(b"x")

    monkeypatch.setattr(vd, "_probe_duration", lambda p: 10.0)

    results = vd.search_youtube("test topic", n_results=3, staging=tmp_path)
    assert len(results) == 3
    assert all(r["ok"] for r in results)
    assert "v1" in called["urls"][0]
