from typing import Annotated, Any

from pydantic import ConfigDict, Field, HttpUrl, StringConstraints, field_validator

from .ingredient import Ingredient
from .recipe_base import RecipeBase
from .recipe_tags import RecipeTagsPublic


class Recipe(RecipeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=100)
    image_urls: list[Annotated[HttpUrl, StringConstraints(max_length=1024)]] = Field(
        default_factory=list, max_length=10
    )
    thumbnail_urls: list[str] = Field(default_factory=list)
    owner_id: int | None = None
    owner_username: str | None = None
    owner_display_name: str | None = None
    owner_avatar_url: str | None = None
    status: str = "approved"
    rejection_reason: str | None = None
    has_pending_draft: bool = False
    tags: RecipeTagsPublic | None = None
    favorites_count: int = 0
    average_rating: float = 0.0
    ratings_count: int = 0
    comments_count: int = 0
    engagement_score: float = 0.0
    # Caller-aware fields: attached by the service layer after cache read;
    # the cached payload itself stays user-agnostic.
    is_favorited: bool = False
    user_rating: int | None = None

    @field_validator("image_urls", mode="before")
    @classmethod
    def filter_empty_urls(cls, v: Any) -> list[str]:
        if not v:
            return []
        if isinstance(v, list):
            return [url for url in v if url and isinstance(url, str) and url.strip()]
        return []
