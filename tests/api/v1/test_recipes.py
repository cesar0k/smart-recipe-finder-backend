from typing import Any, Dict, cast

import pytest
from httpx import AsyncClient


@pytest.mark.crud
@pytest.mark.asyncio
class TestRecipeOperations:
    BASE_RECIPE_DATA: dict[str, Any] = {
        "title": "Standard Recipe",
        "ingredients": ["ingredient A", "ingredient B"],
        "instructions": "Mix and cook.",
        "cooking_time_in_minutes": 30,
        "difficulty": "medium",
        "cuisine": "SomeCuisine",
    }

    @pytest.fixture
    async def existing_recipe(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> Dict[str, Any]:
        response = await async_client.post(
            "/api/v1/recipes/", json=self.BASE_RECIPE_DATA, headers=auth_headers
        )
        assert response.status_code == 201
        return cast(Dict[str, Any], response.json())

    @pytest.mark.smoke
    async def test_create_recipe(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        new_recipe = self.BASE_RECIPE_DATA.copy()
        new_recipe["title"] = "New Created Recipe"

        response = await async_client.post(
            "/api/v1/recipes/", json=new_recipe, headers=auth_headers
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == new_recipe["title"]
        assert data["id"] is not None

    @pytest.mark.smoke
    async def test_get_recipes_list(
        self, async_client: AsyncClient, existing_recipe: Dict[str, Any]
    ) -> None:
        response = await async_client.get("/api/v1/recipes/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        ids = [r["id"] for r in data]
        assert existing_recipe["id"] in ids

    @pytest.mark.smoke
    async def test_get_recipe_by_id(
        self, async_client: AsyncClient, existing_recipe: Dict[str, Any]
    ) -> None:
        recipe_id = existing_recipe["id"]
        response = await async_client.get(f"/api/v1/recipes/{recipe_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == existing_recipe["title"]

    async def test_get_recipe_not_found(
        self, async_client: AsyncClient, existing_recipe: Dict[str, Any]
    ) -> None:
        recipe_id = existing_recipe["id"]
        response = await async_client.get(f"/api/v1/recipes/{recipe_id + 1}")
        assert response.status_code == 404

    async def test_update_recipe_partial(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        existing_recipe: Dict[str, Any],
    ) -> None:
        recipe_id = existing_recipe["id"]
        update_payload = {"title": "Updated Title", "difficulty": "hard"}

        response = await async_client.patch(
            f"/api/v1/recipes/{recipe_id}",
            json=update_payload,
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()

        assert data["title"] == update_payload["title"]
        assert data["difficulty"] == update_payload["difficulty"]
        assert data["cuisine"] == existing_recipe["cuisine"]

    async def test_update_recipe_ingredients(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        existing_recipe: Dict[str, Any],
    ) -> None:
        recipe_id = existing_recipe["id"]
        new_ingredients = ["new_ing1", "new_ing2"]

        response = await async_client.patch(
            f"/api/v1/recipes/{recipe_id}",
            json={"ingredients": new_ingredients},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()

        actual_ingredients = set(ing["name"] for ing in data["ingredients"])
        assert actual_ingredients == set(new_ingredients)

    async def test_update_recipe_drops_one_of_existing_images(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        existing_recipe: Dict[str, Any],
    ) -> None:
        """Regression: removing an image via PATCH used to raise
        InvalidRequestError ("Instance has been deleted") because
        _update_recipe_directly called db.add(db_recipe) after
        _set_recipe_images had already marked one RecipeImage row as
        deleted. The cascade walked back into the deleted row.
        """
        recipe_id = existing_recipe["id"]
        seed_urls = [
            "https://example.com/a.jpg",
            "https://example.com/b.jpg",
        ]

        # Seed: attach two images via PATCH. Goes through the same
        # _set_recipe_images path that the bug lives in, but with only
        # additions — no deletes, no cascade interaction.
        seed_resp = await async_client.patch(
            f"/api/v1/recipes/{recipe_id}",
            json={"image_urls": seed_urls},
            headers=auth_headers,
        )
        assert seed_resp.status_code == 200
        assert set(seed_resp.json()["image_urls"]) == set(seed_urls)

        # Trigger: drop one of the two. _set_recipe_images now calls
        # db.delete(img) on the dropped row, after which the old
        # `db.add(db_recipe)` cascade exploded.
        drop_resp = await async_client.patch(
            f"/api/v1/recipes/{recipe_id}",
            json={"image_urls": [seed_urls[0]]},
            headers=auth_headers,
        )
        assert drop_resp.status_code == 200
        assert drop_resp.json()["image_urls"] == [seed_urls[0]]

    async def test_update_recipe_not_found(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        existing_recipe: Dict[str, Any],
    ) -> None:
        recipe_id = existing_recipe["id"]
        update_payload = {"title": "Ghost Recipe"}
        response = await async_client.patch(
            f"/api/v1/recipes/{recipe_id + 1}",
            json=update_payload,
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_delete_recipe(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        existing_recipe: Dict[str, Any],
    ) -> None:
        recipe_id = existing_recipe["id"]
        response = await async_client.delete(f"/api/v1/recipes/{recipe_id}", headers=auth_headers)
        assert response.status_code == 200

        get_response = await async_client.get(f"/api/v1/recipes/{recipe_id}")
        assert get_response.status_code == 404

    async def test_delete_recipe_not_found(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        existing_recipe: Dict[str, Any],
    ) -> None:
        recipe_id = existing_recipe["id"]
        response = await async_client.delete(
            f"/api/v1/recipes/{recipe_id + 1}", headers=auth_headers
        )
        assert response.status_code == 404
