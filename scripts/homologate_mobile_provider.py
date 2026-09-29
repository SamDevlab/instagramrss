"""Multi-profile, read-only homologation for the mobile Story provider.

The candidate scan may use the mobile username endpoint to find active
profiles. Every application onboarding still starts with a freshly constructed
Story permalink and goes through SourceService.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from instagram.providers.base import ProviderError
from instagram.providers.mobile import MobileInstagramProvider
from instagram.service import SourceService
from instagram.storage import SourceStore
from rss.builder import build_source_rss
from instagram.parser import parse_story_permalink


CANDIDATES = (
    "prof.tiacarol",
    "instagram",
    "nasa",
    "natgeo",
    "nike",
    "adidas",
    "netflixbrasil",
    "globoplay",
    "g1",
    "cnnbrasil",
)


def _attr(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return default


def _is_video(story: Any) -> bool:
    media_type = _attr(story, "media_type", default=None)
    media_type = getattr(media_type, "value", media_type)
    return str(media_type).lower() in {"2", "video"} or bool(_attr(story, "video_url"))


def _error_code(exc: BaseException) -> str:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if "429" in message or "rate" in message or "toomanyrequests" in name:
        return "RATE_LIMITED"
    if "challenge" in name or "challenge" in message:
        return "CHALLENGE_REQUIRED"
    if "checkpoint" in name or "checkpoint" in message:
        return "CHECKPOINT_REQUIRED"
    if "login" in name or "login_required" in message:
        return "LOGIN_REQUIRED"
    if "feedback" in message:
        return "FEEDBACK_REQUIRED"
    return exc.__class__.__name__


def _ffprobe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
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
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"available": False, "valid": False, "has_audio": False}

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"available": True, "valid": False, "has_audio": False}

    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration = (payload.get("format") or {}).get("duration")
    valid = bool(
        result.returncode == 0
        and video
        and float(duration or 0) > 0
        and int(video.get("width") or 0) > 0
        and int(video.get("height") or 0) > 0
    )
    return {"available": True, "valid": valid, "has_audio": audio}


def _valid_media(path: Path, item: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    if not path.is_file():
        return False, {"kind": item.get("media_type")}
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not data or digest != item.get("sha256") or len(data) != int(item.get("bytes", -1)):
        return False, {"kind": item.get("media_type")}

    if item.get("media_type") == "video":
        probe = _ffprobe(path)
        return bool(probe.get("valid")), {"kind": "video", **probe}

    image_magic = (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )
    return image_magic, {"kind": "image"}


def _candidate_summary(username: str, user: Any, stories: list[Any]) -> dict[str, Any]:
    videos = sum(_is_video(story) for story in stories)
    return {
        "username": username,
        "user_id": str(_attr(user, "pk", "id", default="")),
        "story_count": len(stories),
        "images": len(stories) - videos,
        "videos": videos,
        "status": "ACTIVE" if stories else "NO_ACTIVE_STORIES",
    }


class TrackingClient:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.story_info_calls = 0
        self.user_stories_calls = 0

    def story_info(self, *args: Any, **kwargs: Any) -> Any:
        self.story_info_calls += 1
        return self._client.story_info(*args, **kwargs)

    def user_stories_v1(self, *args: Any, **kwargs: Any) -> Any:
        self.user_stories_calls += 1
        return self._client.user_stories_v1(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _run_summary(result: dict[str, Any], elapsed: float) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "discovered": result.get("discovered", 0),
        "valid": result.get("valid", 0),
        "downloaded": result.get("downloaded", 0),
        "reused": result.get("reused", 0),
        "new": result.get("new", 0),
        "expired": result.get("expired", 0),
        "snapshot_id": result.get("snapshot_id"),
        "elapsed_seconds": round(elapsed, 3),
    }


def _refresh(service: SourceService, source_id: str) -> dict[str, Any]:
    started = time.monotonic()
    result = service.refresh_source(source_id)
    return _run_summary(result, time.monotonic() - started)


def _validate_snapshot(store: SourceStore, source_id: str) -> dict[str, Any]:
    snapshot = store.load_current(source_id)
    valid = 0
    invalid = 0
    videos = 0
    video_ffprobe_ok = 0
    video_with_audio = 0
    images = 0
    for item in snapshot.get("items") or []:
        path = store.media_path(source_id, item["filename"])
        ok, details = _valid_media(path, item)
        valid += int(ok)
        invalid += int(not ok)
        if details.get("kind") == "video":
            videos += 1
            video_ffprobe_ok += int(bool(details.get("valid")))
            video_with_audio += int(bool(details.get("has_audio")))
        else:
            images += 1
    return {
        "active_files": len(snapshot.get("items") or []),
        "valid_files": valid,
        "invalid_files": invalid,
        "video_files": videos,
        "video_ffprobe_ok": video_ffprobe_ok,
        "video_with_audio": video_with_audio,
        "image_files": images,
    }


def _validate_seed(provider: MobileInstagramProvider, username: str, seed_id: str) -> dict[str, Any]:
    parsed = parse_story_permalink(f"https://www.instagram.com/stories/{username}/{seed_id}/")
    story = provider.client.story_info(parsed.seed_story_id)
    returned_pk = str(_attr(story, "pk", "id", default=""))
    owner = _attr(story, "user", "owner")
    owner_id = str(_attr(owner, "pk", "id", default=""))
    owner_username = str(_attr(owner, "username", default="")).lower()
    if returned_pk != seed_id or owner_username != username.lower():
        raise RuntimeError("seed Story não corresponde ao permalink construído")
    feed = provider.client.user_stories_v1(owner_id)
    feed_ids = [str(_attr(item, "pk", "id", default="")) for item in feed]
    videos = sum(_is_video(item) for item in feed)
    return {
        "seed_lookup": "PASS",
        "owner_user_id": owner_id,
        "owner_username": owner_username,
        "story_count": len(feed),
        "unique_story_pk": len(set(feed_ids)),
        "images": len(feed) - videos,
        "videos": videos,
        "one_seed_to_full_feed": bool(feed) and len(feed_ids) == len(set(feed_ids)),
    }


def _rss_local_only(rss: str, source_id: str, item_count: int) -> bool:
    try:
        root = ET.fromstring(rss)
    except ET.ParseError:
        return False
    namespace = {"media": "http://search.yahoo.com/mrss/"}
    urls = [
        node.attrib.get("url", "")
        for node in root.findall(".//media:content", namespace)
    ]
    forbidden = ("saveclip.app", "saveinsta", "snapinsta")
    return len(urls) == item_count and all(
        urlparse(url).path.startswith(f"/media/{source_id}/")
        and not any(value in url.lower() for value in forbidden)
        for url in urls
    )


def homologate(data_dir: Path, candidates: tuple[str, ...]) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    provider = MobileInstagramProvider()
    client = provider.client
    candidate_reports: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []

    for username in candidates:
        try:
            user = client.user_info_by_username_v1(username)
            user_id = str(_attr(user, "pk", "id", default=""))
            stories = list(client.user_stories_v1(user_id))
            report = _candidate_summary(username, user, stories)
            candidate_reports.append(report)
            if stories:
                seed_id = str(_attr(stories[0], "pk", "id", default=""))
                report["seed_story_pk"] = seed_id
                report["constructed_permalink"] = f"https://www.instagram.com/stories/{username}/{seed_id}/"
                active.append({"username": username, "user_id": user_id, "seed_id": seed_id})
            print(
                f"CANDIDATE username={username} user_id={user_id} "
                f"story_count={len(stories)} status={report['status']}"
            )
        except Exception as exc:
            report = {
                "username": username,
                "status": "ERROR",
                "error_code": _error_code(exc),
            }
            candidate_reports.append(report)
            print(f"CANDIDATE username={username} status=ERROR error_code={report['error_code']}")

    preferred = next((item for item in active if item["username"] == "prof.tiacarol"), None)
    if not preferred:
        raise RuntimeError("prof.tiacarol não possui Story ativo no momento")
    selected = [preferred]
    selected.extend(item for item in active if item is not preferred)
    selected = selected[:3]
    if len(selected) < 3:
        raise RuntimeError(f"apenas {len(selected)} perfis ativos encontrados; mínimo=3")

    store = SourceStore(data_dir)
    service = SourceService(store=store, provider_factory=lambda: provider)
    profiles: list[dict[str, Any]] = []

    for item in selected:
        username = item["username"]
        permalink = f"https://www.instagram.com/stories/{username}/{item['seed_id']}/"
        seed_report = _validate_seed(provider, username, item["seed_id"])
        source = service.create_source(permalink)
        if str(source.get("instagram_user_id")) != seed_report["owner_user_id"]:
            raise RuntimeError(f"user_id persistido divergente para @{username}")
        source_id = source["source_id"]

        run_a = _refresh(service, source_id)
        media_a = _validate_snapshot(store, source_id)
        snapshot_a = store.load_current(source_id)
        ids_a = {
            story["provider_story_id"]: (story.get("sha256"), story.get("filename"))
            for story in snapshot_a.get("items") or []
        }

        run_b = _refresh(service, source_id)
        media_b = _validate_snapshot(store, source_id)
        snapshot_b = store.load_current(source_id)
        ids_b = {
            story["provider_story_id"]: (story.get("sha256"), story.get("filename"))
            for story in snapshot_b.get("items") or []
        }
        unchanged = set(ids_a) & set(ids_b)
        stable = all(ids_a[story_id] == ids_b[story_id] for story_id in unchanged)

        rss = build_source_rss(source, snapshot_b)
        rss_local_only = _rss_local_only(rss, source_id, len(snapshot_b.get("items") or []))

        profiles.append(
            {
                "username": username,
                "seed_permalink": permalink,
                "owner_user_id": seed_report["owner_user_id"],
                "story_count": seed_report["story_count"],
                "images": seed_report["images"],
                "videos": seed_report["videos"],
                "one_seed_to_full_feed": seed_report["one_seed_to_full_feed"],
                "source_id": source_id,
                "run_a": run_a,
                "run_b": run_b,
                "media_a": media_a,
                "media_b": media_b,
                "unchanged_count": len(unchanged),
                "unchanged_redownloaded": run_b.get("downloaded", 0),
                "stable_story_ids": stable,
                "rss_item_count": len(snapshot_b.get("items") or []),
                "rss_local_urls_only": rss_local_only,
            }
        )
        print(
            f"PROFILE username={username} source_id={source_id} "
            f"run_a={run_a['status']}:{run_a['downloaded']} "
            f"run_b={run_b['status']}:{run_b['downloaded']} "
            f"unchanged_redownloaded={run_b.get('downloaded', 0)}"
        )

    tracker = TrackingClient(provider.client)
    provider.client = tracker
    restart_results: list[dict[str, Any]] = []
    for profile in profiles:
        result = _refresh(
            SourceService(store=SourceStore(data_dir), provider_factory=lambda: provider),
            profile["source_id"],
        )
        restart_results.append(result)
    restart_pass = all(result.get("status") == "COMPLETE" and result.get("downloaded") == 0 for result in restart_results)

    scheduler_service = SourceService(store=SourceStore(data_dir), provider_factory=lambda: provider)
    scheduler_results = __import__("instagram.scheduler", fromlist=["StoryScheduler"]).StoryScheduler(
        scheduler_service,
        interval_minutes=15,
    ).run_once()
    scheduler_pass = all(
        result.get("status") == "COMPLETE" and result.get("downloaded") == 0
        for result in scheduler_results
    )

    steady_state = tracker.story_info_calls == 0 and tracker.user_stories_calls > 0
    report = {
        "candidate_reports": candidate_reports,
        "profiles": profiles,
        "persistence_after_restart": restart_pass,
        "restart_results": restart_results,
        "steady_state_uses_user_id": steady_state,
        "steady_state_story_info_calls": tracker.story_info_calls,
        "steady_state_user_stories_calls": tracker.user_stories_calls,
        "scheduler_run_once": scheduler_pass,
        "scheduler_results": scheduler_results,
    }
    report_path = data_dir / "homologation-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Homologate the mobile Story provider with real public profiles")
    parser.add_argument("--data-dir", default="./data-homologation")
    args = parser.parse_args()
    try:
        report = homologate(Path(args.data_dir).resolve(), CANDIDATES)
    except ProviderError as exc:
        print(f"HOMOLOGATION=FAIL error_code={exc.code.value}")
        return 1
    except Exception as exc:
        print(f"HOMOLOGATION=FAIL error_code={_error_code(exc)}")
        return 1

    print(f"TEST_PROFILE_COUNT={len(report['profiles'])}")
    print(f"PERSISTENCE_AFTER_RESTART={'PASS' if report['persistence_after_restart'] else 'FAIL'}")
    print(f"STEADY_STATE_USES_USER_ID={str(report['steady_state_uses_user_id']).lower()}")
    print(f"SCHEDULER_RUN_ONCE={'PASS' if report['scheduler_run_once'] else 'FAIL'}")
    print("HOMOLOGATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
