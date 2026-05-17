import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "scripts" / "content_creator"
if str(CONTENT_DIR) not in sys.path:
    sys.path.insert(0, str(CONTENT_DIR))

import topic_picker  # noqa: E402


def test_queue_contains_matches_story_topic_or_url(monkeypatch):
    rows = [
        ["Project Name", "Inspo URL", "story_id"],
        ["Sophia Barclay case", "https://example.com/reel/1", "NWS-001"],
    ]
    monkeypatch.setattr(topic_picker, "sheet_get", lambda _range: rows)

    assert topic_picker.queue_contains(story_id="NWS-001")
    assert topic_picker.queue_contains(topic="Sophia Barclay case")
    assert topic_picker.queue_contains(url="https://example.com/reel/1")
    assert not topic_picker.queue_contains(story_id="NWS-002", topic="Other")


def test_queue_contains_missing_sheet_is_non_blocking(monkeypatch):
    def fail(_range):
        raise RuntimeError("temporary sheet outage")

    monkeypatch.setattr(topic_picker, "sheet_get", fail)
    assert topic_picker.queue_contains(story_id="NWS-001") is False
