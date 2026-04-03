from typing import Annotated, Any

from pydantic import Field, HttpUrl, StringConstraints, field_validator, ConfigDict

from .ingredient import Ingredient
from .recipe_base import RecipeBase


class Recipe(RecipeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=100)
    image_urls: list[Annotated[HttpUrl, StringConstraints(max_length=1024)]] = Field(
        default_factory=list, max_length=10
    )
    owner_id: int | None = None
    status: str = "approved"
    rejection_reason: str | None = None

    @field_validator("image_urls", mode="before")
    @classmethod
    def filter_empty_urls(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, list):
            return [url for url in v if url and isinstance(url, str) and url.strip()]
        return []
