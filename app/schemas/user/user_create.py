from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=100)
    display_name: str | None = Field(None, max_length=200)
    password: str = Field(..., min_length=8, max_length=128)
