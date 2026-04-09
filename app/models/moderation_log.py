from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base


class ModerationLog(Base):
    __tablename__ = "moderation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recipes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    draft_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recipe_drafts.id", ondelete="SET NULL"),
        nullable=True,
    )
    moderator_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Denormalized fields for display (avoid JOINs)
    recipe_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moderator_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
