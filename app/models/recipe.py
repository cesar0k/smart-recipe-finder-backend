from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import Base
from .enums import RecipeDifficulty, RecipeStatus, pg_enum

if TYPE_CHECKING:
    from .cuisine import Cuisine
    from .recipe_image import RecipeImage
    from .recipe_tags import RecipeTags
    from .user import User


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
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=[], nullable=False)
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
    # engagement_score = favorites_count*1.0 + ratings_count*2.0 + comments_count*3.0
    # Recomputed synchronously after every rating/comment/favorite mutation.
    engagement_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0.0"), nullable=False
    )

    # Relationship to User (lazy="raise" — must explicitly load via selectinload)
    owner: Mapped[User | None] = relationship("User", lazy="raise")

    # Relationship to Cuisine — small reference table; lazy="raise" so callers
    # explicitly opt into the join (we keep it as a separate query in most
    # places to stay friendly to the existing selectinload patterns).
    cuisine_ref: Mapped[Cuisine | None] = relationship("Cuisine", lazy="raise")

    # Relationship to RecipeTags — 1:1, NULL until background task completes.
    # lazy="noload": never auto-load (prevents N+1), returns None if not selectinloaded.
    # passive_deletes=True + cascade="all, delete-orphan" lets the DB's ON DELETE
    # CASCADE handle the FK cleanup without SQLAlchemy trying to NULL it out first.
    tags: Mapped[RecipeTags | None] = relationship(
        "RecipeTags",
        back_populates="recipe",
        lazy="noload",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Recipe images — normalised one-row-per-image. position is the display
    # order (matches the old ARRAY index). cascade deletes the rows when the
    # recipe is deleted; passive_deletes defers to the DB's ON DELETE CASCADE.
    images: Mapped[list[RecipeImage]] = relationship(
        "RecipeImage",
        order_by="RecipeImage.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="noload",
    )

    @property
    def image_urls(self) -> list[str]:
        """Pydantic-compatible accessor — flat list of full-size URLs in order.
        Requires the ``images`` relationship to be selectinloaded."""
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
        """Pydantic-friendly accessor: returns the cuisine *name* (or None).
        Requires the ``cuisine_ref`` relationship to be selectinloaded."""
        try:
            return self.cuisine_ref.name if self.cuisine_ref else None
        except Exception:
            return None

    @property
    def owner_username(self) -> str | None:
        """Computed property — Pydantic reads it via from_attributes=True."""
        try:
            return self.owner.username if self.owner else None
        except Exception:
            # Relationship not loaded (lazy="raise" triggers error)
            return None

    @property
    def owner_display_name(self) -> str | None:
        """Optional display name; callers fall back to username when None."""
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
        """Set dynamically by service layer when needed. Defaults to False."""
        return getattr(self, "_has_pending_draft", False)

    @has_pending_draft.setter
    def has_pending_draft(self, value: bool) -> None:
        self._has_pending_draft = value
