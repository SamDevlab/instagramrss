from threading import Lock


_MEDIA_URLS: dict[str, str] = {}
_MEDIA_CONTENT: dict[str, tuple[bytes, str]] = {}
_MEDIA_LOCK = Lock()


def register_media(story_id: str, media_url: str) -> None:
    with _MEDIA_LOCK:
        _MEDIA_URLS[story_id] = media_url


def resolve_media(story_id: str) -> str | None:
    with _MEDIA_LOCK:
        return _MEDIA_URLS.get(story_id)


def register_media_content(media_url: str, content: bytes, media_type: str) -> None:
    with _MEDIA_LOCK:
        _MEDIA_CONTENT[media_url] = (content, media_type)


def resolve_media_content(story_id: str) -> tuple[bytes, str] | None:
    with _MEDIA_LOCK:
        media_url = _MEDIA_URLS.get(story_id)
        return _MEDIA_CONTENT.get(media_url) if media_url else None
