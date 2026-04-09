from pydantic import BaseModel, Field


class UserLogin(BaseModel):
    login: str = Field(..., description="Email or username")
    password: str
