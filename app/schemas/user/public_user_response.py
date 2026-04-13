from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PublicUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    avatar_url: str | None = None
    role: str = "user"
    created_at: datetime
    recipe_count: int = 0
