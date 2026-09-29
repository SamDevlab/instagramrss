from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class SourcePaths:
    root: Path
    source: Path
    state: Path
    current: Path
    catalog: Path
    media: Path


class SourceStore:
    def __init__(self, data_dir: str | Path | None = None) -> None:
        root = Path(data_dir or os.getenv("DATA_DIR", "./data"))
        self.root = root.resolve()
        self.sources_dir = self.root / "sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def source_id_for(self, username: str) -> str:
        normalized = username.lower().strip()
        return f"ig_{sha256(normalized.encode('utf-8')).hexdigest()[:16]}"

    def paths(self, source_id: str) -> SourcePaths:
        if not source_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in source_id):
            raise ValueError("source_id inválido")
        root = self.sources_dir / source_id
        return SourcePaths(
            root=root,
            source=root / "source.json",
            state=root / "state.json",
            current=root / "current.json",
            catalog=root / "catalog.json",
            media=root / "media",
        )

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _normalize_source(source: dict) -> dict:
        """Apply conservative defaults to sources written before auth policies."""
        normalized = dict(source)
        owner_id = normalized.get("owner_id")
        normalized["owner_id"] = (
            str(owner_id).strip() or None
            if owner_id is not None
            else None
        )
        if "auth_policy" not in normalized:
            normalized["auth_policy"] = (
                "PINNED"
                if normalized.get("auth_connection_id")
                else "PREFER_OWNER_WITH_SHARED_FALLBACK"
            )
        elif normalized.get("auth_policy"):
            normalized["auth_policy"] = str(normalized["auth_policy"]).strip().upper()
        normalized.setdefault("last_auth_connection_id", None)
        normalized.setdefault("last_auth_selection_reason", None)
        return normalized

    def list_sources(self) -> list[dict]:
        items: list[dict] = []
        for source_file in sorted(self.sources_dir.glob("*/source.json")):
            try:
                source = self._read_json(source_file, {})
                if isinstance(source, dict):
                    items.append(self._normalize_source(source))
            except (OSError, json.JSONDecodeError):
                continue
        return items

    def find_source_by_username(self, username: str) -> dict | None:
        username = username.lower()
        for source in self.list_sources():
            if str(source.get("username", "")).lower() == username:
                return source
        return None

    def save_source(self, source: dict) -> None:
        paths = self.paths(source["source_id"])
        paths.media.mkdir(parents=True, exist_ok=True)
        with self._lock:
            _atomic_json(paths.source, self._normalize_source(source))

    def load_source(self, source_id: str) -> dict:
        source = self._read_json(self.paths(source_id).source, None)
        if not source:
            raise FileNotFoundError(f"Fonte {source_id} não encontrada")
        return self._normalize_source(source)

    def load_state(self, source_id: str) -> dict:
        return self._read_json(
            self.paths(source_id).state,
            {
                "lastAttemptAt": None,
                "lastCompleteAt": None,
                "lastContentChangeAt": None,
                "lastNewMediaAt": None,
                "lastStatus": "NEW",
                "activeCount": 0,
                "inactiveCount": 0,
                "consecutiveFailures": 0,
                "pendingLargeDropSignature": None,
            },
        )

    def save_state(self, source_id: str, state: dict) -> None:
        with self._lock:
            _atomic_json(self.paths(source_id).state, state)

    def load_current(self, source_id: str) -> dict:
        return self._read_json(
            self.paths(source_id).current,
            {"source_id": source_id, "status": "EMPTY", "items": []},
        )

    def save_current(self, source_id: str, snapshot: dict) -> None:
        with self._lock:
            _atomic_json(self.paths(source_id).current, snapshot)

    def load_catalog(self, source_id: str) -> dict:
        return self._read_json(self.paths(source_id).catalog, {"items": {}})

    def save_catalog(self, source_id: str, catalog: dict) -> None:
        with self._lock:
            _atomic_json(self.paths(source_id).catalog, catalog)

    def media_path(self, source_id: str, filename: str) -> Path:
        if Path(filename).name != filename:
            raise ValueError("filename inválido")
        base = self.paths(source_id).media.resolve()
        candidate = (base / filename).resolve()
        if candidate.parent != base:
            raise ValueError("Caminho de mídia inválido")
        return candidate
