"""Unit tests for MCP tools (no network or database required)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.mcp.client import RecipeApiError
from app.mcp.tools import recipes as tool_module


class FakeApi:
    """Stand-in for RecipeApiClient that records calls."""

    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> "FakeApi":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, params))
        if self.error is not None:
            raise self.error
        return self.response


def _use_api(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    monkeypatch.setattr(tool_module, "RecipeApiClient", lambda: api)


def _card(recipe_id: int) -> dict[str, Any]:
    return {"id": recipe_id, "title": f"Recipe {recipe_id}", "cuisine": "russian"}


def test_search_recipes_caps_limit_and_maps_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi([_card(1), _card(2), _card(3)])
    _use_api(monkeypatch, api)

    cards = asyncio.run(
        tool_module.search_recipes(
            "soup",
            limit=2,
            include_ingredients=["chicken", "rice"],
            exclude_ingredients=["peanuts"],
            max_time=30,
            difficulty="easy",
            cuisine="russian",
        )
    )

    assert [card.id for card in cards] == [1, 2]
    assert api.calls == [
        (
            "/api/v1/recipes/search/",
            {
                "q": "soup",
                "limit": 2,
                "include_ingredients": "chicken,rice",
                "exclude_ingredients": "peanuts",
                "max_time": 30,
                "difficulty": "easy",
                "cuisine": "russian",
            },
        )
    ]


def test_browse_recipes_passes_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi([_card(1)])
    _use_api(monkeypatch, api)

    cards = asyncio.run(
        tool_module.browse_recipes(
            skip=5,
            limit=1,
            include_ingredients=["egg"],
            exclude_ingredients=["milk"],
            min_time=10,
            max_time=20,
            difficulty="medium",
            cuisine="italian",
            meal_type="soup",
            sort="popular",
        )
    )

    assert [card.id for card in cards] == [1]
    assert api.calls == [
        (
            "/api/v1/recipes/",
            {
                "skip": 5,
                "limit": 1,
                "include_ingredients": "egg",
                "exclude_ingredients": "milk",
                "min_time": 10,
                "max_time": 20,
                "difficulty": "medium",
                "cuisine": "italian",
                "meal_type": "soup",
                "sort": "popular",
            },
        )
    ]


def test_get_recipe_maps_details(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(
        {
            "id": 7,
            "title": "Soup",
            "instructions": "Boil.",
            "ingredients": [{"name": "water"}, {}],
            "owner_username": "chef",
        }
    )
    _use_api(monkeypatch, api)

    details = asyncio.run(tool_module.get_recipe(7))

    assert details.id == 7
    assert details.ingredients == ["water"]
    assert details.instructions == "Boil."
    assert api.calls == [("/api/v1/recipes/7", None)]


def test_find_similar_recipes_uses_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi([_card(2)])
    _use_api(monkeypatch, api)

    cards = asyncio.run(tool_module.find_similar_recipes(7, limit=3))

    assert [card.id for card in cards] == [2]
    assert api.calls == [("/api/v1/recipes/7/similar", {"limit": 3})]


def test_list_cuisines_returns_names(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(["italian", "итальянская"])
    _use_api(monkeypatch, api)

    assert asyncio.run(tool_module.list_cuisines()) == ["italian", "итальянская"]
    assert api.calls == [("/api/v1/recipes/cuisines", None)]


@pytest.mark.parametrize(
    ("status_code", "detail", "message"),
    [
        (401, "nope", "Authentication failed"),
        (403, "forbidden", "Access denied: forbidden"),
        (404, "missing", "Not found: missing"),
        (503, "warming", "warming up"),
        (500, "boom", "Recipe API error"),
    ],
)
def test_api_errors_are_mapped_to_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    detail: str,
    message: str,
) -> None:
    api = FakeApi(error=RecipeApiError(status_code, detail))
    _use_api(monkeypatch, api)

    with pytest.raises(ToolError, match=message):
        asyncio.run(tool_module.get_recipe(999))


def test_unexpected_payload_raises_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeApi({"detail": "oops"})
    _use_api(monkeypatch, api)

    with pytest.raises(ToolError, match="Unexpected response"):
        asyncio.run(tool_module.search_recipes("soup"))
