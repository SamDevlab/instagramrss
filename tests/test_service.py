import hashlib
from datetime import datetime, timedelta, timezone

from instagram.providers.base import ProviderStory, ResolvedUser, StoryProvider
from instagram.service import SourceService
from instagram.storage import SourceStore


def make_story(story_id: str, hour: int) -> ProviderStory:
    taken = datetime(2026, 9, 20, hour, 0, tzinfo=timezone.utc)
    return ProviderStory(
        provider_story_id=story_id,
        provider_user_id="42",
        username="nasa",
        media_type="image",
        taken_at=taken,
        expires_at=taken + timedelta(hours=24),
        image_url=f"https://example.invalid/{story_id}.jpg",
        video_url=None,
        duration=None,
        provider_index=0,
    )


class MutableProvider(StoryProvider):
    stories = [make_story("a", 12), make_story("b", 11)]

    def resolve_user(self, username: str) -> ResolvedUser:
        return ResolvedUser(user_id="42", username=username)

    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        return list(self.__class__.stories)


class FakeDownloader:
    calls = 0
    fail_on = None

    def download(self, story, media_dir):
        type(self).calls += 1
        if story.provider_story_id == self.fail_on:
            raise OSError("download failed")
        data = b"image-" + story.provider_story_id.encode()
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


def make_service(tmp_path):
    MutableProvider.stories = [make_story("a", 12), make_story("b", 11)]
    FakeDownloader.calls = 0
    downloader = FakeDownloader()
    downloader.fail_on = None
    return SourceService(
        store=SourceStore(tmp_path / "data"),
        provider_factory=MutableProvider,
        downloader=downloader,
    ), downloader


def test_second_complete_run_reuses_files(tmp_path):
    service, _ = make_service(tmp_path)
    source = service.create_source("https://www.instagram.com/stories/nasa/999/")

    first = service.refresh_source(source["source_id"])
    second = service.refresh_source(source["source_id"])

    assert first["status"] == "COMPLETE"
    assert first["downloaded"] == 2
    assert second["status"] == "COMPLETE"
    assert second["downloaded"] == 0
    assert second["reused"] == 2
    assert FakeDownloader.calls == 2


def test_new_story_and_expiration_update_snapshot(tmp_path):
    service, _ = make_service(tmp_path)
    source = service.create_source("https://www.instagram.com/stories/nasa/999/")
    service.refresh_source(source["source_id"])

    MutableProvider.stories = [
        make_story("c", 13),
        make_story("a", 12),
    ]
    result = service.refresh_source(source["source_id"])
    snapshot = service.current_snapshot(source["source_id"])

    assert result["status"] == "COMPLETE"
    assert result["new"] == 1
    assert result["expired"] == 1
    assert [item["provider_story_id"] for item in snapshot["items"]] == ["c", "a"]


def test_empty_snapshot_preserves_previous_complete(tmp_path):
    service, _ = make_service(tmp_path)
    source = service.create_source("https://www.instagram.com/stories/nasa/999/")
    service.refresh_source(source["source_id"])
    before = service.current_snapshot(source["source_id"])

    MutableProvider.stories = []
    result = service.refresh_source(source["source_id"])
    after = service.current_snapshot(source["source_id"])

    assert result["status"] == "SUSPICIOUS_EMPTY_SNAPSHOT"
    assert after["snapshot_id"] == before["snapshot_id"]


def test_partial_download_preserves_previous_complete(tmp_path):
    service, downloader = make_service(tmp_path)
    source = service.create_source("https://www.instagram.com/stories/nasa/999/")
    service.refresh_source(source["source_id"])
    before = service.current_snapshot(source["source_id"])

    MutableProvider.stories = [make_story("c", 13), make_story("a", 12)]
    downloader.fail_on = "c"
    result = service.refresh_source(source["source_id"])
    after = service.current_snapshot(source["source_id"])

    assert result["status"] == "PARTIAL"
    assert after["snapshot_id"] == before["snapshot_id"]
