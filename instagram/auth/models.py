from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AuthConnectionType(str, Enum):
    INSTAGRAM_SESSION = "INSTAGRAM_SESSION"
    META_OAUTH = "META_OAUTH"
    LEGACY_SERVER_SESSION = "LEGACY_SERVER_SESSION"


class AuthConnectionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RECONNECT_REQUIRED = "RECONNECT_REQUIRED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"


class AuthCapability(str, Enum):
    RESOLVE_OWNER_FROM_SEED = "resolve_owner_from_seed"
    LIST_TARGET_USER_STORIES = "list_target_user_stories"
    OWN_AUTHORIZED_ACCOUNT_STORIES = "own_authorized_account_stories"
    FETCH_MEDIA = "fetch_media"


INSTAGRAM_SESSION_CAPABILITIES = (
    AuthCapability.RESOLVE_OWNER_FROM_SEED.value,
    AuthCapability.LIST_TARGET_USER_STORIES.value,
    AuthCapability.FETCH_MEDIA.value,
)


@dataclass
class AuthConnection:
    id: str
    type: str
    status: str
    credential_ref: str | None
    capabilities: list[str] = field(default_factory=list)
    subject_username: str | None = None
    subject_user_id: str | None = None
    expires_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "credential_ref": self.credential_ref,
            "capabilities": list(self.capabilities),
            "subject_username": self.subject_username,
            "subject_user_id": self.subject_user_id,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_validated_at": self.last_validated_at,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AuthConnection":
        return cls(
            id=str(payload["id"]),
            type=str(payload["type"]),
            status=str(payload["status"]),
            credential_ref=(
                str(payload["credential_ref"])
                if payload.get("credential_ref")
                else None
            ),
            capabilities=[str(item) for item in payload.get("capabilities", [])],
            subject_username=(
                str(payload["subject_username"])
                if payload.get("subject_username")
                else None
            ),
            subject_user_id=(
                str(payload["subject_user_id"])
                if payload.get("subject_user_id")
                else None
            ),
            expires_at=str(payload["expires_at"]) if payload.get("expires_at") else None,
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
            last_validated_at=(
                str(payload["last_validated_at"])
                if payload.get("last_validated_at")
                else None
            ),
            last_error=str(payload["last_error"]) if payload.get("last_error") else None,
        )

    def public_dict(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("credential_ref", None)
        return payload
