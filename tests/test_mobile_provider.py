from datetime import datetime, timezone
import sys
from types import SimpleNamespace

from instagram.providers.mobile import MobileInstagramProvider


def _provider(client):
    provider = MobileInstagramProvider.__new__(MobileInstagramProvider)
    provider.client = client
    return provider


def test_mobile_onboarding_resolves_owner_from_seed_story():
    created = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    class FakeClient:
        def __init__(self):
            self.story_info_calls = []

        def story_info(self, story_id):
            self.story_info_calls.append(story_id)
            return SimpleNamespace(
                pk=story_id,
                user=SimpleNamespace(pk="28336121036", username="prof.tiacarol"),
                taken_at=created,
            )

        def user_info_by_username_v1(self, username):
            raise AssertionError("username fallback must not run when seed lookup passes")

    client = FakeClient()
    resolved = _provider(client).resolve_source("prof.tiacarol", "3995505476820383692")

    assert resolved.user_id == "28336121036"
    assert resolved.username == "prof.tiacarol"
    assert client.story_info_calls == ["3995505476820383692"]


def test_mobile_story_feed_normalizes_video_and_image_candidates():
    created = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    class FakeClient:
        def user_stories_v1(self, user_id):
            assert user_id == "42"
            return [
                SimpleNamespace(
                    pk="video-1",
                    id="video-1",
                    media_type=1,
                    taken_at=created,
                    thumbnail_url="https://example.invalid/video-thumb.jpg",
                    video_url="https://example.invalid/video.mp4",
                    video_duration=3.5,
                ),
                SimpleNamespace(
                    pk="image-1",
                    id="image-1",
                    media_type=1,
                    taken_at=created.replace(hour=11),
                    thumbnail_url="https://example.invalid/image.jpg",
                    video_url=None,
                    video_duration=None,
                ),
            ]

    stories = _provider(FakeClient()).list_stories("42", "prof.tiacarol")

    assert [story.provider_story_id for story in stories] == ["video-1", "image-1"]
    assert stories[0].media_type == "video"
    assert stories[0].video_url.endswith("video.mp4")
    assert stories[1].media_type == "image"
    assert stories[1].image_url.endswith("image.jpg")


def test_mobile_provider_uses_injected_connection_credential(monkeypatch):
    calls = []

    class FakeClient:
        def login_by_sessionid(self, sessionid):
            calls.append(sessionid)
            return True

    monkeypatch.setitem(sys.modules, "instagrapi", SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(
        "instagram.providers.mobile.create_loader",
        lambda: (_ for _ in ()).throw(AssertionError("global loader must not be used")),
    )

    MobileInstagramProvider(credential={"sessionid": "connection-secret"})

    assert calls == ["connection-secret"]
