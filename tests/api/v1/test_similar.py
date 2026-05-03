from typing import Any, Dict, cast

import pytest
from httpx import AsyncClient
from pytest import MonkeyPatch


@pytest.fixture(autouse=True)
def _stub_embedding_ready(monkeypatch: MonkeyPatch) -> None:
    # ASGITransport does not run FastAPI lifespan, so the model isn't preloaded.
    # The first upsert_recipe call during POST /recipes/ will load it lazily;
    # we just need the readiness guard to pass.
    monkeypatch.setattr(
        "app.api.v1.endpoints.recipes.is_embedding_model_ready", lambda: True
    )


@pytest.mark.crud
@pytest.mark.asyncio
class TestSimilarRecipes:
    PASTA_1: dict[str, Any] = {
        "title": "Spaghetti Carbonara",
        "ingredients": ["spaghetti", "eggs", "bacon", "parmesan"],
        "instructions": "Boil pasta, mix with eggs and bacon, top with parmesan.",
        "cooking_time_in_minutes": 25,
        "difficulty": "medium",
        "cuisine": "Italian",
    }
    PASTA_2: dict[str, Any] = {
        "title": "Penne Arrabbiata",
        "ingredients": ["penne", "tomato", "garlic", "chili", "olive oil"],
        "instructions": "Boil pasta, mix with spicy tomato sauce.",
        "cooking_time_in_minutes": 20,
        "difficulty": "easy",
        "cuisine": "Italian",
    }
    DESSERT: dict[str, Any] = {
        "title": "Chocolate Brownies",
        "ingredients": ["cocoa", "sugar", "butter", "eggs", "flour"],
        "instructions": "Mix ingredients, bake at 180C for 25 minutes.",
        "cooking_time_in_minutes": 45,
        "difficulty": "easy",
        "cuisine": "American",
    }

    async def _create(
        self,
        client: AsyncClient,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.post(
            "/api/v1/recipes/", json=payload, headers=headers
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    async def test_similar_excludes_self_and_returns_approved(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
    ) -> None:
        pasta_1 = await self._create(async_client, auth_headers, self.PASTA_1)
        await self._create(async_client, auth_headers, self.PASTA_2)
        await self._create(async_client, auth_headers, self.DESSERT)

        response = await async_client.get(
            f"/api/v1/recipes/{pasta_1['id']}/similar"
        )
        assert response.status_code == 200
        data = response.json()

        ids = [r["id"] for r in data]
        assert pasta_1["id"] not in ids  # self excluded
        assert all(r["status"] == "approved" for r in data)

    async def test_similar_strict_threshold_returns_empty(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
    ) -> None:
        pasta_1 = await self._create(async_client, auth_headers, self.PASTA_1)
        await self._create(async_client, auth_headers, self.PASTA_2)
        await self._create(async_client, auth_headers, self.DESSERT)

        response = await async_client.get(
            f"/api/v1/recipes/{pasta_1['id']}/similar",
            params={"threshold": 0.0},
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_similar_unknown_recipe_returns_empty(
        self,
        async_client: AsyncClient,
    ) -> None:
        response = await async_client.get("/api/v1/recipes/99999/similar")
        assert response.status_code == 200
        assert response.json() == []

    async def test_similar_respects_limit(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
    ) -> None:
        pasta_1 = await self._create(async_client, auth_headers, self.PASTA_1)
        await self._create(async_client, auth_headers, self.PASTA_2)
        await self._create(async_client, auth_headers, self.DESSERT)

        response = await async_client.get(
            f"/api/v1/recipes/{pasta_1['id']}/similar",
            params={"limit": 1, "threshold": 2.0},
        )
        assert response.status_code == 200
        assert len(response.json()) <= 1
