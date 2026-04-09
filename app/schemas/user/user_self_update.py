from pydantic import BaseModel, EmailStr, Field


class UserSelfUpdate(BaseModel):
    username: str | None = Field(None, min_length=3, max_length=100)
    email: EmailStr | None = None
