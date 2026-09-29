from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, TYPE_CHECKING

from instagram.auth.models import (
    AuthCapability,
    AuthConnection,
    AuthConnectionScope,
    AuthConnectionStatus,
    AuthConnectionType,
    AuthPolicy,
)

if TYPE_CHECKING:
    from instagram.auth.service import AuthConnectionService


@dataclass(frozen=True)
class AuthResolution:
    connection: AuthConnection
    reason: str


class AuthResolver:
    """Select the authentication connection without selecting a provider."""

    SUPPORTED_TYPES = {
        AuthConnectionType.INSTAGRAM_SESSION.value,
        AuthConnectionType.LEGACY_SERVER_SESSION.value,
    }

    def __init__(self, service: "AuthConnectionService") -> None:
        self.service = service

    def resolve(
        self,
        source: Mapping[str, Any],
        required_capability: str | None = None,
    ) -> AuthConnection:
        return self.resolve_with_reason(source, required_capability).connection

    def resolve_with_reason(
        self,
        source: Mapping[str, Any],
        required_capability: str | None = None,
    ) -> AuthResolution:
        capability = required_capability or AuthCapability.LIST_TARGET_USER_STORIES.value
        policy_value = source.get("auth_policy")
        if not policy_value and source.get("auth_connection_id"):
            # Compatibility for callers that still pass the old explicit
            # binding shape without the new policy field.
            policy_value = AuthPolicy.PINNED.value
        policy = str(policy_value or AuthPolicy.PREFER_OWNER_WITH_SHARED_FALLBACK.value).strip().upper()
        if policy == AuthPolicy.PINNED.value:
            return AuthResolution(
                self._resolve_pinned(source, capability),
                "PINNED",
            )
        if policy != AuthPolicy.PREFER_OWNER_WITH_SHARED_FALLBACK.value:
            raise self._error(
                "AUTH_POLICY_INVALID",
                "A source deve usar PREFER_OWNER_WITH_SHARED_FALLBACK ou PINNED.",
            )

        owner_id = str(source.get("owner_id") or "").strip() or None
        connections = self.service.store.list()
        own = [
            connection
            for connection in connections
            if owner_id and connection.owner_id == owner_id and self._eligible(connection, capability)
        ]
        if own:
            return AuthResolution(self._rank(own)[0], "OWNER_AUTH")

        shared = [
            connection
            for connection in connections
            if connection.scope == AuthConnectionScope.SHARED.value
            and self._eligible(connection, capability)
        ]
        if shared:
            return AuthResolution(self._rank(shared)[0], "SHARED_FALLBACK")

        legacy = self.service.legacy_connection()
        if legacy and self._eligible(legacy, capability):
            return AuthResolution(legacy, "LEGACY_FALLBACK")

        raise self._error(
            "AUTH_CONNECTION_REQUIRED",
            "Nenhuma conexão ativa suporta a capability solicitada para esta source.",
        )

    def _resolve_pinned(self, source: Mapping[str, Any], capability: str) -> AuthConnection:
        connection_id = str(source.get("auth_connection_id") or "").strip()
        if not connection_id:
            raise self._error("AUTH_CONNECTION_REQUIRED", "Uma source PINNED exige auth_connection_id.")
        connection = self.service.get(connection_id)
        source_owner = str(source.get("owner_id") or "").strip() or None
        if (
            connection.scope == AuthConnectionScope.PRIVATE.value
            and source_owner
            and connection.owner_id
            and connection.owner_id != source_owner
        ):
            raise self._error(
                "AUTH_CONNECTION_FORBIDDEN",
                "Uma source não pode selecionar a conexão PRIVATE de outro owner.",
                connection,
            )
        if connection.status != AuthConnectionStatus.ACTIVE.value:
            raise self._error(connection.status, f"A conexão está em estado {connection.status}.", connection)
        if not self._eligible(connection, capability):
            raise self._error(
                "AUTH_CONNECTION_CAPABILITY_MISMATCH",
                "A conexão fixada não suporta a capability ou provider necessários.",
                connection,
            )
        return connection

    def _eligible(self, connection: AuthConnection, capability: str) -> bool:
        if connection.status != AuthConnectionStatus.ACTIVE.value:
            return False
        if connection.type not in self.SUPPORTED_TYPES:
            return False
        if capability not in connection.capabilities:
            return False
        if (
            connection.type == AuthConnectionType.INSTAGRAM_SESSION.value
            and not connection.credential_ref
        ):
            return False
        return True

    @staticmethod
    def _rank(connections: list[AuthConnection]) -> list[AuthConnection]:
        return sorted(
            connections,
            key=lambda connection: (
                -AuthResolver._timestamp(connection.last_validated_at),
                -AuthResolver._timestamp(connection.updated_at),
                connection.id,
            ),
        )

    @staticmethod
    def _timestamp(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @staticmethod
    def _error(
        code: str,
        message: str,
        connection: AuthConnection | None = None,
    ):
        from instagram.auth.service import AuthConnectionError

        return AuthConnectionError(code, message, connection=connection)
