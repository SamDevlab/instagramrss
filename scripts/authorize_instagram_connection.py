"""Provision one encrypted Instagram session connection locally.

This explicit CLI is the first-stage bootstrap. It reads a local session file,
stores the credential encrypted, and prints only public connection metadata.
It never exposes a sessionid or cookie through an API response.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from instagram.auth.service import AuthConnectionError, AuthConnectionService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(
        description="Autoriza explicitamente uma sessão Instagram como auth connection."
    )
    parser.add_argument("--username", required=True, help="Usuário dono da sessão local.")
    parser.add_argument(
        "--session-file",
        default=os.getenv("INSTAGRAM_SESSION_FILE", "./session/instagram.session"),
        help="Arquivo de sessão local a importar.",
    )
    args = parser.parse_args()
    session_file = Path(args.session_file)
    if not session_file.is_absolute():
        session_file = PROJECT_ROOT / session_file

    try:
        connection = AuthConnectionService().create_from_session_file(args.username, session_file)
    except AuthConnectionError as exc:
        print(f"Erro: {exc}")
        return 1

    print(json.dumps(connection.public_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
