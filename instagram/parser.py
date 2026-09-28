from dataclasses import dataclass
from urllib.parse import urlparse


_RESERVED_PATHS = {
    "accounts",
    "explore",
    "p",
    "reel",
    "reels",
    "stories",
}


@dataclass(frozen=True)
class StoryPermalink:
    username: str
    seed_story_id: str
    canonical_permalink: str


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


def parse_story_permalink(value: str) -> StoryPermalink:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Link de Story não informado")

    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)

    host = (parsed.hostname or "").lower()
    if host not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("O link deve pertencer ao domínio instagram.com")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[0].lower() != "stories":
        raise ValueError("Informe um link no formato /stories/usuario/ID/")

    username = normalize_username(parts[1])
    story_id = parts[2].strip()
    if not story_id.isdigit():
        raise ValueError("O ID do Story precisa ser numérico")

    canonical = f"https://www.instagram.com/stories/{username}/{story_id}/"
    return StoryPermalink(
        username=username,
        seed_story_id=story_id,
        canonical_permalink=canonical,
    )
