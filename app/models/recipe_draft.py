from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base
from .enums import DraftStatus, RecipeDifficulty, pg_enum


class RecipeDraft(Base):
    __tablename__ = "recipe_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    instructions: Mapped[str] = mapped_column(String(50000), nullable=False)
    cooking_time_in_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[RecipeDifficulty] = mapped_column(
        pg_enum(RecipeDifficulty, name="recipe_difficulty"),
        nullable=False,
    )
    cuisine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=[], nullable=False
    )

    status: Mapped[DraftStatus] = mapped_column(
        pg_enum(DraftStatus, name="draft_status"),
        default=DraftStatus.PENDING,
        nullable=False,
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
