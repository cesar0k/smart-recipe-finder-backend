from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models._base.base import Base

if TYPE_CHECKING:
    from app.models.auth.user import User
    from app.models.recipe.recipe import Recipe


class RecipeRating(Base):
    """One rating (1–5) per user per recipe. Mutable via upsert."""

    __tablename__ = "recipe_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    recipe: Mapped[Recipe] = relationship("Recipe", lazy="raise")
    user: Mapped[User] = relationship("User", lazy="raise")

    __table_args__ = (
        UniqueConstraint("user_id", "recipe_id", name="uq_recipe_ratings_user_recipe"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_recipe_ratings_range"),
        Index("ix_recipe_ratings_recipe_id", "recipe_id"),
        Index("ix_recipe_ratings_user_id", "user_id"),
    )
