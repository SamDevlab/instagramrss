from __future__ import annotations

import hashlib
import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from instagram.providers.base import ProviderStory


class MediaDownloadError(RuntimeError):
    pass


def _extension(media_type: str, content_type: str, data: bytes) -> str:
    lowered = (content_type or "").lower()
    if media_type == "video" or data[4:8] == b"ftyp" or "video/" in lowered:
        return ".mp4"
    if data.startswith(b"\xff\xd8\xff") or "jpeg" in lowered:
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n") or "png" in lowered:
        return ".png"
    if data[:4] in (b"RIFF",) and data[8:12] == b"WEBP":
        return ".webp"
    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0]) if content_type else None
    return guessed or (".mp4" if media_type == "video" else ".bin")


def _valid_magic(media_type: str, data: bytes) -> bool:
    if not data:
        return False
    if media_type == "video":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _probe_video(path: Path) -> dict:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"ffprobe_available": False}

    if proc.returncode != 0:
        return {"ffprobe_available": True, "ffprobe_valid": False, "ffprobe_error": proc.stderr[:300]}

    import json

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ffprobe_available": True, "ffprobe_valid": False}

    streams = payload.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = (payload.get("format") or {}).get("duration")
    return {
        "ffprobe_available": True,
        "ffprobe_valid": bool(video_stream),
        "codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "duration": float(duration) if duration not in (None, "N/A") else None,
        "has_audio": has_audio,
    }


class MediaDownloader:
    def __init__(self, timeout: int = 45) -> None:
        self.timeout = timeout

    def download(self, story: ProviderStory, media_dir: Path) -> dict:
        media_dir.mkdir(parents=True, exist_ok=True)
        request = Request(
            story.media_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.instagram.com/",
                "Accept": "*/*",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise MediaDownloadError(f"HTTP {status} ao baixar Story")
                content_type = response.headers.get_content_type() or "application/octet-stream"
                data = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise MediaDownloadError(f"Falha ao baixar Story {story.provider_story_id}: {exc}") from exc

        if not _valid_magic(story.media_type, data):
            raise MediaDownloadError(
                f"Mídia inválida para Story {story.provider_story_id}: magic bytes inesperados"
            )

        digest = hashlib.sha256(data).hexdigest()
        suffix = _extension(story.media_type, content_type, data)
        filename = f"{digest}{suffix}"
        target = media_dir / filename
        if not target.exists():
            fd, tmp_name = tempfile.mkstemp(prefix=".media.", suffix=".tmp", dir=str(media_dir))
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, target)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass

        metadata = {
            "sha256": digest,
            "filename": filename,
            "bytes": len(data),
            "content_type": content_type,
        }
        if story.media_type == "video":
            metadata.update(_probe_video(target))
        return metadata
