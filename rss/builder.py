from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.etree import ElementTree as ET

from instagram.models import StoryMedia


MEDIA_NS = "http://search.yahoo.com/mrss/"
ET.register_namespace("media", MEDIA_NS)


def _rss_date(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return format_datetime(value)


def build_story_rss(username: str, stories: list[StoryMedia]) -> str:
    """Legacy RSS builder kept for backwards compatibility."""
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")

    ET.SubElement(channel, "title").text = f"Instagram Stories - @{username}"
    ET.SubElement(channel, "link").text = f"https://www.instagram.com/{username}/"
    ET.SubElement(channel, "description").text = f"Stories ativos do Instagram de @{username}"
    ET.SubElement(channel, "lastBuildDate").text = _rss_date(datetime.now(timezone.utc))

    for story in stories:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"Story de @{username}"
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"instagram-story-{story.id}"
        ET.SubElement(item, "pubDate").text = _rss_date(story.created_at)
        ET.SubElement(item, "description").text = (
            f"Story {story.media_type} de @{username}; expira em {story.expires_at.isoformat()}"
        )
        ET.SubElement(
            item,
            f"{{{MEDIA_NS}}}content",
            {
                "url": story.media_url,
                "type": "video/mp4" if story.media_type == "video" else "image/jpeg",
                "medium": story.media_type,
            },
        )

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def build_source_rss(
    source: dict,
    snapshot: dict,
    public_base_url: str = "",
) -> str:
    """Build production RSS using only locally persisted media URLs."""
    username = source["username"]
    source_id = source["source_id"]
    base = public_base_url.rstrip("/")

    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = f"Instagram Stories - @{username}"
    ET.SubElement(channel, "link").text = f"https://www.instagram.com/{username}/"
    ET.SubElement(channel, "description").text = f"Stories ativos do Instagram de @{username}"
    ET.SubElement(channel, "lastBuildDate").text = _rss_date(datetime.now(timezone.utc))

    for story in snapshot.get("items") or []:
        item = ET.SubElement(channel, "item")
        story_id = str(story["provider_story_id"])
        media_type = story.get("media_type", "image")
        filename = quote(str(story["filename"]))
        media_url = f"{base}/media/{source_id}/{filename}" if base else f"/media/{source_id}/{filename}"
        created = datetime.fromisoformat(story["taken_at"])
        expires = story.get("expires_at")

        ET.SubElement(item, "title").text = f"Story de @{username}"
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"instagram-story-{story_id}"
        ET.SubElement(item, "pubDate").text = _rss_date(created)
        description = f"Story {media_type} de @{username}"
        if expires:
            description += f"; expira em {expires}"
        ET.SubElement(item, "description").text = description
        ET.SubElement(
            item,
            f"{{{MEDIA_NS}}}content",
            {
                "url": media_url,
                "type": story.get("content_type")
                or ("video/mp4" if media_type == "video" else "image/jpeg"),
                "medium": media_type,
            },
        )

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
