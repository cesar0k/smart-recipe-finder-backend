from pydantic import BaseModel


class UnreadCountResponse(BaseModel):
    count: int
