from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True)
class StoryMedia:
    id: str
    username: str
    media_type: str
    media_url: str
    created_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict:
        data = asdict(self)
        data["type"] = data.pop("media_type")
        data["created_at"] = self.created_at.isoformat()
        data["expires_at"] = self.expires_at.isoformat()
        return data
