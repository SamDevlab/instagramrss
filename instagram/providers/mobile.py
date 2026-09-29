from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from instagram.providers.base import (
    ProviderError,
    ProviderErrorCode,
    ProviderStory,
    ResolvedUser,
    StoryProvider,
)
from instagram.session import InstagramSessionError, create_loader
from instagram.auth.crypto import redact_sensitive_message


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _attr(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return default


def _error_code(exc: BaseException) -> ProviderErrorCode:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if "private" in message or "not authorized to view" in message or "access denied" in message:
        return ProviderErrorCode.TARGET_ACCESS_DENIED
    if "not found" in message or "does not exist" in message:
        return ProviderErrorCode.TARGET_NOT_FOUND
    if "auth_invalid" in message or "authorization required" in message:
        return ProviderErrorCode.AUTH_INVALID
    if "429" in message or "toomanyrequests" in name or "rate limit" in message:
        return ProviderErrorCode.RATE_LIMITED
    if "feedback" in message:
        return ProviderErrorCode.FEEDBACK_REQUIRED
    if "challenge" in name or "challenge" in message:
        return ProviderErrorCode.CHALLENGE_REQUIRED
    if "checkpoint" in name or "checkpoint" in message:
        return ProviderErrorCode.CHECKPOINT_REQUIRED
    if "login" in name or "login_required" in message or "not logged" in message:
        return ProviderErrorCode.LOGIN_REQUIRED
    if "session" in message:
        return ProviderErrorCode.INVALID_SESSION
    return ProviderErrorCode.PROVIDER_ERROR


class MobileInstagramProvider(StoryProvider):
    """Story provider backed by Instagrapi's mobile Story endpoints."""

    def __init__(self, client: Any | None = None, credential: dict[str, Any] | None = None) -> None:
        if client is not None:
            self.client = client
            return

        try:
            from instagrapi import Client

            if credential is None:
                loader = create_loader()
                cookies = loader.context.save_session()
            else:
                cookies = credential.get("cookies") or {}
            sessionid = credential.get("sessionid") if credential else cookies.get("sessionid")
            if not sessionid:
                raise InstagramSessionError("A sessão não contém sessionid válido.")
            self.client = Client()
            if not self.client.login_by_sessionid(str(sessionid)):
                raise InstagramSessionError("A sessão não foi aceita pelo cliente mobile.")
        except ProviderError:
            raise
        except InstagramSessionError as exc:
            raise ProviderError(ProviderErrorCode.INVALID_SESSION, str(exc)) from exc
        except Exception as exc:
            code = _error_code(exc)
            safe = redact_sensitive_message(str(exc), (str(sessionid),) if "sessionid" in locals() and sessionid else ())
            raise ProviderError(code, f"Falha ao inicializar provider mobile: {safe}") from exc

    def resolve_source(self, username: str, seed_story_id: str | None = None) -> ResolvedUser:
        if not seed_story_id:
            return self.resolve_user(username)

        try:
            seed_story = self.client.story_info(str(seed_story_id))
            return self._resolved_user_from_story(seed_story, username)
        except ProviderError:
            raise
        except Exception as exc:
            try:
                user = self.client.user_info_by_username_v1(username)
                user_id = _attr(user, "pk", "id")
                if not user_id:
                    raise ValueError("Resposta mobile sem user_id")
                resolved_username = str(_attr(user, "username", default=username)).lower()
                return ResolvedUser(user_id=str(user_id), username=resolved_username)
            except Exception as fallback_exc:
                code = _error_code(fallback_exc)
                raise ProviderError(
                    code,
                    f"Não foi possível resolver a fonte pelo Story ou username mobile: {fallback_exc}",
                ) from exc

    def resolve_user(self, username: str) -> ResolvedUser:
        try:
            user = self.client.user_info_by_username_v1(username)
            user_id = _attr(user, "pk", "id")
            if not user_id:
                raise ValueError("Resposta mobile sem user_id")
            resolved_username = str(_attr(user, "username", default=username)).lower()
            return ResolvedUser(user_id=str(user_id), username=resolved_username)
        except Exception as exc:
            raise ProviderError(
                _error_code(exc),
                f"Não foi possível resolver @{username} pelo endpoint mobile: {exc}",
            ) from exc

    def _resolved_user_from_story(self, story: Any, requested_username: str) -> ResolvedUser:
        owner = _attr(story, "user", "owner")
        user_id = _attr(owner, "pk", "id")
        owner_username = str(_attr(owner, "username", default="")).lower()
        if not user_id or not owner_username:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_ERROR,
                "Story seed sem owner/user_id estável.",
            )
        if owner_username != requested_username.lower():
            raise ProviderError(
                ProviderErrorCode.PROVIDER_ERROR,
                "O owner do Story não corresponde ao username solicitado.",
            )
        return ResolvedUser(user_id=str(user_id), username=owner_username)

    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        try:
            raw_stories = self.client.user_stories_v1(str(user_id))
            stories = [
                self._normalize_story(story, user_id, username, index)
                for index, story in enumerate(raw_stories)
            ]
            stories.sort(key=lambda story: (story.taken_at, story.provider_story_id), reverse=True)
            return [
                ProviderStory(
                    **{**story.__dict__, "provider_index": index}
                )
                for index, story in enumerate(stories)
            ]
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                _error_code(exc),
                f"Não foi possível listar Stories pelo endpoint mobile: {exc}",
            ) from exc

    @staticmethod
    def _normalize_story(
        story: Any,
        user_id: str,
        username: str,
        provider_index: int,
    ) -> ProviderStory:
        story_id = _attr(story, "pk", "id")
        taken_at = _attr(story, "taken_at", "date")
        if not story_id or not isinstance(taken_at, datetime):
            raise ProviderError(
                ProviderErrorCode.PROVIDER_ERROR,
                "Story mobile sem pk ou taken_at válido.",
            )

        video_url = _attr(story, "video_url")
        thumbnail_url = _attr(story, "thumbnail_url", "image_url")
        media_type = _attr(story, "media_type", default=None)
        media_type_value = getattr(media_type, "value", media_type)
        is_video = str(media_type_value).lower() in {"2", "video"} or bool(video_url)
        image_url = str(thumbnail_url) if thumbnail_url else None
        normalized_video_url = str(video_url) if video_url else None
        if is_video and not normalized_video_url:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_ERROR,
                f"Story {story_id} sem video_url.",
            )
        if not is_video and not image_url:
            raise ProviderError(
                ProviderErrorCode.PROVIDER_ERROR,
                f"Story {story_id} sem thumbnail/image URL.",
            )

        taken = _utc(taken_at)
        duration = _attr(story, "video_duration")
        return ProviderStory(
            provider_story_id=str(story_id),
            provider_user_id=str(user_id),
            username=username.lower(),
            media_type="video" if is_video else "image",
            taken_at=taken,
            expires_at=taken + timedelta(hours=24),
            image_url=image_url,
            video_url=normalized_video_url,
            duration=float(duration) if duration not in (None, "") else None,
            provider_index=provider_index,
        )
