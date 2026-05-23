from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models._base.base import Base

if TYPE_CHECKING:
    from app.models.recipe.ingredient import Ingredient


class RecipeIngredient(Base):
    """M2M between Recipe and Ingredient with display position.

    Free-form quantities (e.g. "½ стакана сахара", "щепотка соли", "по вкусу")
    live directly inside ``Ingredient.name`` — we don't split amount/unit into
    structured columns because the UI submits a single string per row.
    """

    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
    )
    ingredient_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ingredients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ingredient: Mapped[Ingredient] = relationship("Ingredient", lazy="raise")

    __table_args__ = (
        UniqueConstraint("recipe_id", "ingredient_id", name="uq_recipe_ingredient"),
        Index("ix_recipe_ingredients_recipe_position", "recipe_id", "position"),
        Index("ix_recipe_ingredients_ingredient", "ingredient_id"),
    )
