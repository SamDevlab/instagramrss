"""Create a persistent Instaloader session for the Stories collector."""

import os
import sys
import argparse
from pathlib import Path

import instaloader
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


class SessionCreationError(RuntimeError):
    """An expected error while creating the Instagram session."""


def _session_path(configured_path: str) -> Path:
    path = Path(configured_path or "./session/instagram.session")
    return path if path.is_absolute() else PROJECT_ROOT / path


def _safe_message(error: Exception, password: str) -> str:
    message = str(error) or error.__class__.__name__
    return message.replace(password, "<senha ocultada>") if password else message


def _build_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )


def _create_session_from_browser(
    loader: instaloader.Instaloader,
    username: str,
    browser: str,
    session_file: Path,
) -> None:
    try:
        from instaloader.__main__ import get_cookies_from_instagram
    except ImportError as exc:
        raise SessionCreationError(
            "Suporte a cookies do navegador indisponível. Execute pip install -r requirements.txt."
        ) from exc

    try:
        cookies = get_cookies_from_instagram("instagram", browser)
        if not cookies.get("sessionid"):
            raise SessionCreationError(
                f"O {browser} não forneceu um cookie sessionid válido. Confirme o login em @{username}."
            )
        loader.context.update_cookies(cookies)
    except Exception as exc:
        if isinstance(exc, SessionCreationError):
            raise
        raise SessionCreationError(
            f"Não foi possível importar a sessão do {browser}: {_safe_message(exc, '')}"
        ) from exc

    loader.save_session_to_file(filename=str(session_file))


def create_session(browser: str | None = None) -> Path:
    if not ENV_FILE.exists():
        raise SessionCreationError("Arquivo .env não encontrado na raiz do projeto.")

    load_dotenv(ENV_FILE, override=True)
    username = os.getenv("INSTAGRAM_USERNAME", "").strip()
    password = os.getenv("INSTAGRAM_PASSWORD", "")
    configured_path = os.getenv("INSTAGRAM_SESSION_FILE", "./session/instagram.session").strip()

    if not username or username == "COLOQUE_SEU_USUARIO_AQUI":
        raise SessionCreationError("Preencha INSTAGRAM_USERNAME no arquivo .env.")
    if not browser and not password.strip():
        raise SessionCreationError("Preencha INSTAGRAM_PASSWORD no arquivo .env apenas durante a criação da sessão.")

    session_file = _session_path(configured_path)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    loader = _build_loader()

    try:
        if browser:
            print(f"Importando a sessão do Instagram a partir do {browser}...")
            _create_session_from_browser(loader, username, browser, session_file)
            print(f"Sessão criada com sucesso em: {session_file}")
            print("A aplicação poderá reutilizar essa sessão sem solicitar a senha novamente.")
            return session_file

        print(f"Iniciando autenticação do Instagram para @{username}...")
        try:
            loader.login(username, password)
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            print("O Instagram solicitou autenticação em duas etapas.")
            code = input("Digite o código 2FA enviado pelo Instagram: ").strip()
            if not code:
                raise SessionCreationError("Nenhum código 2FA foi informado.")
            loader.two_factor_login(code)
        loader.save_session_to_file(filename=str(session_file))
    except SessionCreationError:
        raise
    except instaloader.exceptions.BadCredentialsException as exc:
        raise SessionCreationError("O Instagram rejeitou o usuário ou a senha informados.") from exc
    except instaloader.exceptions.LoginException as exc:
        raise SessionCreationError(
            "O Instagram exigiu uma verificação ou desafio adicional. Conclua-o no fluxo do Instagram "
            "e execute este script novamente."
        ) from exc
    except Exception as exc:
        raise SessionCreationError(f"Falha ao criar a sessão: {_safe_message(exc, password)}") from exc

    print(f"Sessão criada com sucesso em: {session_file}")
    print("A aplicação poderá reutilizar essa sessão sem solicitar a senha novamente.")
    return session_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria uma sessão persistente do Instagram para o projeto.")
    parser.add_argument(
        "--browser",
        choices=("chrome", "edge", "firefox", "brave", "chromium", "opera", "opera_gx", "vivaldi", "safari"),
        help="Importa os cookies do navegador em vez de fazer login com senha.",
    )
    args = parser.parse_args()

    try:
        create_session(browser=args.browser)
    except SessionCreationError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
