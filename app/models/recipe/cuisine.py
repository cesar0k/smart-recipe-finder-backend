from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models._base.base import Base


class Cuisine(Base):
    """Reference table for cuisine names (populated find-or-create)."""

    __tablename__ = "cuisines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
