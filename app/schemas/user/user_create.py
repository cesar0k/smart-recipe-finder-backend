from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=100)
    display_name: str | None = Field(None, max_length=200)
    password: str = Field(..., min_length=8, max_length=128)
    language: str = Field(default="ru", pattern="^(ru|en)$")
    recaptcha_token: str | None = None
    # "v3" (default, invisible) or "v2" (Safari fallback via visible checkbox).
    recaptcha_type: Literal["v2", "v3"] = "v3"
