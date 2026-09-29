from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from instagram.auth.crypto import (
    CredentialStoreError,
    EncryptedCredentialStore,
    redact_sensitive_message,
)
from instagram.auth.models import (
    INSTAGRAM_SESSION_CAPABILITIES,
    AuthConnection,
    AuthConnectionScope,
    AuthConnectionStatus,
    AuthConnectionType,
)
from instagram.auth.store import AuthConnectionStore
from instagram.parser import normalize_username
from instagram.session import load_session_credential


class AuthConnectionError(RuntimeError):
    def __init__(self, code: str, message: str, *, connection: AuthConnection | None = None) -> None:
        self.code = code
        self.connection = connection
        super().__init__(redact_sensitive_message(message))


class AuthConnectionService:
    def __init__(
        self,
        store: AuthConnectionStore | None = None,
        credentials: EncryptedCredentialStore | None = None,
    ) -> None:
        self.store = store or AuthConnectionStore()
        self.credentials = credentials or EncryptedCredentialStore()

    def list_public(self) -> list[dict[str, Any]]:
        return [connection.public_dict() for connection in self.store.list()]

    def get(self, connection_id: str) -> AuthConnection:
        try:
            return self.store.get(connection_id)
        except KeyError as exc:
            raise AuthConnectionError("AUTH_CONNECTION_NOT_FOUND", str(exc)) from exc

    def create_instagram_session_connection(
        self,
        subject_username: str,
        credential: Mapping[str, Any],
        *,
        subject_user_id: str | None = None,
        owner_id: str | None = None,
        scope: str = AuthConnectionScope.PRIVATE.value,
    ) -> AuthConnection:
        username = normalize_username(subject_username)
        owner = self.normalize_owner_id(owner_id)
        normalized_scope = self._normalize_scope(scope)
        cookies = credential.get("cookies")
        sessionid = credential.get("sessionid")
        if not isinstance(cookies, Mapping):
            cookies = {}
        if not sessionid:
            sessionid = cookies.get("sessionid")
        if not sessionid:
            raise AuthConnectionError(
                "AUTH_CREDENTIAL_INVALID",
                "A conexão Instagram exige uma sessão autenticada válida.",
            )

        normalized_credential = {
            "kind": "instagram_session",
            "username": username,
            "cookies": {str(key): str(value) for key, value in cookies.items()},
            "sessionid": str(sessionid),
        }
        try:
            credential_ref = self.credentials.put(normalized_credential)
        except CredentialStoreError as exc:
            raise AuthConnectionError("AUTH_CREDENTIAL_STORE_UNAVAILABLE", str(exc)) from exc

        now = self.store.now()
        connection = AuthConnection(
            id=self.store.new_id(),
            type=AuthConnectionType.INSTAGRAM_SESSION.value,
            status=AuthConnectionStatus.ACTIVE.value,
            credential_ref=credential_ref,
            owner_id=owner,
            scope=normalized_scope,
            capabilities=list(INSTAGRAM_SESSION_CAPABILITIES),
            subject_username=username,
            subject_user_id=str(subject_user_id) if subject_user_id else None,
            created_at=now,
            updated_at=now,
            last_validated_at=None,
        )
        try:
            self.store.save(connection)
        except Exception:
            self.credentials.delete(credential_ref)
            raise
        return connection

    def create_from_session_file(
        self,
        subject_username: str,
        session_file: str | Path,
        *,
        owner_id: str | None = None,
        scope: str = AuthConnectionScope.PRIVATE.value,
    ) -> AuthConnection:
        try:
            credential = load_session_credential(subject_username, session_file)
        except Exception as exc:
            raise AuthConnectionError(
                "AUTH_CREDENTIAL_INVALID",
                f"Não foi possível importar a sessão local: {exc}",
            ) from exc
        return self.create_instagram_session_connection(
            subject_username,
            credential,
            owner_id=owner_id,
            scope=scope,
        )

    @staticmethod
    def normalize_owner_id(owner_id: str | None) -> str | None:
        value = str(owner_id or "").strip()
        if not value:
            return None
        if len(value) > 128 or any(character.isspace() for character in value):
            raise AuthConnectionError(
                "OWNER_ID_INVALID",
                "owner_id deve ser um identificador não vazio de até 128 caracteres.",
            )
        return value

    @staticmethod
    def _normalize_scope(scope: str | None) -> str:
        value = str(scope or AuthConnectionScope.PRIVATE.value).strip().upper()
        if value not in {item.value for item in AuthConnectionScope}:
            raise AuthConnectionError(
                "AUTH_CONNECTION_SCOPE_INVALID",
                "scope deve ser PRIVATE ou SHARED.",
            )
        return value

    def credential_for(self, connection: AuthConnection) -> dict[str, Any]:
        if connection.type != AuthConnectionType.INSTAGRAM_SESSION.value:
            raise AuthConnectionError(
                "AUTH_PROVIDER_UNAVAILABLE",
                f"O provider para {connection.type} ainda não está disponível.",
                connection=connection,
            )
        if not connection.credential_ref:
            raise AuthConnectionError(
                "AUTH_CREDENTIAL_MISSING",
                "A conexão não possui uma credencial persistida.",
                connection=connection,
            )
        try:
            return self.credentials.get(connection.credential_ref)
        except CredentialStoreError as exc:
            raise AuthConnectionError("AUTH_CREDENTIAL_UNAVAILABLE", str(exc), connection=connection) from exc

    def legacy_connection(self) -> AuthConnection | None:
        legacy_username = os.getenv("INSTAGRAM_USERNAME", "").strip()
        legacy_file = os.getenv("INSTAGRAM_SESSION_FILE", "./session/instagram.session").strip()
        enabled = os.getenv("INSTAGRAM_LEGACY_SESSION_ENABLED", "true").strip().lower()
        if not legacy_username or not legacy_file or enabled not in {"1", "true", "yes", "on"}:
            return None
        now = self.store.now()
        return AuthConnection(
            id="legacy_server_session",
            type=AuthConnectionType.LEGACY_SERVER_SESSION.value,
            status=AuthConnectionStatus.ACTIVE.value,
            credential_ref=None,
            owner_id=os.getenv("INSTAGRAM_LEGACY_OWNER_ID", "legacy_operator").strip() or "legacy_operator",
            scope=AuthConnectionScope.SHARED.value,
            capabilities=list(INSTAGRAM_SESSION_CAPABILITIES),
            subject_username=legacy_username,
            created_at=now,
            updated_at=now,
            last_validated_at=None,
        )

    def connection_for_source(self, source: Mapping[str, Any]) -> AuthConnection:
        """Compatibility shim; selection rules live in AuthResolver."""
        from instagram.auth.resolver import AuthResolver

        return AuthResolver(self).resolve(source)

    def select_for_source(self, source: Mapping[str, Any], required_capability: str | None = None) -> AuthConnection:
        from instagram.auth.resolver import AuthResolver

        return AuthResolver(self).resolve(source, required_capability)

    def mark_reconnect_required(self, connection_id: str, error: str) -> AuthConnection:
        connection = self.get(connection_id)
        connection.status = AuthConnectionStatus.RECONNECT_REQUIRED.value
        connection.last_error = redact_sensitive_message(error)
        connection.last_validated_at = self.store.now()
        connection.updated_at = self.store.now()
        self.store.save(connection)
        return connection

    def mark_validated(self, connection_id: str) -> AuthConnection:
        connection = self.get(connection_id)
        if connection.status == AuthConnectionStatus.ACTIVE.value:
            connection.last_validated_at = self.store.now()
            connection.last_error = None
            connection.updated_at = self.store.now()
            self.store.save(connection)
        return connection

    def disconnect(self, connection_id: str) -> AuthConnection:
        connection = self.get(connection_id)
        credential_ref = connection.credential_ref
        connection.status = AuthConnectionStatus.REVOKED.value
        connection.credential_ref = None
        connection.last_error = None
        connection.last_validated_at = self.store.now()
        connection.updated_at = self.store.now()
        self.store.save(connection)
        if credential_ref:
            try:
                self.credentials.delete(credential_ref)
            except CredentialStoreError:
                connection.status = AuthConnectionStatus.ERROR.value
                connection.last_error = "A credencial foi revogada, mas não pôde ser removida do store."
                connection.updated_at = self.store.now()
                self.store.save(connection)
        return connection
