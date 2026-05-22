from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models._base.base import Base


class RecipeFavorite(Base):
    """Join table: a user has favorited a recipe (composite PK, CASCADE FKs)."""

    __tablename__ = "recipe_favorites"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("recipes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_recipe_favorites_user_created", "user_id", "created_at"),
        Index("ix_recipe_favorites_recipe_id", "recipe_id"),
    )
