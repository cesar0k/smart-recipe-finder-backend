from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Cuisine(Base):
    """Reference table for cuisine names used by recipes.

    Populated lazily: when a recipe is saved with a new cuisine name we
    `find-or-create` an entry. Once present, the row is shared by all
    recipes for that cuisine. Frees `recipes.cuisine_id` from string
    duplication and lets us add localised names / icons later.
    """

    __tablename__ = "cuisines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
