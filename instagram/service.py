from __future__ import annotations

import hashlib
import inspect
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Callable

from instagram.auth.service import AuthConnectionError, AuthConnectionService
from instagram.downloader import MediaDownloadError, MediaDownloader
from instagram.parser import parse_story_permalink
from instagram.providers import (
    DirectInstagramProvider,
    MobileInstagramProvider,
    ProviderError,
    ProviderErrorCode,
    ResolvedUser,
    StoryProvider,
)
from instagram.providers.factory import ProviderFactory
from instagram.storage import SourceStore, utcnow_iso


class SourceServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


def default_provider_factory(source: dict | None = None) -> StoryProvider:
    return ProviderFactory().for_source(source or {})


def _snapshot_id(items: list[dict]) -> str:
    canonical = "|".join(
        f"{item['provider_story_id']}:{item['sha256']}:{item['provider_index']}"
        for item in items
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class SourceService:
    def __init__(
        self,
        store: SourceStore | None = None,
        provider_factory: Callable[..., StoryProvider] | None = None,
        downloader: MediaDownloader | None = None,
        auth_service: AuthConnectionService | None = None,
    ) -> None:
        self.store = store or SourceStore()
        self.auth_service = auth_service or AuthConnectionService()
        self.provider_factory = provider_factory
        self.provider_registry = ProviderFactory(self.auth_service)
        self.downloader = downloader or MediaDownloader()
        self._locks: dict[str, Lock] = {}
        self._locks_guard = Lock()

    def _lock_for(self, source_id: str) -> Lock:
        with self._locks_guard:
            return self._locks.setdefault(source_id, Lock())

    def _provider_for_source(self, source: dict) -> StoryProvider:
        if self.provider_factory is None:
            selected = self.auth_service.select_for_source(source)
            if (
                source.get("_new_source")
                and not source.get("auth_connection_id")
                and selected.type != "LEGACY_SERVER_SESSION"
            ):
                source["auth_connection_id"] = selected.id
            return self.provider_registry.for_source(source)

        try:
            parameters = inspect.signature(self.provider_factory).parameters.values()
            accepts_source = any(
                parameter.kind in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                }
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_source = False
        return self.provider_factory(source) if accepts_source else self.provider_factory()

    def _record_provider_auth_failure(self, source: dict, error: ProviderError) -> str:
        if (
            source.get("auth_connection_id")
            and error.code
            in {
                ProviderErrorCode.LOGIN_REQUIRED,
                ProviderErrorCode.CHALLENGE_REQUIRED,
                ProviderErrorCode.CHECKPOINT_REQUIRED,
                ProviderErrorCode.INVALID_SESSION,
            }
        ):
            self.auth_service.mark_reconnect_required(source["auth_connection_id"], error.code.value)
            return "RECONNECT_REQUIRED"
        return error.code.value

    def create_source(
        self,
        story_url: str,
        refresh: bool = False,
        auth_connection_id: str | None = None,
    ) -> dict:
        parsed = parse_story_permalink(story_url)
        existing = self.store.find_source_by_username(parsed.username)
        if existing:
            source = dict(existing)
            source["seed_story_id"] = parsed.seed_story_id
            source["canonical_permalink"] = parsed.canonical_permalink
            source["updated_at"] = utcnow_iso()
            if auth_connection_id:
                source["auth_connection_id"] = auth_connection_id
        else:
            source_id = self.store.source_id_for(parsed.username)
            source = {
                "source_id": source_id,
                "username": parsed.username,
                "instagram_user_id": None,
                "auth_connection_id": auth_connection_id,
                "seed_story_id": parsed.seed_story_id,
                "canonical_permalink": parsed.canonical_permalink,
                "created_at": utcnow_iso(),
                "updated_at": utcnow_iso(),
                "_new_source": True,
            }

        try:
            provider = self._provider_for_source(source)
        except ProviderError as exc:
            status = self._record_provider_auth_failure(source, exc)
            raise SourceServiceError(str(exc), code=status) from exc
        try:
            if source.get("instagram_user_id"):
                resolved = ResolvedUser(
                    user_id=str(source["instagram_user_id"]),
                    username=str(source["username"]).lower(),
                )
            else:
                resolved = provider.resolve_source(parsed.username, parsed.seed_story_id)
        except ProviderError as exc:
            status = self._record_provider_auth_failure(source, exc)
            raise SourceServiceError(str(exc), code=status) from exc
        source["username"] = resolved.username
        source["instagram_user_id"] = resolved.user_id
        source["updated_at"] = utcnow_iso()
        source.pop("_new_source", None)
        self.store.save_source(source)
        if refresh:
            self.refresh_source(source["source_id"])
        return self.get_source(source["source_id"])

    def get_source(self, source_id: str) -> dict:
        source = self.store.load_source(source_id)
        state = self.store.load_state(source_id)
        current = self.store.load_current(source_id)
        return {
            **source,
            "status": state.get("lastStatus", "NEW"),
            "active_count": len(current.get("items") or []),
            "state": state,
        }

    def list_sources(self) -> list[dict]:
        result = []
        for source in self.store.list_sources():
            try:
                result.append(self.get_source(source["source_id"]))
            except (FileNotFoundError, ValueError):
                continue
        return result

    def current_snapshot(self, source_id: str) -> dict:
        self.store.load_source(source_id)
        return self.store.load_current(source_id)

    def refresh_source(self, source_id: str) -> dict:
        lock = self._lock_for(source_id)
        if not lock.acquire(blocking=False):
            return {"source_id": source_id, "status": "LOCKED", "changed": False}
        try:
            return self._refresh_locked(source_id)
        finally:
            lock.release()

    def _refresh_locked(self, source_id: str) -> dict:
        source = self.store.load_source(source_id)
        state = self.store.load_state(source_id)
        previous = self.store.load_current(source_id)
        previous_items = previous.get("items") or []
        previous_ids = {item["provider_story_id"] for item in previous_items}
        now = utcnow_iso()

        state["lastAttemptAt"] = now
        self.store.save_state(source_id, state)

        try:
            provider = self._provider_for_source(source)
            stories = provider.list_stories(source["instagram_user_id"], source["username"])
            if source.get("auth_connection_id"):
                self.auth_service.mark_validated(source["auth_connection_id"])
        except AuthConnectionError as exc:
            state.update(
                {
                    "lastStatus": exc.code,
                    "consecutiveFailures": int(state.get("consecutiveFailures", 0)) + 1,
                }
            )
            self.store.save_state(source_id, state)
            return {
                "source_id": source_id,
                "status": exc.code,
                "changed": False,
                "error": str(exc),
            }
        except ProviderError as exc:
            status = self._record_provider_auth_failure(source, exc)
            state.update(
                {
                    "lastStatus": status,
                    "consecutiveFailures": int(state.get("consecutiveFailures", 0)) + 1,
                }
            )
            self.store.save_state(source_id, state)
            error = (
                "A conexão Instagram precisa ser reconectada."
                if status == "RECONNECT_REQUIRED"
                else str(exc)
            )
            return {"source_id": source_id, "status": status, "changed": False, "error": error}

        discovered = len(stories)
        if previous_ids and discovered == 0:
            state.update(
                {
                    "lastStatus": "SUSPICIOUS_EMPTY_SNAPSHOT",
                    "consecutiveFailures": int(state.get("consecutiveFailures", 0)) + 1,
                }
            )
            self.store.save_state(source_id, state)
            return {
                "source_id": source_id,
                "status": "SUSPICIOUS_EMPTY_SNAPSHOT",
                "discovered": 0,
                "changed": False,
            }

        current_story_ids = {story.provider_story_id for story in stories}
        if previous_ids and discovered < max(1, len(previous_ids) / 2):
            signature = hashlib.sha256(
                "|".join(sorted(current_story_ids)).encode("utf-8")
            ).hexdigest()
            if state.get("pendingLargeDropSignature") != signature:
                state.update(
                    {
                        "lastStatus": "SUSPICIOUS_LARGE_DROP",
                        "pendingLargeDropSignature": signature,
                        "consecutiveFailures": int(state.get("consecutiveFailures", 0)) + 1,
                    }
                )
                self.store.save_state(source_id, state)
                return {
                    "source_id": source_id,
                    "status": "SUSPICIOUS_LARGE_DROP",
                    "discovered": discovered,
                    "changed": False,
                }

        catalog = self.store.load_catalog(source_id)
        catalog_items = catalog.setdefault("items", {})
        media_dir = self.store.paths(source_id).media
        current_items: list[dict] = []
        downloaded = 0
        reused = 0

        try:
            for story in stories:
                existing = catalog_items.get(story.provider_story_id)
                if existing:
                    filename = existing.get("filename")
                    media_path = self.store.media_path(source_id, filename) if filename else None
                    if media_path and media_path.exists() and media_path.stat().st_size == int(existing.get("bytes", -1)):
                        item = dict(existing)
                        item.update(
                            {
                                "provider_index": story.provider_index,
                                "taken_at": story.taken_at.isoformat(),
                                "expires_at": story.expires_at.isoformat(),
                                "last_seen_at": now,
                                "active": True,
                            }
                        )
                        current_items.append(item)
                        catalog_items[story.provider_story_id] = item
                        reused += 1
                        continue

                media = self.downloader.download(story, media_dir)
                item = {
                    "provider_story_id": story.provider_story_id,
                    "provider_user_id": story.provider_user_id,
                    "username": story.username,
                    "media_type": story.media_type,
                    "taken_at": story.taken_at.isoformat(),
                    "expires_at": story.expires_at.isoformat(),
                    "provider_index": story.provider_index,
                    "sha256": media["sha256"],
                    "filename": media["filename"],
                    "bytes": media["bytes"],
                    "content_type": media.get("content_type"),
                    "first_seen_at": existing.get("first_seen_at") if existing else now,
                    "last_seen_at": now,
                    "active": True,
                    "duration": media.get("duration", story.duration),
                    "has_audio": media.get("has_audio"),
                    "width": media.get("width"),
                    "height": media.get("height"),
                }
                catalog_items[story.provider_story_id] = item
                current_items.append(item)
                downloaded += 1
        except (MediaDownloadError, OSError, ValueError) as exc:
            state.update(
                {
                    "lastStatus": "PARTIAL",
                    "consecutiveFailures": int(state.get("consecutiveFailures", 0)) + 1,
                }
            )
            self.store.save_state(source_id, state)
            return {
                "source_id": source_id,
                "status": "PARTIAL",
                "discovered": discovered,
                "valid": len(current_items),
                "downloaded": downloaded,
                "reused": reused,
                "changed": False,
                "error": str(exc),
            }

        current_items.sort(
            key=lambda item: (item.get("taken_at", ""), item["provider_story_id"]),
            reverse=True,
        )
        for index, item in enumerate(current_items):
            item["provider_index"] = index

        new_ids = current_story_ids - previous_ids
        expired_ids = previous_ids - current_story_ids
        for expired_id in expired_ids:
            if expired_id in catalog_items:
                catalog_items[expired_id]["active"] = False
                catalog_items[expired_id]["inactive_since"] = now

        snapshot = {
            "source_id": source_id,
            "username": source["username"],
            "status": "COMPLETE",
            "snapshot_id": _snapshot_id(current_items),
            "collected_at": now,
            "items": current_items,
        }

        changed = bool(new_ids or expired_ids) or previous.get("snapshot_id") != snapshot["snapshot_id"]
        self.store.save_catalog(source_id, catalog)
        self.store.save_current(source_id, snapshot)

        state.update(
            {
                "lastCompleteAt": now,
                "lastStatus": "COMPLETE",
                "activeCount": len(current_items),
                "inactiveCount": sum(1 for item in catalog_items.values() if not item.get("active")),
                "consecutiveFailures": 0,
                "pendingLargeDropSignature": None,
            }
        )
        if changed:
            state["lastContentChangeAt"] = now
        if new_ids:
            state["lastNewMediaAt"] = now
        self.store.save_state(source_id, state)

        return {
            "source_id": source_id,
            "status": "COMPLETE",
            "snapshot_id": snapshot["snapshot_id"],
            "discovered": discovered,
            "valid": len(current_items),
            "downloaded": downloaded,
            "reused": reused,
            "new": len(new_ids),
            "expired": len(expired_ids),
            "changed": changed,
        }

    def mark_stale_sources(self, stale_minutes: int | None = None) -> None:
        stale_minutes = stale_minutes or int(os.getenv("STORY_STALE_MINUTES", "35"))
        now = datetime.now(timezone.utc)
        for source in self.store.list_sources():
            source_id = source["source_id"]
            state = self.store.load_state(source_id)
            last_complete = state.get("lastCompleteAt")
            if not last_complete:
                continue
            try:
                completed = datetime.fromisoformat(last_complete)
            except ValueError:
                continue
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            if (now - completed).total_seconds() > stale_minutes * 60:
                state["lastStatus"] = "STALE"
                self.store.save_state(source_id, state)
