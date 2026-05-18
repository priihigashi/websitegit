"""ResourceRouter approval + cross-runner bridge tests.

Covers the remaining audit gaps after commit 9878fed:
- Flow B reply parsing + candidate status mutation.
- story_id resolution and Drive resource fetch bridge.
"""

from __future__ import annotations

import io
import json
import sys
import types
import unittest
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "content_creator"))

try:
    import pytz  # noqa: F401
except ModuleNotFoundError:
    sys.modules["pytz"] = types.SimpleNamespace(
        timezone=lambda name: ZoneInfo(name),
        UTC=timezone.utc,
    )

if "googleapiclient.http" not in sys.modules:
    google_mod = types.ModuleType("googleapiclient")
    http_mod = types.ModuleType("googleapiclient.http")
    http_mod.MediaIoBaseDownload = object
    http_mod.MediaInMemoryUpload = object
    http_mod.MediaFileUpload = object
    sys.modules.setdefault("googleapiclient", google_mod)
    sys.modules["googleapiclient.http"] = http_mod

import approval_handler as ah  # noqa: E402
import main as cc_main  # noqa: E402


class ResourceRouterApprovalTests(unittest.TestCase):
    def test_resource_router_subject_and_reply_parser(self):
        subject = "Re: [ResourceRouter] NWS-001 — 3 clip candidates need approval"

        self.assertTrue(ah.is_resource_router_reply(subject))
        self.assertEqual(ah._extract_rr_story_id(subject), "NWS-001")
        self.assertEqual(
            ah.parse_resource_router_reply("APPROVE 1, 3"),
            {"action": "approve", "indices": [1, 3]},
        )
        self.assertEqual(
            ah.parse_resource_router_reply("approve all"),
            {"action": "approve", "indices": "all"},
        )
        self.assertEqual(
            ah.parse_resource_router_reply("reject all"),
            {"action": "reject", "indices": "all"},
        )

    def test_candidate_status_flip_preserves_non_selected_entries(self):
        manifest = {
            "clips": [
                {"title": "one", "status": "CANDIDATE"},
                {"title": "two", "status": "STAGED"},
                {"title": "three", "status": "CANDIDATE"},
            ]
        }

        updated, count = ah._apply_rr_candidate_action(manifest, "approve", [1, 3])

        self.assertEqual(count, 2)
        self.assertIs(updated, manifest)
        self.assertEqual(updated["clips"][0]["status"], "APPROVED")
        self.assertEqual(updated["clips"][1]["status"], "STAGED")
        self.assertEqual(updated["clips"][2]["status"], "APPROVED")

    def test_reject_all_candidates_in_list_manifest(self):
        manifest = [
            {"title": "one", "status": "CANDIDATE"},
            {"title": "two", "status": "APPROVED"},
            {"title": "three", "status": "CANDIDATE"},
        ]

        updated, count = ah._apply_rr_candidate_action(manifest, "reject", "all")

        self.assertEqual(count, 2)
        self.assertIs(updated, manifest)
        self.assertEqual([x["status"] for x in updated], ["REJECTED", "APPROVED", "REJECTED"])


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeMediaRequest:
    def __init__(self, data: bytes):
        self.data = data


class _FakeFiles:
    def __init__(self, files_by_id):
        self.files_by_id = files_by_id
        self.list_calls = []

    def list(self, **kwargs):
        query = kwargs.get("q", "")
        self.list_calls.append(kwargs)
        if "mimeType='application/vnd.google-apps.folder'" in query:
            return _FakeExecute({"files": [{"id": "folder-1", "name": "resources_NWS-001"}]})
        if "'folder-1' in parents" in query:
            return _FakeExecute({
                "files": [
                    {"id": "clips-file", "name": "clips.json"},
                    {"id": "story-file", "name": "story_resources.json"},
                ]
            })
        return _FakeExecute({"files": []})

    def get_media(self, fileId, supportsAllDrives=True):
        return _FakeMediaRequest(self.files_by_id[fileId])


class _FakeDrive:
    def __init__(self):
        self._files = _FakeFiles({
            "clips-file": json.dumps([{"status": "STAGED", "target_slide": 1}]).encode("utf-8"),
            "story-file": json.dumps({"combined_summary": "Use the approved source clip."}).encode("utf-8"),
        })

    def files(self):
        return self._files


class _FakeDownloader:
    def __init__(self, fh: io.BytesIO, request: _FakeMediaRequest):
        self.fh = fh
        self.request = request
        self.done = False

    def next_chunk(self):
        if not self.done:
            self.fh.write(self.request.data)
            self.done = True
        return None, True


class ContentCreatorBridgeTests(unittest.TestCase):
    def test_resource_story_id_prefers_topic_entry_then_env(self):
        with patch.dict("os.environ", {"CAPTURE_STORY_ID": "ENV-1"}, clear=False):
            self.assertEqual(cc_main._resource_story_id({"story_id": "ROW-1"}), "ROW-1")
            self.assertEqual(cc_main._resource_story_id({"capture_story_id": "ROW-2"}), "ROW-2")
            self.assertEqual(cc_main._resource_story_id({}), "")
            self.assertEqual(cc_main._resource_story_id({"_allow_env_story_id": True}), "ENV-1")

    def test_manifest_target_slide_uses_hint_then_first_open_suggestion(self):
        self.assertEqual(cc_main._manifest_target_slide("4", [1, 2], {}), 4)
        self.assertIsNone(cc_main._manifest_target_slide("bad", [1, 2], {}))
        self.assertEqual(cc_main._manifest_target_slide(None, [1, 2, 3], {1: "/tmp/a.mp4"}), 2)
        self.assertEqual(cc_main._manifest_target_slide(None, [], {}), 1)

    def test_fetch_resources_from_drive_downloads_clips_and_story_resources(self):
        fake_drive = _FakeDrive()
        fake_http = types.ModuleType("googleapiclient.http")
        fake_http.MediaIoBaseDownload = _FakeDownloader
        fake_http.MediaInMemoryUpload = object

        with patch.object(cc_main, "get_drive_service", return_value=fake_drive), patch.dict(
            sys.modules, {"googleapiclient.http": fake_http}
        ):
            target = ROOT / "tmp_test_resources_bridge"
            if target.exists():
                for child in target.iterdir():
                    child.unlink()
            else:
                target.mkdir()
            try:
                fetched = cc_main.fetch_resources_from_drive("NWS-001", target)
                self.assertEqual(fetched, 2)
                self.assertTrue((target / "clips.json").exists())
                self.assertTrue((target / "story_resources.json").exists())
                self.assertEqual(
                    json.loads((target / "story_resources.json").read_text())["combined_summary"],
                    "Use the approved source clip.",
                )
                self.assertTrue(
                    all(call.get("supportsAllDrives") and call.get("includeItemsFromAllDrives")
                        for call in fake_drive.files().list_calls)
                )
            finally:
                for child in target.iterdir():
                    child.unlink()
                target.rmdir()


if __name__ == "__main__":
    unittest.main()
