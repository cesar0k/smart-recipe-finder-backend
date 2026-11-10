"""Agent-friendly DTOs.

The REST API returns the full ``Recipe`` model, which is too verbose for a
search result list. ``RecipeCard`` keeps only the fields an agent needs to
choose a recipe; ``RecipeDetails`` adds ingredients and instructions for the
single-recipe view.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.mcp.config import settings


def _flatten_tags(tags: dict[str, Any] | None) -> list[str]:
    if not tags:
        return []

    result: list[str] = []
    for key in ("vegetarian", "vegan", "gluten_free", "dairy_free"):
        if tags.get(key) is True:
            result.append(key.replace("_", "-"))

    for key in (
        "meal_type",
        "main_protein",
        "cooking_method",
        "spice_level",
        "occasion",
        "cost_tier",
        "technique_difficulty",
        "cultural_sub_region",
    ):
        value = tags.get(key)
        if isinstance(value, str) and value:
            result.append(value)

    allergens = tags.get("allergens") or []
    if isinstance(allergens, list):
        result.extend(f"allergen:{allergen}" for allergen in allergens if isinstance(allergen, str))

    return list(dict.fromkeys(result))


def _first_image_url(data: dict[str, Any]) -> str | None:
    image_urls = data.get("image_urls") or []
    if isinstance(image_urls, list) and image_urls:
        return str(image_urls[0])
    return None


class RecipeCard(BaseModel):
    id: int
    title: str
    description: str | None = None
    cooking_time_in_minutes: int | None = None
    difficulty: str | None = None
    cuisine: str | None = None
    average_rating: float = 0.0
    ratings_count: int = 0
    favorites_count: int = 0
    comments_count: int = 0
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None
    url: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "RecipeCard":
        recipe_id = int(data["id"])
        return cls(
            id=recipe_id,
            title=str(data.get("title") or "Untitled recipe"),
            description=data.get("description"),
            cooking_time_in_minutes=data.get("cooking_time_in_minutes"),
            difficulty=data.get("difficulty"),
            cuisine=data.get("cuisine"),
            average_rating=float(data.get("average_rating") or 0.0),
            ratings_count=int(data.get("ratings_count") or 0),
            favorites_count=int(data.get("favorites_count") or 0),
            comments_count=int(data.get("comments_count") or 0),
            tags=_flatten_tags(data.get("tags")),
            image_url=_first_image_url(data),
            url=f"{settings.SRF_WEB_URL.rstrip('/')}/recipe/{recipe_id}",
        )


class RecipeDetails(RecipeCard):
    instructions: str | None = None
    ingredients: list[str] = Field(default_factory=list)
    owner_username: str | None = None
    owner_display_name: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "RecipeDetails":
        card = RecipeCard.from_api(data)
        ingredients = [
            str(item["name"])
            for item in (data.get("ingredients") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        return cls(
            **card.model_dump(),
            instructions=data.get("instructions"),
            ingredients=ingredients,
            owner_username=data.get("owner_username"),
            owner_display_name=data.get("owner_display_name"),
        )
