from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base


class UserFollow(Base):
    """Join table: follower_id follows followed_id (composite PK, CASCADE FKs)."""

    __tablename__ = "user_follows"

    follower_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    followed_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_user_follows_followed_created", "followed_id", "created_at"),
        Index("ix_user_follows_follower_id", "follower_id"),
    )
