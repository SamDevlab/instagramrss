import asyncio
from datetime import datetime, timedelta, timezone

import httpx

import app as app_module
from instagram.models import StoryMedia


def test_health_and_viewer_are_served():
    async def request_pages():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health"), await client.get("/")

    health, viewer = asyncio.run(request_pages())

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert viewer.status_code == 200
    assert "Instagram Stories Viewer" in viewer.text
    assert 'id="profileInput"' in viewer.text


def test_story_and_rss_endpoints_use_only_the_mocked_collector(monkeypatch):
    created = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
    story = StoryMedia(
        id="123",
        username="nasa",
        media_type="image",
        media_url="https://example.com/story.jpg",
        created_at=created,
        expires_at=created + timedelta(hours=24),
    )

    def fake_fetch(self, username):
        assert username == "nasa"
        return [story]

    monkeypatch.setattr(app_module.StoryCollector, "fetch", fake_fetch)
    async def request_endpoints():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            story_responses = [
                await client.get("/stories/nasa"),
                await client.get("/stories?profile=%40nasa"),
            ]
            rss_responses = [
                await client.get("/rss/stories/nasa"),
                await client.get("/rss/stories?profile=%40nasa"),
            ]
            return story_responses, rss_responses

    story_responses, rss_responses = asyncio.run(request_endpoints())

    for response in story_responses:
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["stories"][0]["id"] == "123"

    for response in rss_responses:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/rss+xml")
        assert "instagram-story-123" in response.text
