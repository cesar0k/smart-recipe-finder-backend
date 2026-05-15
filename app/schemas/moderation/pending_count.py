from pydantic import BaseModel


class PendingCountResponse(BaseModel):
    recipes: int
    drafts: int
    comment_reports: int = 0
