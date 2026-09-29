"""Read-only POC for resolving Stories through Instagrapi's mobile endpoints.

This script is intentionally isolated from the production provider. It imports
the existing persistent Instaloader session only long enough to obtain the
sessionid in memory, then uses Instagrapi for one seed Story lookup and one
mobile Story-feed lookup. It never prints cookies, tokens, URLs, or response
bodies.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from instagram.parser import parse_story_permalink
from instagram.session import InstagramSessionError, create_loader


class ProbeError(RuntimeError):
    """An expected, sanitized POC failure."""

    def __init__(self, operation: str, error_code: str) -> None:
        super().__init__(error_code)
        self.operation = operation
        self.error_code = error_code


def _status(operation: str, status: str, error_code: str | None = None) -> None:
    fields = [f"operation={operation}", f"status={status}"]
    if error_code:
        fields.append(f"error_code={error_code}")
    print(" ".join(fields))


def _classify_error(exc: BaseException) -> str:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if "429" in text or "toomanyrequests" in name or "rate limit" in text:
        return "RATE_LIMITED"
    if "feedback" in text:
        return "FEEDBACK_REQUIRED"
    if "challenge" in name or "challenge" in text:
        return "CHALLENGE_REQUIRED"
    if "checkpoint" in name or "checkpoint" in text:
        return "CHECKPOINT_REQUIRED"
    if "login" in name or "login_required" in text or "not logged" in text:
        return "LOGIN_REQUIRED"
    return exc.__class__.__name__


def _read_sessionid() -> str:
    try:
        loader = create_loader()
        cookies = loader.context.save_session()
    except InstagramSessionError as exc:
        raise ProbeError("SESSION_IMPORT", _classify_error(exc)) from exc
    except Exception as exc:
        raise ProbeError("SESSION_IMPORT", _classify_error(exc)) from exc

    sessionid = cookies.get("sessionid")
    if not sessionid:
        raise ProbeError("SESSION_IMPORT", "SESSIONID_MISSING")
    return str(sessionid)


def _load_client(sessionid: str) -> Any:
    try:
        module = importlib.import_module("instagrapi")
        client = module.Client()
        if not client.login_by_sessionid(sessionid):
            raise ProbeError("INSTAGRAPI_SESSION_IMPORT", "LOGIN_REQUIRED")
        return client
    except ProbeError:
        raise
    except Exception as exc:
        raise ProbeError("INSTAGRAPI_SESSION_IMPORT", _classify_error(exc)) from exc


def _read_attr(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return default


def _story_summary(story: Any, owner_user_id: str) -> dict[str, Any]:
    media_type = _read_attr(story, "media_type", default=None)
    if hasattr(media_type, "value"):
        media_type = media_type.value
    video_url = _read_attr(story, "video_url", default=None)
    is_video = str(media_type).lower() in {"2", "video"} or bool(video_url)
    return {
        "pk": str(_read_attr(story, "pk", "id", default="")),
        "id": str(_read_attr(story, "id", "pk", default="")),
        "taken_at": _serialize_datetime(_read_attr(story, "taken_at", "date", default=None)),
        "media_type": "video" if is_video else "image",
        "has_thumbnail_url": bool(_read_attr(story, "thumbnail_url", default=None)),
        "has_video_url": bool(video_url),
        "video_duration": _read_attr(story, "video_duration", default=None),
        "owner_user_id": owner_user_id,
    }


def _serialize_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _owner_from_story(story: Any, username: str) -> tuple[str, str]:
    owner = _read_attr(story, "user", "owner", default=None)
    owner_id = _read_attr(owner, "pk", "id", default=None)
    owner_username = _read_attr(owner, "username", default=username)
    if owner_id is None:
        raise ProbeError("STORY_INFO", "OWNER_USER_ID_MISSING")
    return str(owner_id), str(owner_username)


def _list_mobile_stories(client: Any, owner_user_id: str) -> list[dict[str, Any]]:
    try:
        stories: Iterable[Any] = client.user_stories_v1(str(owner_user_id))
        summaries = [_story_summary(story, owner_user_id) for story in stories]
    except Exception as exc:
        raise ProbeError("USER_STORIES_V1", _classify_error(exc)) from exc

    ids = [item["pk"] for item in summaries]
    if any(not item_id for item_id in ids):
        raise ProbeError("USER_STORIES_V1", "STORY_PK_MISSING")

    images = sum(item["media_type"] != "video" for item in summaries)
    videos = sum(item["media_type"] == "video" for item in summaries)
    print(f"story_count={len(summaries)}")
    print(f"unique_story_pk={len(set(ids))}")
    print(f"image_count={images}")
    print(f"video_count={videos}")
    print(f"mobile_story_feed={'PASS' if len(ids) == len(set(ids)) else 'FAIL_DUPLICATE_IDS'}")
    return summaries


def probe(permalink: str) -> int:
    try:
        parsed = parse_story_permalink(permalink)
    except Exception as exc:
        _status("PERMALINK_PARSE", "FAIL", _classify_error(exc))
        return 1

    try:
        sessionid = _read_sessionid()
        _status("SESSION_IMPORT", "PASS")
        client = _load_client(sessionid)
        _status("INSTAGRAPI_SESSION_IMPORT", "PASS")
    except ProbeError as exc:
        _status(exc.operation, "FAIL", exc.error_code)
        return 1

    owner_user_id: str | None = None
    owner_username = parsed.username
    try:
        seed_story = client.story_info(str(parsed.seed_story_id))
        owner_user_id, owner_username = _owner_from_story(seed_story, parsed.username)
        _status("STORY_INFO", "PASS")
        print(f"seed_story_lookup=PASS")
        print(f"owner_user_id={owner_user_id}")
        print(f"owner_username_matches={owner_username.lower() == parsed.username.lower()}")
    except Exception as exc:
        _status("STORY_INFO", "FAIL", _classify_error(exc))
        try:
            user = client.user_info_by_username_v1(parsed.username)
            owner_user_id = str(_read_attr(user, "pk", "id", default=""))
            if not owner_user_id:
                raise ProbeError("USERNAME_V1", "OWNER_USER_ID_MISSING")
            _status("USERNAME_V1", "PASS")
            print("resolution_method=username_v1")
        except ProbeError as fallback_exc:
            _status(fallback_exc.operation, "FAIL", fallback_exc.error_code)
            return 1
        except Exception as fallback_exc:
            _status("USERNAME_V1", "FAIL", _classify_error(fallback_exc))
            return 1
    else:
        print("resolution_method=seed_story")

    try:
        _list_mobile_stories(client, owner_user_id)
        print(f"username={parsed.username}")
        print(f"owner_user_id={owner_user_id}")
        return 0
    except ProbeError as exc:
        _status(exc.operation, "FAIL", exc.error_code)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only mobile Story discovery POC")
    parser.add_argument(
        "permalink",
        help="Active Instagram Story permalink; its numeric ID is used as the seed",
    )
    args = parser.parse_args()
    return probe(args.permalink)


if __name__ == "__main__":
    raise SystemExit(main())
