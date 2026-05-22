from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base.base import Base


class Ingredient(Base):
    """Reference table for ingredient names (stored lowercase + trimmed)."""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)
