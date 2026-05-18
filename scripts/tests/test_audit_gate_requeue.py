"""Audit-order regression tests for preview gating and failed rebuilds."""

from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "scripts" / "content"))

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

import content_auditor  # noqa: E402
import main as cc_main  # noqa: E402
import send_preview_emails  # noqa: E402


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AuditGatePreviewTests(unittest.TestCase):
    def test_preview_sender_only_allows_explicit_audit_pass(self):
        results = [
            {"post_id": "PASS-1", "audit_result": {"passed": True}},
            {"post_id": "FAIL-1", "audit_result": {"passed": False}},
            {"post_id": "MISSING-AUDIT"},
        ]

        passing = send_preview_emails.passing_audited_results(results)

        self.assertEqual([r["post_id"] for r in passing], ["PASS-1"])

    def test_audit_failures_mark_catalog_and_queue_for_rebuild(self):
        calls = []

        def fake_sheet_values(tab, range_a1):
            if tab == content_auditor.CATALOG_TAB:
                return [
                    ["Post ID", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "Status", "Notes"],
                    ["POST-1", "", "", "", "", "", "", "", "", "", "", "", "pending_approval", "old note"],
                ]
            if tab == content_auditor.QUEUE_TAB and range_a1 == "A:AZ":
                rows = [["Status", "Date Status Changed", "Notes"]]
                rows.extend([[] for _ in range(40)])
                rows.append(["pending_approval", "2026-05-16", "queue old note"])
                return rows
            return []

        def fake_batch_update(updates):
            calls.extend(updates)

        failed = [{
            "post_id": "POST-1",
            "agents": [{"agent": "Fact Checker", "verdict": "FAIL", "score": 5}],
        }]
        results = [{"post_id": "POST-1", "queue_row_idx": 42}]

        with patch.object(content_auditor, "_sheet_values", side_effect=fake_sheet_values), patch.object(
            content_auditor, "_batch_update", side_effect=fake_batch_update
        ):
            updated = content_auditor.mark_audit_failed_requeue(results, failed)

        self.assertEqual(updated, 1)
        self.assertIn({"range": "'📸 Project Content Catalog'!M2", "values": [["audit_failed_requeue"]]}, calls)
        self.assertIn({"range": "'📋 Content Queue'!A42", "values": [["Awaiting Rebuild"]]}, calls)
        notes = [u for u in calls if u["range"] in ("'📸 Project Content Catalog'!N2", "'📋 Content Queue'!C42")]
        self.assertTrue(any("RR-AUDIT-ORDER" in u["values"][0][0] for u in notes))
        self.assertTrue(any("queue old note" in u["values"][0][0] for u in notes))

    def test_queue_loader_includes_awaiting_rebuild_even_with_drive_folder(self):
        rows = [
            [
                "Status", "Content Type", "Drive Folder Path", "Source", "Format", "Project Name",
                "Brief / Angle", "Inspo URL", "series_override", "fake_news_route",
                "fake_news_confidence", "clips_needed", "story_id",
            ],
            ["Approved", "Carousel", "", "brazil", "", "Fresh story", "angle", "url", "", "B", "0", "", "STORY-1"],
            ["Approved", "Carousel", "https://drive/folder", "brazil", "", "Built already", "angle", "url", "", "B", "0", "", "STORY-2"],
            ["Awaiting Rebuild", "Carousel", "https://drive/folder", "brazil", "", "Retry me", "angle", "url", "", "B", "0", "", "STORY-3"],
        ]

        with patch.object(cc_main, "get_oauth_token", return_value="token"), patch.object(
            cc_main.urllib.request, "urlopen", return_value=_FakeHTTPResponse({"values": rows})
        ):
            buildable = cc_main.get_approved_queue_rows()

        self.assertEqual([row["topic"] for row in buildable], ["Fresh story", "Retry me"])
        retry = buildable[1]
        self.assertTrue(retry["force_rebuild"])
        self.assertEqual(retry["rebuild_reason"], "awaiting rebuild")


if __name__ == "__main__":
    unittest.main()
