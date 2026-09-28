from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ProviderErrorCode(str, Enum):
    RATE_LIMITED = "RATE_LIMITED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CHALLENGE_REQUIRED = "CHALLENGE_REQUIRED"
    CHECKPOINT_REQUIRED = "CHECKPOINT_REQUIRED"
    FEEDBACK_REQUIRED = "FEEDBACK_REQUIRED"
    INVALID_SESSION = "INVALID_SESSION"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ProviderError(RuntimeError):
    def __init__(self, code: ProviderErrorCode, message: str) -> None:
        super().__init__(message)
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
    @abstractmethod
    def resolve_user(self, username: str) -> ResolvedUser:
        raise NotImplementedError

    @abstractmethod
    def list_stories(self, user_id: str, username: str) -> list[ProviderStory]:
        raise NotImplementedError
