from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    username: str
    display_name: str | None = None
    avatar_url: str | None = None
    role: str
    auth_provider: str
    is_active: bool
    created_at: datetime
    email_verified: bool = False
    pending_email: str | None = None
    language: str = "ru"
