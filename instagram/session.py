import os
from pathlib import Path

import instaloader
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class InstagramSessionError(RuntimeError):
    pass


class FailFastRateController(instaloader.RateController):
    """Surface Instagram rate limits immediately instead of sleeping for minutes."""

    def handle_429(self, query_type: str) -> None:
        raise instaloader.exceptions.TooManyRequestsException(
            "Instagram aplicou rate limit. Aguarde alguns minutos antes de tentar novamente."
        )


def _build_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        request_timeout=30,
        rate_controller=lambda context: FailFastRateController(context),
    )


def create_loader(
    *,
    login_username: str | None = None,
    session_file: str | Path | None = None,
    credential: dict | None = None,
) -> instaloader.Instaloader:
    login_username = (login_username or os.getenv("INSTAGRAM_USERNAME", "")).strip()
    configured_session = str(session_file or os.getenv("INSTAGRAM_SESSION_FILE", "./session/instagram.session")).strip()
    configured_session = configured_session or "./session/instagram.session"

    if credential is None and not login_username:
        raise InstagramSessionError(
            "INSTAGRAM_USERNAME não configurado. Stories exigem uma sessão autenticada."
        )

    loader = _build_loader()

    if credential is not None:
        raw_cookies = credential.get("cookies") or {}
        cookies = {str(key): str(value) for key, value in raw_cookies.items()}
        if credential.get("sessionid") and "sessionid" not in cookies:
            cookies["sessionid"] = str(credential["sessionid"])
        if not cookies.get("sessionid"):
            raise InstagramSessionError("A credencial não contém uma sessão autenticada válida.")
        try:
            loader.context.update_cookies(cookies)
            return loader
        except Exception as exc:
            raise InstagramSessionError("Não foi possível carregar a conexão Instagram.") from exc

    session_file = Path(configured_session)
    if not session_file.is_absolute():
        session_file = BASE_DIR / session_file

    if not session_file.exists():
        raise InstagramSessionError(
            "Arquivo de sessão não encontrado. Execute python scripts/create_instagram_session.py para criar a primeira sessão."
        )

    try:
        loader.load_session_from_file(login_username, filename=str(session_file))
        return loader
    except Exception as exc:
        raise InstagramSessionError(
            "Não foi possível carregar a sessão do Instagram. Gere uma nova sessão com "
            "python scripts/create_instagram_session.py."
        ) from exc


def load_session_credential(login_username: str, session_file: str | Path) -> dict:
    """Read a local session file for explicit connection provisioning.

    The returned credential is intended for immediate encryption by the auth
    connection service and is never serialized by an HTTP response.
    """

    loader = create_loader(login_username=login_username, session_file=session_file)
    cookies = loader.context.save_session()
    normalized = {str(key): str(value) for key, value in cookies.items()}
    if not normalized.get("sessionid"):
        raise InstagramSessionError("A sessão local não contém sessionid válido.")
    return {"kind": "instagram_session", "username": login_username.strip().lower(), "cookies": normalized}
