from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from app.models._base.base import Base
from app.models._base.enums import RecipeDifficulty, RecipeStatus, pg_enum

if TYPE_CHECKING:
    from app.models.auth.user import User
    from app.models.recipe.cuisine import Cuisine
    from app.models.recipe.recipe_image import RecipeImage
    from app.models.recipe.recipe_ingredient import RecipeIngredient
    from app.models.recipe.recipe_tags import RecipeTags


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    instructions: Mapped[str] = mapped_column(String(50000), nullable=False)
    cooking_time_in_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[RecipeDifficulty] = mapped_column(
        pg_enum(RecipeDifficulty, name="recipe_difficulty"),
        nullable=False,
    )
    cuisine_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cuisines.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[RecipeStatus] = mapped_column(
        pg_enum(RecipeStatus, name="recipe_status"),
        default=RecipeStatus.APPROVED,
        server_default=text("'approved'"),
        nullable=False,
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    favorites_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    average_rating: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0.0"), nullable=False
    )
    ratings_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    comments_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    # favorites*1 + ratings*2 + comments*3, recomputed on every related write.
    engagement_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0.0"), nullable=False
    )

    # Relationships use lazy="raise"/"noload" — consumers must selectinload.
    owner: Mapped[User | None] = relationship("User", lazy="raise")
    cuisine_ref: Mapped[Cuisine | None] = relationship("Cuisine", lazy="raise")
    tags: Mapped[RecipeTags | None] = relationship(
        "RecipeTags",
        back_populates="recipe",
        lazy="noload",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    images: Mapped[list[RecipeImage]] = relationship(
        "RecipeImage",
        order_by="RecipeImage.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="noload",
    )
    recipe_ingredients: Mapped[list[RecipeIngredient]] = relationship(
        "RecipeIngredient",
        order_by="RecipeIngredient.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="noload",
    )

    # Pydantic-facing accessors. Each one swallows the lazy="raise" exception
    # so a missing selectinload degrades to an empty/None result rather than
    # blowing up serialization.

    @property
    def ingredients(self) -> list[dict[str, str | None]]:
        try:
            return [{"name": ri.ingredient.name} for ri in self.recipe_ingredients]
        except Exception:
            return []

    @property
    def image_urls(self) -> list[str]:
        try:
            return [img.full_url for img in self.images]
        except Exception:
            return []

    @property
    def thumbnail_urls(self) -> list[str]:
        try:
            return [img.thumbnail_url for img in self.images]
        except Exception:
            return []

    @property
    def cuisine(self) -> str | None:
        try:
            return self.cuisine_ref.name if self.cuisine_ref else None
        except Exception:
            return None

    @property
    def owner_username(self) -> str | None:
        try:
            return self.owner.username if self.owner else None
        except Exception:
            return None

    @property
    def owner_display_name(self) -> str | None:
        try:
            return self.owner.display_name if self.owner else None
        except Exception:
            return None

    @property
    def owner_avatar_url(self) -> str | None:
        try:
            return self.owner.avatar_url if self.owner else None
        except Exception:
            return None

    @property
    def has_pending_draft(self) -> bool:
        """Set dynamically by the service layer; defaults to False."""
        return getattr(self, "_has_pending_draft", False)

    @has_pending_draft.setter
    def has_pending_draft(self, value: bool) -> None:
        self._has_pending_draft = value
