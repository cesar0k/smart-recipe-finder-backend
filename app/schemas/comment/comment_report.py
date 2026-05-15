from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CommentReportItem(BaseModel):
    """A single report row (one reporter, one reason)."""

    model_config = ConfigDict(from_attributes=True)

    reporter_id: int
    reporter_username: str | None = None
    reason: str
    created_at: datetime


class ReportedCommentResponse(BaseModel):
    """Aggregated view of a commented that has been reported.

    Contains the comment itself, all associated reports, and enough
    context (recipe, parent comment) for the moderator to decide.
    """

    comment_id: int
    recipe_id: int
    recipe_title: str
    content: str
    is_deleted: bool
    author_id: int
    author_username: str | None = None
    created_at: datetime

    # Parent comment context (if this is a reply)
    parent_comment_id: int | None = None
    parent_content: str | None = None
    parent_author_username: str | None = None

    # Reports
    report_count: int
    reports: list[CommentReportItem]
