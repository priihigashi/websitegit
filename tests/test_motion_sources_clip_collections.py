import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "content_creator"))

import motion_sources as ms


def test_extract_urls_from_clip_collection_links_cell():
    links = (
        "https://www.pexels.com/video/man-choosing-colors-6474156/ | "
        "https://www.youtube.com/watch?v=yvi9GE9mfIU [tutorial]"
    )

    assert ms._extract_urls(links) == [
        "https://www.pexels.com/video/man-choosing-colors-6474156/",
        "https://www.youtube.com/watch?v=yvi9GE9mfIU",
    ]


def test_topic_tokens_normalize_remodel_terms():
    assert ms._topic_tokens("Bathroom remodel planning") == {"bathroom", "remodel"}
    assert "remodel" in ms._topic_tokens("Wrong time to remodeling / renovation")


def test_video_byte_guard_rejects_html_and_accepts_mp4_header():
    html = b"<!doctype html><html>" + (b"x" * 20_000)
    mp4 = b"\x00\x00\x00\x18ftypmp42" + (b"x" * 20_000)

    assert ms._looks_like_video_bytes(html) is False
    assert ms._looks_like_video_bytes(mp4) is True
