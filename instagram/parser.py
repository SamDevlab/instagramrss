from urllib.parse import urlparse


_RESERVED_PATHS = {
    "accounts",
    "explore",
    "p",
    "reel",
    "reels",
    "stories",
}


def normalize_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Perfil do Instagram não informado")

    if "instagram.com" in value.lower():
        candidate_url = value if "://" in value else f"https://{value}"
        parsed = urlparse(candidate_url)
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise ValueError("URL do Instagram inválida")
        value = parts[0]

    username = value.lstrip("@").strip().lower()

    if not username or username in _RESERVED_PATHS:
        raise ValueError("Perfil do Instagram inválido")

    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._")
    if any(char not in allowed for char in username):
        raise ValueError("Username do Instagram contém caracteres inválidos")

    if len(username) > 30:
        raise ValueError("Username do Instagram é muito longo")

    return username
