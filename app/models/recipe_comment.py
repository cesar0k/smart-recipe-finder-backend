from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from .base import Base

if TYPE_CHECKING:
    from .recipe import Recipe
    from .user import User


class RecipeComment(Base):
    """A comment on a recipe, optionally a reply to another comment (max 2 levels).

    Soft-deleted comments set is_deleted=True and content="" — replies remain visible.
    parent_comment_id uses SET NULL so deleting a parent preserves its replies.
    """

    __tablename__ = "recipe_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # None → top-level comment; int → reply to that comment (never a reply-to-reply)
    parent_comment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("recipe_comments.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    recipe: Mapped[Recipe] = relationship("Recipe", lazy="raise")
    user: Mapped[User] = relationship("User", lazy="raise")

    __table_args__ = (
        Index("ix_recipe_comments_recipe_created", "recipe_id", "created_at"),
        Index("ix_recipe_comments_parent_id", "parent_comment_id"),
        Index("ix_recipe_comments_user_id", "user_id"),
    )
