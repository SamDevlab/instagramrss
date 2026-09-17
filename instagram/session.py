import os
from pathlib import Path

import instaloader
from dotenv import load_dotenv


load_dotenv()


class InstagramSessionError(RuntimeError):
    pass


def create_loader() -> instaloader.Instaloader:
    login_username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    password = os.getenv("INSTAGRAM_PASSWORD", "").strip()
    configured_session = os.getenv("INSTAGRAM_SESSION_FILE", "./session/instagram.session").strip()

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
    )

    session_file = Path(configured_session)

    if session_file.exists():
        try:
            loader.load_session_from_file(login_username, filename=str(session_file))
            return loader
        except Exception as exc:
            if not password:
                raise InstagramSessionError(
                    "Não foi possível carregar a sessão do Instagram e nenhuma senha foi configurada para renová-la."
                ) from exc

    if not password:
        raise InstagramSessionError(
            "Arquivo de sessão não encontrado. Configure INSTAGRAM_PASSWORD temporariamente para criar a primeira sessão."
        )

    try:
        loader.login(login_username, password)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        loader.save_session_to_file(filename=str(session_file))
        return loader
    except Exception as exc:
        raise InstagramSessionError(f"Falha ao autenticar no Instagram: {exc}") from exc
