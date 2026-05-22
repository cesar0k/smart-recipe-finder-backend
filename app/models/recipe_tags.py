from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from .base import Base
from .enums import (
    CookingMethod,
    CostTier,
    MainProtein,
    MealType,
    Occasion,
    SpiceLevel,
    TechniqueDifficulty,
    pg_enum,
)

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

    # ── Classification (Postgres ENUM-backed) ─────────────────────────────────
    # cultural_sub_region stays as a free-text String — its value domain is
    # open-ended (LLM emits names like "Volga region", "Caucasian", ...).
    meal_type: Mapped[MealType | None] = mapped_column(
        pg_enum(MealType, name="meal_type"), nullable=True
    )
    main_protein: Mapped[MainProtein | None] = mapped_column(
        pg_enum(MainProtein, name="main_protein"), nullable=True
    )
    cooking_method: Mapped[CookingMethod | None] = mapped_column(
        pg_enum(CookingMethod, name="cooking_method"), nullable=True
    )
    spice_level: Mapped[SpiceLevel | None] = mapped_column(
        pg_enum(SpiceLevel, name="spice_level"), nullable=True
    )
    occasion: Mapped[Occasion | None] = mapped_column(
        pg_enum(Occasion, name="occasion"), nullable=True
    )
    cost_tier: Mapped[CostTier | None] = mapped_column(
        pg_enum(CostTier, name="cost_tier"), nullable=True
    )
    technique_difficulty: Mapped[TechniqueDifficulty | None] = mapped_column(
        pg_enum(TechniqueDifficulty, name="technique_difficulty"), nullable=True
    )
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
