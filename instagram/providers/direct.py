from __future__ import annotations

from datetime import datetime, timedelta, timezone

import instaloader

from instagram.providers.base import (
    ProviderError,
    ProviderErrorCode,
    ProviderStory,
    ResolvedUser,
    StoryProvider,
)
from instagram.session import InstagramSessionError, create_loader


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DirectInstagramProvider(StoryProvider):
    """Direct Story provider backed by the persistent Instaloader session.

    This provider does not use SaveClip/SaveInsta/SnapInsta. The permalink is
    only used by onboarding; steady-state refreshes use the persisted numeric
    Instagram user id.
    """

    def __init__(self) -> None:
        try:
            self.loader = create_loader()
        except InstagramSessionError as exc:
            raise ProviderError(ProviderErrorCode.INVALID_SESSION, str(exc)) from exc

    def resolve_user(self, username: str) -> ResolvedUser:
        try:
            profile = instaloader.Profile.from_username(self.loader.context, username)
            return ResolvedUser(user_id=str(profile.userid), username=profile.username.lower())
        except Exception as exc:
            raise self._map_error(exc, f"Não foi possível resolver @{username}") from exc

    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        try:
            stories: list[ProviderStory] = []
            provider_index = 0
            for reel in self.loader.get_stories(userids=[int(user_id)]):
                for item in reel.get_items():
                    taken_at = _utc(item.date_utc)
                    is_video = bool(item.is_video)
                    image_url = str(item.url) if getattr(item, "url", None) else None
                    video_url = (
                        str(item.video_url)
                        if is_video and getattr(item, "video_url", None)
                        else None
                    )
                    media_id = getattr(item, "mediaid", None)
                    if media_id is None:
                        media_id = getattr(item, "shortcode", None)
                    if media_id is None:
                        raise ProviderError(
                            ProviderErrorCode.PROVIDER_ERROR,
                            "O Instagram retornou uma Story sem ID estável.",
                        )
                    duration = getattr(item, "video_duration", None)
                    stories.append(
                        ProviderStory(
                            provider_story_id=str(media_id),
                            provider_user_id=str(user_id),
                            username=username.lower(),
                            media_type="video" if is_video else "image",
                            taken_at=taken_at,
                            expires_at=taken_at + timedelta(hours=24),
                            image_url=image_url,
                            video_url=video_url,
                            duration=float(duration) if duration is not None else None,
                            provider_index=provider_index,
                        )
                    )
                    provider_index += 1
            stories.sort(key=lambda story: (story.taken_at, story.provider_story_id), reverse=True)
            return [
                ProviderStory(
                    **{
                        **story.__dict__,
                        "provider_index": index,
                    }
                )
                for index, story in enumerate(stories)
            ]
        except ProviderError:
            raise
        except Exception as exc:
            raise self._map_error(exc, f"Não foi possível listar Stories de @{username}") from exc

    @staticmethod
    def _map_error(exc: Exception, prefix: str) -> ProviderError:
        name = exc.__class__.__name__.lower()
        message = str(exc)
        lowered = message.lower()

        if "toomanyrequests" in name or "429" in lowered or "rate limit" in lowered:
            code = ProviderErrorCode.RATE_LIMITED
        elif "feedback" in lowered:
            code = ProviderErrorCode.FEEDBACK_REQUIRED
        elif "challenge" in name or "challenge" in lowered:
            code = ProviderErrorCode.CHALLENGE_REQUIRED
        elif "checkpoint" in lowered:
            code = ProviderErrorCode.CHECKPOINT_REQUIRED
        elif "login" in name or "login_required" in lowered or "not logged" in lowered:
            code = ProviderErrorCode.LOGIN_REQUIRED
        elif "session" in lowered:
            code = ProviderErrorCode.INVALID_SESSION
        else:
            code = ProviderErrorCode.PROVIDER_ERROR

        safe_message = message[:500] if message else exc.__class__.__name__
        return ProviderError(code, f"{prefix}: {safe_message}")
