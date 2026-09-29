from __future__ import annotations

import os
from typing import Any, Mapping

from instagram.auth.resolver import AuthResolver
from instagram.auth.models import AuthConnectionType
from instagram.auth.service import AuthConnectionError, AuthConnectionService
from instagram.providers.base import StoryProvider
from instagram.providers.direct import DirectInstagramProvider
from instagram.providers.mobile import MobileInstagramProvider


class ProviderFactory:
    """Resolve one isolated provider context for one source refresh."""

    def __init__(self, auth_service: AuthConnectionService | None = None) -> None:
        self.auth_service = auth_service or AuthConnectionService()

    def for_connection(self, connection) -> StoryProvider:
        if connection.type == AuthConnectionType.INSTAGRAM_SESSION.value:
            credential = self.auth_service.credential_for(connection)
            return MobileInstagramProvider(credential=credential)

        if connection.type == AuthConnectionType.LEGACY_SERVER_SESSION.value:
            return self._legacy_provider()

        if connection.type == AuthConnectionType.META_OAUTH.value:
            raise AuthConnectionError(
                "AUTH_CONNECTION_CAPABILITY_MISMATCH",
                "META_OAUTH ainda não implementa o contrato de Stories deste serviço.",
                connection=connection,
            )

        raise AuthConnectionError(
            "AUTH_PROVIDER_UNAVAILABLE",
            f"Tipo de conexão não suportado: {connection.type}.",
            connection=connection,
        )

    def for_source(self, source: Mapping[str, Any]) -> StoryProvider:
        """Compatibility entry point; AuthResolver remains the selector."""
        resolution = AuthResolver(self.auth_service).resolve_with_reason(source)
        return self.for_connection(resolution.connection)

    @staticmethod
    def _legacy_provider() -> StoryProvider:
        provider_name = os.getenv("INSTAGRAM_PROVIDER", "mobile").strip().lower() or "mobile"
        if provider_name == "mobile":
            return MobileInstagramProvider()
        if provider_name == "direct":
            return DirectInstagramProvider()
        raise AuthConnectionError(
            "AUTH_PROVIDER_UNAVAILABLE",
            f"INSTAGRAM_PROVIDER inválido: {provider_name}",
        )
