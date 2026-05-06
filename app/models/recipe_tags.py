from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from .base import Base

if TYPE_CHECKING:
    from .recipe import Recipe


class RecipeTags(Base):
    """LLM-generated structured tags for a recipe (1:1 with Recipe).

    All tag fields are nullable — a NULL row means tags haven't been generated
    yet (background task pending or LLM failed). Search post-filter treats NULL
    tags as "include" to avoid incorrectly excluding untagged recipes.
    """

    __tablename__ = "recipe_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("recipes.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ── Dietary flags ─────────────────────────────────────────────────────────
    vegetarian: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    vegan: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    gluten_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    dairy_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── Classification enums (stored as String) ───────────────────────────────
    meal_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    main_protein: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cooking_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    spice_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    occasion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    technique_difficulty: Mapped[str | None] = mapped_column(String(15), nullable=True)
    cultural_sub_region: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Allergens (multi-value) ───────────────────────────────────────────────
    allergens: Mapped[list[Any]] = mapped_column(
        ARRAY(String),
        default=list,
        server_default=text("'{}'"),
        nullable=False,
    )

    # ── Metadata ──────────────────────────────────────────────────────────────
    source: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "llm"
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # ── Relationship ──────────────────────────────────────────────────────────
    recipe: Mapped[Recipe] = relationship("Recipe", back_populates="tags", lazy="raise")
