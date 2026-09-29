from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from instagram.models import StoryMedia
from rss.builder import MEDIA_NS, build_source_rss, build_story_rss


def test_build_story_rss_contains_media_content():
    created = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
    story = StoryMedia(
        id="123",
        username="nasa",
        media_type="image",
        media_url="https://example.com/story.jpg",
        created_at=created,
        expires_at=created + timedelta(hours=24),
    )

    xml = build_story_rss("nasa", [story])
    root = ET.fromstring(xml)

    item = root.find("./channel/item")
    assert item is not None
    assert item.find("guid").text == "instagram-story-123"

    media = item.find(f"{{{MEDIA_NS}}}content")
    assert media is not None
    assert media.attrib["url"] == "https://example.com/story.jpg"
    assert media.attrib["type"] == "image/jpeg"


def test_build_source_rss_uses_local_media_url():
    source = {"source_id": "ig_abc", "username": "nasa"}
    snapshot = {
        "items": [
            {
                "provider_story_id": "123",
                "provider_index": 0,
                "taken_at": "2026-09-16T20:00:00+00:00",
                "expires_at": "2026-09-17T20:00:00+00:00",
                "media_type": "video",
                "filename": "abc.mp4",
                "content_type": "video/mp4",
            }
        ]
    }
    xml = build_source_rss(source, snapshot, public_base_url="https://rss.example")
    root = ET.fromstring(xml)
    media = root.find(f"./channel/item/{{{MEDIA_NS}}}content")
    assert media is not None
    assert media.attrib["url"] == "https://rss.example/media/ig_abc/abc.mp4"
