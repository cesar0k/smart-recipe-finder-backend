from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base.base import Base


class RecipeImage(Base):
    """One image attached to a recipe (full + thumbnail URL, with display order)."""

    __tablename__ = "recipe_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
    )
    full_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_recipe_images_recipe_position", "recipe_id", "position"),
    )
