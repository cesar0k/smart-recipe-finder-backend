from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Ingredient(Base):
    """Reference table for ingredient names.

    Names are stored lowercase + trimmed (normalised on write) so look-ups
    are exact-match against the unique index. Drafts intentionally keep
    their own JSONB snapshot of the proposed ingredients and don't share
    rows here.
    """

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
