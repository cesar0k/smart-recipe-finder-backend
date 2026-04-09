from pydantic import BaseModel, Field


class UserUpdate(BaseModel):
    role: str | None = Field(None, pattern="^(user|moderator)$")
    is_active: bool | None = None
