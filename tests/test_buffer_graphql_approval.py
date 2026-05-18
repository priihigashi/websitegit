import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTENT_CREATOR = ROOT / "scripts" / "content_creator"
sys.path.insert(0, str(CONTENT_CREATOR))

import approval_handler as ah  # noqa: E402


def test_buffer_find_channel_skips_locked_and_disconnected(monkeypatch):
    payload = {
        "data": {
            "account": {
                "organizations": [
                    {
                        "name": "My Organization",
                        "channels": [
                            {
                                "id": "locked",
                                "service": "instagram",
                                "displayName": "wrong",
                                "isLocked": True,
                                "isDisconnected": False,
                            },
                            {
                                "id": "healthy",
                                "service": "instagram",
                                "displayName": "oakparkconstruction",
                                "isLocked": False,
                                "isDisconnected": False,
                            },
                        ],
                    }
                ]
            }
        }
    }
    monkeypatch.delenv("BUFFER_OPC_INSTAGRAM_PROFILE_ID", raising=False)
    monkeypatch.setattr(ah, "_buffer_graphql", lambda query, variables=None: payload)

    channel = ah._buffer_find_channel("instagram")

    assert channel["id"] == "healthy"


def test_buffer_create_graphql_post_uses_instagram_feed_metadata(monkeypatch):
    captured = {}

    def fake_graphql(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post_123", "dueAt": None, "status": "queued", "text": "caption"},
                }
            }
        }

    monkeypatch.setattr(ah, "_buffer_graphql", fake_graphql)

    post = ah._buffer_create_graphql_post("channel_123", "caption", ["https://example.com/a.png"])

    post_input = captured["variables"]["input"]
    assert post["id"] == "post_123"
    assert post_input["channelId"] == "channel_123"
    assert post_input["metadata"]["instagram"] == {
        "type": "post",
        "shouldShareToFeed": True,
    }
    assert post_input["assets"] == [{"image": {"url": "https://example.com/a.png"}}]
    assert post_input["mode"] == "addToQueue"
