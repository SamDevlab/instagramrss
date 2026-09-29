from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from instagram.auth.crypto import redact_sensitive_message


class ProviderErrorCode(str, Enum):
    RATE_LIMITED = "RATE_LIMITED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    FEEDBACK_REQUIRED = "FEEDBACK_REQUIRED"
    INVALID_SESSION = "INVALID_SESSION"
    AUTH_INVALID = "AUTH_INVALID"
    TARGET_ACCESS_DENIED = "TARGET_ACCESS_DENIED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    AUTH_CONNECTION_CAPABILITY_MISMATCH = "AUTH_CONNECTION_CAPABILITY_MISMATCH"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ProviderError(RuntimeError):
    def __init__(self, code: ProviderErrorCode, message: str) -> None:
        super().__init__(redact_sensitive_message(message))
        self.code = code


@dataclass(frozen=True)
class ResolvedUser:
    user_id: str
    username: str


@dataclass(frozen=True)
class ProviderStory:
    provider_story_id: str
    provider_user_id: str
    username: str
    media_type: str
    taken_at: datetime
    expires_at: datetime
    image_url: str | None
    video_url: str | None
    duration: float | None
    provider_index: int

    @property
    def media_url(self) -> str:
        if self.media_type == "video" and self.video_url:
            return self.video_url
        if self.image_url:
            return self.image_url
        if self.video_url:
            return self.video_url
        raise ValueError(f"Story {self.provider_story_id} não possui URL de mídia")


class StoryProvider(ABC):
    def resolve_source(self, username: str, seed_story_id: str | None = None) -> ResolvedUser:
        """Resolve a source during onboarding.

        Providers that can use a Story permalink may override this method. The
        default keeps existing providers and test doubles compatible.
        """
        return self.resolve_user(username)

    @abstractmethod
    def resolve_user(self, username: str) -> ResolvedUser:
        raise NotImplementedError

    @abstractmethod
    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        raise NotImplementedError
