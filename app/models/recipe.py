from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from .base import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    instructions: Mapped[str] = mapped_column(String(50000), nullable=False)
    cooking_time_in_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(50), nullable=False)
    cuisine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=[], nullable=False
    )
    image_urls: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default=text("'{}'"), nullable=False
    )
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="approved", server_default=text("'approved'"), nullable=False
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
