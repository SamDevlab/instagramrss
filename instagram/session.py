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


def create_loader() -> instaloader.Instaloader:
    login_username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    configured_session = os.getenv("INSTAGRAM_SESSION_FILE", "./session/instagram.session").strip()
    configured_session = configured_session or "./session/instagram.session"

    if not login_username:
        raise InstagramSessionError(
            "INSTAGRAM_USERNAME não configurado. Stories exigem uma sessão autenticada."
        )

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
        request_timeout=30,
        rate_controller=lambda context: FailFastRateController(context),
    )

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
