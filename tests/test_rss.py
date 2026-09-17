from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from instagram.models import StoryMedia
from rss.builder import MEDIA_NS, build_story_rss


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
