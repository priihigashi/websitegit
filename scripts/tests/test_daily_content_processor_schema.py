"""Daily content processor sheet-schema tests.

Run:
  python3 -m unittest scripts/tests/test_daily_content_processor_schema.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import daily_content_processor as dcp  # noqa: E402


class DailyContentProcessorSchemaTests(unittest.TestCase):
    def test_youtube_video_id_handles_watch_short_and_short_url(self):
        self.assertEqual(
            dcp.youtube_video_id("https://www.youtube.com/watch?v=BErErllIgFM"),
            "BErErllIgFM",
        )
        self.assertEqual(
            dcp.youtube_video_id("https://youtu.be/abc123xyz01"),
            "abc123xyz01",
        )
        self.assertEqual(
            dcp.youtube_video_id("https://www.youtube.com/shorts/ABCDEFGHIJK"),
            "ABCDEFGHIJK",
        )

    def test_header_index_prefers_url_over_content_hub_link(self):
        header = [
            "Date Added",
            "Content Hub Link",
            "Platform",
            "URL",
            "Status",
        ]

        self.assertEqual(dcp.header_index(header, "URL", "Link"), 3)

    def test_inspiration_library_update_is_header_keyed(self):
        header = [
            "Date Added",
            "Content Hub Link",
            "Platform",
            "URL",
            "Description",
            "Transcription",
            "Status",
            "Topic / Title",
            "Niche",
            "AI Score (1-5)",
            "Date Status Changed",
        ]
        info = {
            "title": "Driveway repair",
            "about": "Concrete repair example.",
            "extracted": "Tip one\nTip two",
            "quality": 4,
            "niche": "Oak Park Construction",
            "flow_fit": "Social Media",
            "suggested_action": "Build carousel",
        }

        updates = dcp.build_row_update(
            header,
            [],
            info,
            "https://drive.google.com/file/d/example/view",
            "Source transcript",
        )

        self.assertEqual(updates["Topic / Title"], "Driveway repair")
        self.assertEqual(updates["Niche"], "Oak Park Construction")
        self.assertEqual(updates["AI Score (1-5)"], "4")
        self.assertEqual(updates["Status"], "Processed")
        self.assertIn("Source transcript", updates["Transcription"])
        self.assertNotIn("Content Hub Link", updates)
        self.assertNotIn("URL", updates)

    def test_old_content_inspo_update_still_supported(self):
        header = [
            "Date",
            "Link",
            "Topic/Type",
            "Niche",
            "Transcript Drive Link",
            "Title",
            "What we extracted",
            "Quality",
            "Status",
        ]
        info = {
            "title": "AI workflow",
            "about": "Automation example.",
            "extracted": "Step one",
            "quality": 3,
            "niche": "AI Tips",
            "flow_fit": "AI Tips",
            "suggested_action": "Save prompt",
        }

        updates = dcp.build_row_update(header, [], info, "drive-link", "transcript")

        self.assertEqual(updates["Title"], "AI workflow")
        self.assertEqual(updates["Transcript Drive Link"], "drive-link")
        self.assertIn("Step one", updates["What we extracted"])
        self.assertEqual(updates["Quality"], "3")


if __name__ == "__main__":
    unittest.main()
