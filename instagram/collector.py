import hashlib
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from instagram.media_proxy import register_media_content
from instagram.models import StoryMedia
from instagram.session import InstagramSessionError, create_loader


class InstagramCollectorError(RuntimeError):
    pass


class StoryCollector:
    """Collect active Stories through Instagram's web interface.

    Instaloader is used only to read the persistent session file. The actual
    Story navigation happens in a real Chromium page to avoid Instaloader's
    API/GraphQL request path.
    """

    def __init__(self, browser_channel: str | None = None, headless: bool = True) -> None:
        self.browser_channel = browser_channel or os.getenv("INSTAGRAM_BROWSER_CHANNEL", "chrome").strip() or "chrome"
        self.headless = headless

    def fetch(self, username: str) -> list[StoryMedia]:
        try:
            loader = create_loader()
            cookies = loader.context.save_session()
            if not cookies.get("sessionid"):
                raise InstagramSessionError("A sessão do Instagram não contém um cookie sessionid válido.")
        except InstagramSessionError:
            raise
        except Exception as exc:
            raise InstagramSessionError("Não foi possível carregar a sessão persistente do Instagram.") from exc

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise InstagramCollectorError(
                "O coletor web precisa do Playwright. Execute pip install -r requirements.txt."
            ) from exc

        try:
            with sync_playwright() as playwright:
                browser = self._launch_browser(playwright, PlaywrightError)
                try:
                    context = browser.new_context(
                        locale="pt-BR",
                        viewport={"width": 1280, "height": 900},
                    )
                    context.add_cookies(self._playwright_cookies(cookies))
                    page = context.new_page()
                    media_requests: list[str] = []
                    media_response_sizes: dict[str, int] = {}
                    media_assets: dict[str, list[tuple[int, bytes]]] = {}
                    page.on(
                        "response",
                        lambda response: self._record_media_response(
                            response,
                            media_requests,
                            media_response_sizes,
                            media_assets,
                        ),
                    )
                    page.goto(
                        f"https://www.instagram.com/stories/{username}/",
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    page.wait_for_timeout(1_000)
                    self._raise_for_auth_state(page)
                    return self._collect_from_story_viewer(
                        page,
                        username,
                        PlaywrightTimeoutError,
                        media_requests,
                        media_response_sizes,
                        media_assets,
                    )
                finally:
                    browser.close()
        except InstagramSessionError:
            raise
        except InstagramCollectorError:
            raise
        except PlaywrightTimeoutError as exc:
            raise InstagramCollectorError(
                "O Instagram demorou demais para carregar a página de Stories."
            ) from exc
        except PlaywrightError as exc:
            message = str(exc)
            if "Executable doesn't exist" in message or "channel" in message.lower():
                raise InstagramCollectorError(
                    "Não foi possível iniciar o Chrome. Instale o Google Chrome ou configure "
                    "INSTAGRAM_BROWSER_CHANNEL com um canal Chromium disponível."
                ) from exc
            raise InstagramCollectorError(f"Falha no navegador do Instagram: {message}") from exc
        except Exception as exc:
            raise InstagramCollectorError(f"Falha ao consultar Stories pela interface web: {exc}") from exc

    def _launch_browser(self, playwright, playwright_error):
        try:
            return playwright.chromium.launch(
                channel=self.browser_channel,
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except playwright_error:
            if self.browser_channel != "chrome":
                raise
            return playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

    @staticmethod
    def _playwright_cookies(cookies: dict[str, str]) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "value": value,
                "domain": ".instagram.com",
                "path": "/",
                "secure": True,
            }
            for name, value in cookies.items()
            if value
        ]

    @staticmethod
    def _raise_for_auth_state(page) -> None:
        url = page.url.lower()
        if "/accounts/login" in url or "/challenge/" in url or "/checkpoint/" in url:
            raise InstagramSessionError(
                "A sessão não foi aceita pelo Instagram no navegador. Gere uma nova sessão persistente."
            )

        body_text = page.locator("body").inner_text(timeout=10_000).lower()
        if "suspicious login" in body_text or "confirme sua identidade" in body_text:
            raise InstagramSessionError(
                "O Instagram solicitou uma verificação adicional para essa sessão."
            )

    def _collect_from_story_viewer(
        self,
        page,
        username: str,
        timeout_error,
        media_requests: list[str],
        media_response_sizes: dict[str, int],
        media_assets: dict[str, list[tuple[int, bytes]]],
    ) -> list[StoryMedia]:
        self._open_story_viewer(page)
        try:
            self._media_scope(page).locator("img, video").first.wait_for(
                state="visible",
                timeout=12_000,
            )
        except timeout_error:
            body_text = page.locator("body").inner_text(timeout=5_000).lower()
            if "page isn't available" in body_text or "página não está disponível" in body_text:
                raise InstagramCollectorError(f"Perfil @{username} não encontrado")
            return []

        stories: list[StoryMedia] = []
        seen_urls: set[str] = set()
        seen_media_keys: set[str] = set()
        for _ in range(100):
            media_type, media_url = self._wait_for_media_url(
                page,
                media_requests,
                seen_urls,
                seen_media_keys,
                media_response_sizes,
            )
            if not media_url:
                break

            seen_urls.add(media_url)
            media_key = self._media_key(media_type, media_url)
            if media_key in seen_media_keys:
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(450)
                continue

            seen_media_keys.add(media_key)
            if media_type == "video":
                page.wait_for_timeout(1_200)
                assembled_media = self._assemble_media(media_url, media_assets)
                if assembled_media:
                    register_media_content(media_url, assembled_media, "video/mp4")
            created_at = self._current_story_created_at(page) or datetime.now(timezone.utc)
            story_id = hashlib.sha256(media_key.encode("utf-8")).hexdigest()[:20]
            stories.append(
                StoryMedia(
                    id=story_id,
                    username=username,
                    media_type=media_type,
                    media_url=media_url,
                    created_at=created_at,
                    expires_at=created_at + timedelta(hours=24),
                )
            )

            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(450)

        return stories

    @staticmethod
    def _current_story_created_at(page) -> datetime | None:
        """Read the original publication time from Instagram's Story header."""
        media_scope = StoryCollector._media_scope(page)
        time_nodes = media_scope.locator("time[datetime]")
        for index in range(time_nodes.count()):
            node = time_nodes.nth(index)
            try:
                if not node.is_visible():
                    continue
                value = node.get_attribute("datetime")
            except Exception:
                continue
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        # Older Instagram layouts may expose only text such as "21 h".
        try:
            text = media_scope.inner_text(timeout=1_000)
        except Exception:
            return None
        match = re.search(
            r"(?<!\w)(?:há\s*)?(\d+)\s*(seg(?:undo)?s?|s|min(?:uto)?s?|m|h(?:ora)?s?|"
            r"d(?:ia)?s?|sem(?:ana)?s?|w(?:eeks?)?)(?!\w)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith(("sem", "w")):
            delta = timedelta(weeks=amount)
        elif unit.startswith(("seg", "s")):
            delta = timedelta(seconds=amount)
        elif unit.startswith(("min", "m")):
            delta = timedelta(minutes=amount)
        elif unit.startswith(("h",)):
            delta = timedelta(hours=amount)
        elif unit.startswith(("d",)):
            delta = timedelta(days=amount)
        return datetime.now(timezone.utc) - delta

    @staticmethod
    def _media_key(media_type: str, media_url: str) -> str:
        if media_type == "video":
            return urlsplit(media_url).path
        return media_url

    @staticmethod
    def _wait_for_media_url(
        page,
        media_requests: list[str],
        seen_urls: set[str],
        seen_media_keys: set[str],
        media_response_sizes: dict[str, int],
    ) -> tuple[str, str | None]:
        media_type, media_url = "image", None
        for _ in range(24):
            media_type, media_url = StoryCollector._current_media(
                page,
                media_requests,
                seen_urls,
                seen_media_keys,
                media_response_sizes,
            )
            if media_url:
                return media_type, media_url
            page.wait_for_timeout(500)
        return media_type, media_url

    @staticmethod
    def _open_story_viewer(page) -> None:
        """Click Instagram's intermediate story-opening control when present."""
        for label in ("Ver story", "View story", "See story"):
            for control in (
                page.get_by_role("button", name=label, exact=True),
                page.get_by_text(label, exact=True),
            ):
                try:
                    control.first.wait_for(state="visible", timeout=4_000)
                    control.first.click()
                    page.wait_for_timeout(800)
                    return
                except Exception:
                    continue

    @staticmethod
    def _current_media(
        page,
        media_requests: list[str] | None = None,
        seen_urls: set[str] | None = None,
        seen_media_keys: set[str] | None = None,
        media_response_sizes: dict[str, int] | None = None,
    ) -> tuple[str, str | None]:
        media_scope = StoryCollector._media_scope(page)
        seen = seen_urls or set()
        seen_keys = seen_media_keys or set()
        videos = media_scope.locator("video")
        if videos.count():
            video = videos.last
            src = video.get_attribute("src") or video.get_attribute("data-src")
            if src and src.startswith("http"):
                if StoryCollector._media_key("video", src) not in seen_keys:
                    return "video", src
                return "video", None
            if src and src.startswith("blob:"):
                candidates = [
                    request_url
                    for request_url in media_requests or []
                    if request_url not in seen
                    and StoryCollector._media_key("video", request_url) not in seen_keys
                ]
                sizes = media_response_sizes or {}
                complete_candidates = [
                    request_url for request_url in candidates if sizes.get(request_url, 0) >= 64 * 1024
                ]
                if complete_candidates:
                    return "video", complete_candidates[0]
                medium_candidates = [
                    request_url for request_url in candidates if sizes.get(request_url, 0) > 1024
                ]
                if medium_candidates:
                    return "video", medium_candidates[0]
                return "video", None

        images = media_scope.locator("img")
        for index in range(images.count() - 1, -1, -1):
            image = images.nth(index)
            src = image.get_attribute("src")
            if not src:
                srcset = image.get_attribute("srcset") or ""
                candidates = re.findall(r"(https?://[^\s,]+)", srcset)
                src = candidates[-1] if candidates else None
            if (
                src
                and src.startswith("http")
                and src not in seen
                and not StoryCollector._is_profile_image(src)
            ):
                return "image", src

        return "image", None

    @staticmethod
    def _media_scope(page):
        dialog = page.locator('[role="dialog"]')
        return dialog if dialog.count() else page

    @staticmethod
    def _is_profile_image(url: str) -> bool:
        path = urlsplit(url).path.lower()
        return "profile_pic" in path or "-19/" in path

    @staticmethod
    def _record_media_response(
        response,
        media_requests: list[str],
        media_response_sizes: dict[str, int],
        media_assets: dict[str, list[tuple[int, bytes]]],
    ) -> None:
        url = response.url
        path = url.split("?", 1)[0].lower()
        if not path.endswith((".mp4", ".webm", ".m3u8")):
            return
        if url not in media_requests:
            media_requests.append(url)
        try:
            size = int(response.headers.get("content-length", "0"))
        except (TypeError, ValueError):
            size = 0
        media_response_sizes[url] = max(size, media_response_sizes.get(url, 0))
        try:
            content = response.body()
        except Exception:
            return
        if content:
            query = parse_qs(urlsplit(url).query)
            try:
                offset = int(query.get("bytestart", ["0"])[0])
            except (TypeError, ValueError):
                offset = 0
            asset_path = urlsplit(url).path
            entries = media_assets.setdefault(asset_path, [])
            if not any(existing_offset == offset and existing_body == content for existing_offset, existing_body in entries):
                entries.append((offset, content))

    @staticmethod
    def _assemble_media(media_url: str, media_assets: dict[str, list[tuple[int, bytes]]]) -> bytes | None:
        asset_path = urlsplit(media_url).path
        entries = media_assets.get(asset_path, [])
        if not entries:
            return None

        ordered = sorted(entries, key=lambda entry: entry[0])
        parts = [content for _, content in ordered if content[4:8] != b"sidx"]
        if not parts or parts[0][4:8] != b"ftyp":
            return None
        return b"".join(parts)
