from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response

from instagram.collector import InstagramCollectorError, StoryCollector
from instagram.parser import normalize_username
from instagram.session import InstagramSessionError
from rss.builder import build_story_rss


BASE_DIR = Path(__file__).resolve().parent
VIEWER_FILE = BASE_DIR / "static" / "index.html"

app = FastAPI(
    title="Instagram Stories RSS",
    description="Converte exclusivamente Stories ativos do Instagram em JSON e Media RSS.",
    version="0.2.0",
)


@app.get("/", include_in_schema=False)
def viewer() -> FileResponse:
    return FileResponse(VIEWER_FILE)


@app.get("/viewer", include_in_schema=False)
def viewer_alias() -> FileResponse:
    return FileResponse(VIEWER_FILE)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _fetch_stories(profile: str):
    try:
        username = normalize_username(profile)
        stories = StoryCollector().fetch(username)
        return username, stories
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InstagramSessionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InstagramCollectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _stories_payload(profile: str) -> dict:
    username, stories = _fetch_stories(profile)
    return {
        "username": username,
        "count": len(stories),
        "stories": [story.to_dict() for story in stories],
    }


@app.get("/stories")
def stories_query(profile: str = Query(..., description="URL, @username ou username do Instagram")) -> dict:
    return _stories_payload(profile)


@app.get("/stories/{username}")
def stories_path(username: str) -> dict:
    return _stories_payload(username)


def _rss_response(profile: str) -> Response:
    username, stories = _fetch_stories(profile)
    xml = build_story_rss(username, stories)
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/rss/stories")
def rss_query(profile: str = Query(..., description="URL, @username ou username do Instagram")) -> Response:
    return _rss_response(profile)


@app.get("/rss/stories/{username}")
def rss_path(username: str) -> Response:
    return _rss_response(username)
