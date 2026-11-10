"""Unit tests for agent-facing DTOs (no database required)."""

from __future__ import annotations

from app.mcp.dto import RecipeCard, RecipeDetails


def test_recipe_card_compacts_api_payload() -> None:
    card = RecipeCard.from_api(
        {
            "id": 7,
            "title": "Mushroom soup",
            "description": "Warm and quick",
            "cooking_time_in_minutes": 30,
            "difficulty": "easy",
            "cuisine": "russian",
            "average_rating": 4.5,
            "ratings_count": 2,
            "favorites_count": 3,
            "comments_count": 1,
            "tags": {
                "vegetarian": True,
                "vegan": False,
                "meal_type": "soup",
                "allergens": ["gluten"],
            },
            "image_urls": ["https://example.com/soup.webp"],
        }
    )

    assert card.id == 7
    assert card.title == "Mushroom soup"
    assert card.tags == ["vegetarian", "soup", "allergen:gluten"]
    assert card.image_url == "https://example.com/soup.webp"
    assert card.url.endswith("/recipe/7")


def test_recipe_details_flattens_ingredients() -> None:
    details = RecipeDetails.from_api(
        {
            "id": 7,
            "title": "Mushroom soup",
            "instructions": "Boil, blend, serve.",
            "ingredients": [{"name": "mushrooms"}, {"name": "water"}, {}],
            "owner_username": "chef",
        }
    )

    assert details.ingredients == ["mushrooms", "water"]
    assert details.instructions == "Boil, blend, serve."
    assert details.owner_username == "chef"
