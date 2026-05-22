from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models._base.base import Base

if TYPE_CHECKING:
    from app.models.auth.user import User
    from app.models.comment.recipe_comment import RecipeComment


class RecipeCommentReport(Base):
    """A user's report of an abusive/spam comment.

    One report per (reporter, comment) pair — UniqueConstraint prevents duplicates.
    Triggers a notification to all moderators/admins; they decide whether to delete.
    """

    __tablename__ = "recipe_comment_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recipe_comments.id", ondelete="CASCADE"), nullable=False
    )
    reporter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    comment: Mapped[RecipeComment] = relationship("RecipeComment", lazy="raise")
    reporter: Mapped[User] = relationship("User", lazy="raise")

    __table_args__ = (
        UniqueConstraint("comment_id", "reporter_id", name="uq_comment_reports_comment_reporter"),
        Index("ix_comment_reports_comment_id", "comment_id"),
        Index("ix_comment_reports_reporter_id", "reporter_id"),
    )
