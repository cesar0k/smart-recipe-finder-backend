from pydantic import BaseModel


class GoogleAuthCode(BaseModel):
    code: str
    redirect_uri: str
