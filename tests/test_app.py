import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import httpx

import app as app_module
from instagram.providers.base import ProviderStory, ResolvedUser, StoryProvider
from instagram.service import SourceService
from instagram.storage import SourceStore


class FakeProvider(StoryProvider):
    def resolve_user(self, username: str) -> ResolvedUser:
        return ResolvedUser(user_id="42", username=username.lower())

    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        created = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)
        return [
            ProviderStory(
                provider_story_id="123",
                provider_user_id=user_id,
                username=username,
                media_type="image",
                taken_at=created,
                expires_at=created + timedelta(hours=24),
                image_url="https://example.invalid/story.jpg",
                video_url=None,
                duration=None,
                provider_index=0,
            )
        ]


class FakeDownloader:
    def download(self, story, media_dir):
        data = b"fake-image-bytes"
        digest = hashlib.sha256(data).hexdigest()
        filename = f"{digest}.jpg"
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / filename).write_bytes(data)
        return {
            "sha256": digest,
            "filename": filename,
            "bytes": len(data),
            "content_type": "image/jpeg",
        }


def _install_test_service(monkeypatch, tmp_path):
    store = SourceStore(tmp_path / "data")
    service = SourceService(
        store=store,
        provider_factory=FakeProvider,
        downloader=FakeDownloader(),
    )
    monkeypatch.setattr(app_module, "source_store", store)
    monkeypatch.setattr(app_module, "source_service", service)
    return service


def test_health_and_viewer_are_served():
    async def request_pages():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health"), await client.get("/")

    health, viewer = asyncio.run(request_pages())

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert viewer.status_code == 200
    assert "Instagram Stories Viewer" in viewer.text
    assert 'id="profileInput"' in viewer.text


def test_source_registration_refresh_and_legacy_aliases(monkeypatch, tmp_path):
    _install_test_service(monkeypatch, tmp_path)

    async def request_endpoints():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/sources",
                json={
                    "story_url": "https://www.instagram.com/stories/nasa/999/",
                    "refresh": True,
                },
            )
            source_id = created.json()["source_id"]
            stories = await client.get(f"/sources/{source_id}/stories")
            media_url = stories.json()["stories"][0]["media_url"]
            return (
                created,
                stories,
                await client.get(f"/sources/{source_id}/rss.xml"),
                await client.get("/stories/nasa"),
                await client.get("/rss/stories/nasa"),
                await client.get(media_url),
                await client.get(media_url, headers={"Range": "bytes=0-3"}),
            )

    created, stories, rss, legacy_stories, legacy_rss, media, ranged = asyncio.run(request_endpoints())

    assert created.status_code == 200
    assert created.json()["username"] == "nasa"

    for response in (stories, legacy_stories):
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["stories"][0]["id"] == "123"
        assert response.json()["stories"][0]["media_url"].startswith("/media/")

    for response in (rss, legacy_rss):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/rss+xml")
        assert "instagram-story-123" in response.text
        assert "/media/" in response.text

    assert media.status_code == 200
    assert media.content == b"fake-image-bytes"
    assert ranged.status_code in {200, 206}
    if ranged.status_code == 206:
        assert ranged.content == b"fake"
