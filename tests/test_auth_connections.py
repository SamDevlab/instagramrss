import base64
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import httpx

import app as app_module

from instagram.auth.crypto import CredentialStoreError, EncryptedCredentialStore
from instagram.auth.models import (
    AuthConnection,
    AuthConnectionStatus,
    AuthConnectionType,
)
from instagram.auth.service import AuthConnectionError, AuthConnectionService
from instagram.auth.store import AuthConnectionStore
from instagram.providers.base import ProviderError, ProviderErrorCode, ProviderStory, ResolvedUser, StoryProvider
from instagram.providers.factory import ProviderFactory
from instagram.scheduler import StoryScheduler
from instagram.service import SourceService
from instagram.storage import SourceStore
from rss.builder import build_source_rss


MASTER_KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


def make_auth_service(tmp_path: Path) -> AuthConnectionService:
    return AuthConnectionService(
        store=AuthConnectionStore(tmp_path / "connections.json"),
        credentials=EncryptedCredentialStore(
            tmp_path / "credentials.json",
            master_key=base64.urlsafe_b64decode(MASTER_KEY),
        ),
    )


def make_connection(auth_service: AuthConnectionService, username: str, secret: str):
    return auth_service.create_instagram_session_connection(
        username,
        {"sessionid": secret, "cookies": {"sessionid": secret}},
    )


def make_story(story_id: str, user_id: str = "42") -> ProviderStory:
    taken = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    return ProviderStory(
        provider_story_id=story_id,
        provider_user_id=user_id,
        username="target",
        media_type="image",
        taken_at=taken,
        expires_at=taken + timedelta(hours=24),
        image_url=f"https://example.invalid/{story_id}.jpg",
        video_url=None,
        duration=None,
        provider_index=0,
    )


class CredentialBoundProvider(StoryProvider):
    def __init__(self, connection_id: str, secret: str, *, fail: ProviderError | None = None):
        self.connection_id = connection_id
        self.secret = secret
        self.fail = fail
        self.resolve_source_calls = 0
        self.list_calls = 0

    def resolve_source(self, username: str, seed_story_id: str | None = None) -> ResolvedUser:
        self.resolve_source_calls += 1
        return ResolvedUser(user_id="42", username=username.lower())

    def resolve_user(self, username: str) -> ResolvedUser:
        return ResolvedUser(user_id="42", username=username.lower())

    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        self.list_calls += 1
        if self.fail:
            raise self.fail
        return [make_story(f"story-{self.connection_id}", user_id)]


class FakeDownloader:
    def download(self, story, media_dir):
        data = f"media-{story.provider_story_id}".encode()
        digest = hashlib.sha256(data).hexdigest()
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / f"{digest}.jpg").write_bytes(data)
        return {
            "sha256": digest,
            "filename": f"{digest}.jpg",
            "bytes": len(data),
            "content_type": "image/jpeg",
        }


class RecordingFactory:
    def __init__(self, auth_service: AuthConnectionService):
        self.auth_service = auth_service
        self.providers = {}
        self.selected = []

    def for_source(self, source):
        connection = self.auth_service.select_for_source(source)
        credential = self.auth_service.credential_for(connection)
        secret = credential["sessionid"]
        self.selected.append(connection.id)
        provider = self.providers.get(connection.id)
        if provider is None:
            provider = CredentialBoundProvider(connection.id, secret)
            self.providers[connection.id] = provider
        return provider


def test_source_uses_exact_bound_auth_connection_and_isolates_clients(tmp_path):
    auth_service = make_auth_service(tmp_path)
    auth_a = make_connection(auth_service, "clienta", "secret-a")
    auth_b = make_connection(auth_service, "clientb", "secret-b")
    factory = RecordingFactory(auth_service)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=factory.for_source,
        downloader=FakeDownloader(),
    )

    source_a = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=auth_a.id,
    )
    source_b = source_service.create_source(
        "https://www.instagram.com/stories/other/222/",
        auth_connection_id=auth_b.id,
    )
    source_service.refresh_source(source_a["source_id"])
    source_service.refresh_source(source_b["source_id"])

    assert source_a["auth_connection_id"] == auth_a.id
    assert source_b["auth_connection_id"] == auth_b.id
    assert factory.selected == [auth_a.id, auth_b.id, auth_a.id, auth_b.id]
    assert factory.providers[auth_a.id].secret == "secret-a"
    assert factory.providers[auth_b.id].secret == "secret-b"


def test_invalid_session_marks_connection_and_preserves_snapshot(tmp_path):
    auth_service = make_auth_service(tmp_path)
    connection = make_connection(auth_service, "clienta", "secret-a")
    factory = RecordingFactory(auth_service)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=factory.for_source,
        downloader=FakeDownloader(),
    )
    source = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=connection.id,
    )
    first = source_service.refresh_source(source["source_id"])
    before = source_service.current_snapshot(source["source_id"])

    factory.providers[connection.id].fail = ProviderError(
        ProviderErrorCode.LOGIN_REQUIRED,
        "login_required sessionid=secret-a",
    )
    failed = source_service.refresh_source(source["source_id"])

    assert first["status"] == "COMPLETE"
    assert failed["status"] == "RECONNECT_REQUIRED"
    assert "secret-a" not in str(failed)
    assert source_service.current_snapshot(source["source_id"]) == before
    media_file = next((tmp_path / "data" / "sources" / source["source_id"] / "media").glob("*"))
    assert media_file.exists()
    rss = build_source_rss(
        source_service.store.load_source(source["source_id"]),
        before,
        public_base_url="http://test",
    )
    assert "/media/" in rss
    updated = auth_service.get(connection.id)
    assert updated.status == AuthConnectionStatus.RECONNECT_REQUIRED.value
    assert updated.last_error and "secret-a" not in updated.last_error


def test_encrypted_credentials_never_appear_in_source_or_public_api(tmp_path):
    auth_service = make_auth_service(tmp_path)
    connection = make_connection(auth_service, "clienta", "secret-a")
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=lambda: CredentialBoundProvider(connection.id, "secret-a"),
        downloader=FakeDownloader(),
    )
    source = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=connection.id,
    )

    source_json = (tmp_path / "data" / "sources" / source["source_id"] / "source.json").read_text()
    credentials_json = (tmp_path / "credentials.json").read_text()

    assert "secret-a" not in source_json
    assert "secret-a" not in credentials_json
    assert "sessionid" not in source_json
    assert "secret-a" not in str(connection.public_dict())
    assert "credential_ref" not in connection.public_dict()


def test_missing_master_key_fails_without_plaintext_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_CREDENTIAL_MASTER_KEY", raising=False)
    store = EncryptedCredentialStore(tmp_path / "credentials.json")
    with pytest.raises(CredentialStoreError, match="AUTH_CREDENTIAL_MASTER_KEY"):
        store.put({"sessionid": "secret-a"})


def test_disconnect_revokes_credential_but_keeps_source_history(tmp_path):
    auth_service = make_auth_service(tmp_path)
    connection = make_connection(auth_service, "clienta", "secret-a")
    factory = RecordingFactory(auth_service)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=factory.for_source,
        downloader=FakeDownloader(),
    )
    source = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=connection.id,
    )
    source_service.refresh_source(source["source_id"])
    before = source_service.current_snapshot(source["source_id"])
    revoked = auth_service.disconnect(connection.id)

    result = source_service.refresh_source(source["source_id"])
    assert revoked.status == AuthConnectionStatus.REVOKED.value
    assert revoked.credential_ref is None
    assert result["status"] == AuthConnectionStatus.REVOKED.value
    assert source_service.current_snapshot(source["source_id"]) == before
    assert source_service.store.load_source(source["source_id"])["auth_connection_id"] == connection.id


def test_legacy_source_without_auth_connection_keeps_working(tmp_path, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USERNAME", "legacy-owner")
    monkeypatch.setenv("INSTAGRAM_SESSION_FILE", "./session/legacy.session")
    auth_service = make_auth_service(tmp_path)
    legacy_provider = CredentialBoundProvider("legacy", "legacy-secret")
    monkeypatch.setattr(ProviderFactory, "_legacy_provider", staticmethod(lambda: legacy_provider))
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        downloader=FakeDownloader(),
    )

    source = source_service.create_source("https://www.instagram.com/stories/target/111/")
    result = source_service.refresh_source(source["source_id"])

    assert "auth_connection_id" not in source or source["auth_connection_id"] is None
    assert result["status"] == "COMPLETE"


def test_new_source_binds_the_only_active_connection(tmp_path, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_USERNAME", raising=False)
    monkeypatch.delenv("INSTAGRAM_SESSION_FILE", raising=False)
    auth_service = make_auth_service(tmp_path)
    connection = make_connection(auth_service, "clienta", "secret-a")
    provider = CredentialBoundProvider(connection.id, "secret-a")
    monkeypatch.setattr(ProviderFactory, "for_source", lambda self, source: provider)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        downloader=FakeDownloader(),
    )

    source = source_service.create_source("https://www.instagram.com/stories/target/111/")

    assert source["auth_connection_id"] == connection.id


def test_refresh_does_not_resolve_seed_again(tmp_path):
    auth_service = make_auth_service(tmp_path)
    connection = make_connection(auth_service, "clienta", "secret-a")
    factory = RecordingFactory(auth_service)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=factory.for_source,
        downloader=FakeDownloader(),
    )
    source = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=connection.id,
    )
    provider = factory.providers[connection.id]
    source_service.refresh_source(source["source_id"])
    source_service.refresh_source(source["source_id"])

    assert provider.resolve_source_calls == 1
    assert provider.list_calls == 2


def test_meta_connection_is_rejected_by_capability_contract(tmp_path):
    auth_service = make_auth_service(tmp_path)
    connection = AuthConnection(
        id="auth_meta",
        type=AuthConnectionType.META_OAUTH.value,
        status=AuthConnectionStatus.ACTIVE.value,
        credential_ref="cred_meta",
        capabilities=["own_authorized_account_stories"],
        subject_username="authorized",
        created_at=auth_service.store.now(),
        updated_at=auth_service.store.now(),
    )
    auth_service.store.save(connection)

    with pytest.raises(AuthConnectionError) as error:
        ProviderFactory(auth_service).for_source(
            {"auth_connection_id": connection.id, "username": "target"}
        )

    assert error.value.code == "AUTH_CONNECTION_CAPABILITY_MISMATCH"


def test_scheduler_selects_connection_per_source(tmp_path):
    auth_service = make_auth_service(tmp_path)
    auth_a = make_connection(auth_service, "clienta", "secret-a")
    auth_b = make_connection(auth_service, "clientb", "secret-b")
    factory = RecordingFactory(auth_service)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=factory.for_source,
        downloader=FakeDownloader(),
    )
    source_a = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=auth_a.id,
    )
    source_b = source_service.create_source(
        "https://www.instagram.com/stories/other/222/",
        auth_connection_id=auth_b.id,
    )

    results = StoryScheduler(source_service).run_once()

    assert {item["source_id"] for item in results} == {source_a["source_id"], source_b["source_id"]}
    assert factory.selected[-2:] == [auth_a.id, auth_b.id]


def test_auth_a_failure_does_not_stop_auth_b(tmp_path):
    auth_service = make_auth_service(tmp_path)
    auth_a = make_connection(auth_service, "clienta", "secret-a")
    auth_b = make_connection(auth_service, "clientb", "secret-b")
    factory = RecordingFactory(auth_service)
    source_service = SourceService(
        store=SourceStore(tmp_path / "data"),
        auth_service=auth_service,
        provider_factory=factory.for_source,
        downloader=FakeDownloader(),
    )
    source_a = source_service.create_source(
        "https://www.instagram.com/stories/target/111/",
        auth_connection_id=auth_a.id,
    )
    source_b = source_service.create_source(
        "https://www.instagram.com/stories/other/222/",
        auth_connection_id=auth_b.id,
    )
    factory.providers[auth_a.id].fail = ProviderError(
        ProviderErrorCode.LOGIN_REQUIRED,
        "login_required",
    )

    results = StoryScheduler(source_service).run_once()
    by_source = {item["source_id"]: item for item in results}

    assert by_source[source_a["source_id"]]["status"] == "RECONNECT_REQUIRED"
    assert by_source[source_b["source_id"]]["status"] == "COMPLETE"
    assert auth_service.get(auth_b.id).status == AuthConnectionStatus.ACTIVE.value


def test_auth_connection_api_exposes_metadata_only(monkeypatch, tmp_path):
    auth_service = make_auth_service(tmp_path)
    connection = make_connection(auth_service, "clienta", "secret-a")
    monkeypatch.setattr(app_module, "auth_connection_service", auth_service)

    async def request():
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/auth-connections"), await client.delete(
                f"/auth-connections/{connection.id}"
            )

    listed, deleted = asyncio.run(request())

    assert listed.status_code == 200
    assert "secret-a" not in listed.text
    assert "credential_ref" not in listed.text
    assert deleted.status_code == 200
    assert "secret-a" not in deleted.text
