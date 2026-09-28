import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from instagram.parser import normalize_username
from instagram.scheduler import StoryScheduler
from instagram.service import SourceService
from instagram.storage import SourceStore
from rss.builder import build_source_rss


BASE_DIR = Path(__file__).resolve().parent
VIEWER_FILE = BASE_DIR / "static" / "index.html"

source_store = SourceStore()
source_service = SourceService(store=source_store)
story_scheduler = StoryScheduler(source_service)


def _scheduler_enabled() -> bool:
    return os.getenv("STORY_SCHEDULER_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if _scheduler_enabled():
        story_scheduler.start()
    try:
        yield
    finally:
        story_scheduler.stop()


app = FastAPI(
    title="Instagram Stories RSS",
    description="Stories ativos do Instagram com cache persistente e Media RSS local.",
    version="0.3.0",
    lifespan=lifespan,
)


class SourceCreateRequest(BaseModel):
    story_url: str
    refresh: bool = False


@app.get("/", include_in_schema=False)
def viewer() -> FileResponse:
    return FileResponse(VIEWER_FILE)


@app.get("/viewer", include_in_schema=False)
def viewer_alias() -> FileResponse:
    return FileResponse(VIEWER_FILE)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "scheduler_enabled": _scheduler_enabled(),
        "scheduler_running": story_scheduler.running,
    }


def _source_for_username(value: str) -> dict:
    username = normalize_username(value)
    source = source_store.find_source_by_username(username)
    if not source:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Fonte @{username} ainda não cadastrada. "
                "Cadastre com POST /sources usando o link de um Story ativo."
            ),
        )
    return source


def _snapshot_payload(source: dict) -> dict:
    source_id = source["source_id"]
    snapshot = source_service.current_snapshot(source_id)
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    stories = []
    for item in snapshot.get("items") or []:
        story = dict(item)
        local_url = f"/media/{source_id}/{item['filename']}"
        story["media_url"] = f"{base}{local_url}" if base else local_url
        story["id"] = item["provider_story_id"]
        story["type"] = item["media_type"]
        story["created_at"] = item["taken_at"]
        stories.append(story)
    return {
        "source_id": source_id,
        "username": source["username"],
        "status": snapshot.get("status", "EMPTY"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "count": len(stories),
        "stories": stories,
    }


@app.post("/sources")
def create_source(payload: SourceCreateRequest) -> dict:
    try:
        return source_service.create_source(payload.story_url, refresh=payload.refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/sources")
def list_sources() -> dict:
    sources = source_service.list_sources()
    return {"count": len(sources), "sources": sources}


@app.get("/sources/{source_id}")
def get_source(source_id: str) -> dict:
    try:
        return source_service.get_source(source_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/sources/{source_id}/refresh")
def refresh_source(source_id: str) -> dict:
    try:
        return source_service.refresh_source(source_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/sources/{source_id}/stories")
def source_stories(source_id: str) -> dict:
    try:
        source = source_store.load_source(source_id)
        return _snapshot_payload(source)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/sources/{source_id}/rss.xml")
def source_rss(source_id: str) -> Response:
    try:
        source = source_store.load_source(source_id)
        snapshot = source_service.current_snapshot(source_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    xml = build_source_rss(source, snapshot, public_base_url=os.getenv("PUBLIC_BASE_URL", ""))
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/stories")
def stories_query(profile: str = Query(..., description="URL, @username ou username do Instagram")) -> dict:
    return _snapshot_payload(_source_for_username(profile))


@app.get("/stories/{username}")
def stories_path(username: str) -> dict:
    return _snapshot_payload(_source_for_username(username))


@app.get("/rss/stories")
def rss_query(profile: str = Query(..., description="URL, @username ou username do Instagram")) -> Response:
    source = _source_for_username(profile)
    return source_rss(source["source_id"])


@app.get("/rss/stories/{username}")
def rss_path(username: str) -> Response:
    source = _source_for_username(username)
    return source_rss(source["source_id"])


@app.get("/media/{source_id}/{filename}")
def persisted_media(source_id: str, filename: str) -> FileResponse:
    try:
        path = source_store.media_path(source_id, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Mídia não encontrada")

    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
