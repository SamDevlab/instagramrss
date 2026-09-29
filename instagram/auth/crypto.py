from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialStoreError(RuntimeError):
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_master_key(raw: str | None) -> bytes:
    value = (raw or "").strip()
    if not value:
        raise CredentialStoreError(
            "AUTH_CREDENTIAL_MASTER_KEY é obrigatório para persistir credenciais."
        )

    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        decoded = b""
        decode_error = exc
    else:
        decode_error = None

    if len(decoded) not in {16, 24, 32}:
        try:
            decoded = bytes.fromhex(value)
        except ValueError as exc:
            raise CredentialStoreError(
                "AUTH_CREDENTIAL_MASTER_KEY deve ser uma chave AES em base64 ou hexadecimal."
            ) from (decode_error or exc)
    if len(decoded) not in {16, 24, 32}:
        raise CredentialStoreError(
            "AUTH_CREDENTIAL_MASTER_KEY deve ter 16, 24 ou 32 bytes."
        )
    return decoded


def redact_sensitive_message(message: str, secrets_to_redact: tuple[str, ...] = ()) -> str:
    """Remove credenciais conhecidas e padrões comuns de mensagens externas."""

    redacted = str(message or "")
    for secret in secrets_to_redact:
        if secret:
            redacted = redacted.replace(secret, "<credencial ocultada>")

    import re

    patterns = (
        r"(?i)(sessionid|session_id|access_token|refresh_token|authorization|cookie)\s*[=:]\s*[^,;\s]+",
        r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+",
    )
    for pattern in patterns:
        redacted = re.sub(pattern, lambda match: f"{match.group(1) if match.lastindex else 'credencial'}=<credencial ocultada>", redacted)
    return redacted[:500]


class EncryptedCredentialStore:
    """AES-GCM credential store. Plaintext fallback is intentionally unsupported."""

    def __init__(self, path: str | Path | None = None, master_key: bytes | None = None) -> None:
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        self.path = Path(path or data_dir / "auth" / "credentials.json").resolve()
        self._master_key = master_key
        self._lock = RLock()

    def _key(self) -> bytes:
        if self._master_key is None:
            self._master_key = _decode_master_key(os.getenv("AUTH_CREDENTIAL_MASTER_KEY"))
        return self._master_key

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "items": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("Não foi possível ler o credential store criptografado.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
            raise CredentialStoreError("Formato inválido do credential store criptografado.")
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

    def put(self, credential: Mapping[str, Any], credential_ref: str | None = None) -> str:
        key = self._key()
        ref = credential_ref or f"cred_{secrets.token_urlsafe(18)}"
        plaintext = json.dumps(dict(credential), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, ref.encode("utf-8"))
        with self._lock:
            payload = self._read()
            payload.setdefault("items", {})[ref] = {
                "algorithm": "AES-GCM",
                "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
                "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
                "created_at": _utcnow_iso(),
            }
            self._write(payload)
        return ref

    def get(self, credential_ref: str) -> dict[str, Any]:
        key = self._key()
        payload = self._read()
        item = payload.get("items", {}).get(credential_ref)
        if not item:
            raise CredentialStoreError("Credencial da conexão não encontrada.")
        try:
            nonce = base64.urlsafe_b64decode(item["nonce"])
            ciphertext = base64.urlsafe_b64decode(item["ciphertext"])
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, credential_ref.encode("utf-8"))
            result = json.loads(plaintext.decode("utf-8"))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("Não foi possível descriptografar a credencial.") from exc
        except Exception as exc:
            raise CredentialStoreError("Não foi possível validar a credencial criptografada.") from exc
        if not isinstance(result, dict):
            raise CredentialStoreError("Formato inválido da credencial descriptografada.")
        return result

    def delete(self, credential_ref: str) -> None:
        with self._lock:
            payload = self._read()
            if credential_ref in payload.get("items", {}):
                del payload["items"][credential_ref]
                self._write(payload)
