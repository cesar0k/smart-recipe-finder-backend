from typing import Annotated

from pydantic import Field, StringConstraints

from .recipe_base import RecipeBase


class RecipeCreate(RecipeBase):
    title: str = Field(..., min_length=3, max_length=255)
    instructions: str = Field(..., max_length=50000)
    cooking_time_in_minutes: int = Field(default=0)
    difficulty: str = Field(..., max_length=50)
    ingredients: list[Annotated[str, StringConstraints(max_length=255)]] = Field(
        default_factory=list, max_length=100
    )
    image_urls: list[str] = []
