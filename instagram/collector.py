import instaloader

from instagram.models import StoryMedia
from instagram.session import InstagramSessionError, create_loader


class InstagramCollectorError(RuntimeError):
    pass


class StoryCollector:
    def fetch(self, username: str) -> list[StoryMedia]:
        try:
            loader = create_loader()
            profile = instaloader.Profile.from_username(loader.context, username)

            stories: list[StoryMedia] = []
            for story in loader.get_stories(userids=[profile.userid]):
                for item in story.get_items():
                    is_video = bool(item.is_video)
                    media_url = item.video_url if is_video and item.video_url else item.url

                    stories.append(
                        StoryMedia(
                            id=str(item.mediaid),
                            username=username,
                            media_type="video" if is_video else "image",
                            media_url=str(media_url),
                            created_at=item.date_utc,
                            expires_at=item.expiring_utc,
                        )
                    )

            stories.sort(key=lambda item: item.created_at, reverse=True)
            return stories
        except InstagramSessionError:
            raise
        except instaloader.exceptions.ProfileNotExistsException as exc:
            raise InstagramCollectorError(f"Perfil @{username} não encontrado") from exc
        except instaloader.exceptions.PrivateProfileNotFollowedException as exc:
            raise InstagramCollectorError(
                f"O perfil @{username} é privado e a conta autenticada não possui acesso"
            ) from exc
        except instaloader.exceptions.LoginRequiredException as exc:
            raise InstagramSessionError("A sessão do Instagram expirou ou não está autenticada") from exc
        except instaloader.exceptions.TooManyRequestsException as exc:
            raise InstagramCollectorError("Instagram aplicou rate limit. Tente novamente mais tarde.") from exc
        except instaloader.exceptions.InstaloaderException as exc:
            raise InstagramCollectorError(f"Falha ao consultar Stories: {exc}") from exc
