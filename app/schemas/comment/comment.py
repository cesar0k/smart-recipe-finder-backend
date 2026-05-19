from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    parent_comment_id: int | None = None


class CommentReportCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recipe_id: int
    user_id: int
    author_username: str | None = None
    author_avatar_url: str | None = None
    author_role: str | None = None   # "admin" | "moderator" | None
    parent_comment_id: int | None = None
    content: str
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    replies: list[CommentResponse] = Field(default_factory=list)
