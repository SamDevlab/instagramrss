from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from threading import RLock
from typing import Any

from instagram.auth.models import AuthConnection
from instagram.storage import utcnow_iso


class AuthConnectionStore:
    def __init__(self, path: str | Path | None = None) -> None:
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        self.path = Path(path or data_dir / "auth" / "connections.json").resolve()
        self._lock = RLock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "connections": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Não foi possível ler o auth connection store.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("connections"), dict):
            raise RuntimeError("Formato inválido do auth connection store.")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def save(self, connection: AuthConnection) -> None:
        with self._lock:
            payload = self._read()
            payload.setdefault("connections", {})[connection.id] = connection.to_dict()
            self._write(payload)

    def get(self, connection_id: str) -> AuthConnection:
        payload = self._read()
        raw = payload.get("connections", {}).get(connection_id)
        if not raw:
            raise KeyError(f"Auth connection {connection_id} não encontrada")
        return AuthConnection.from_dict(raw)

    def list(self) -> list[AuthConnection]:
        payload = self._read()
        return [
            AuthConnection.from_dict(raw)
            for raw in payload.get("connections", {}).values()
            if isinstance(raw, dict)
        ]

    def new_id(self) -> str:
        return f"auth_{secrets.token_urlsafe(18)}"

    def now(self) -> str:
        return utcnow_iso()
