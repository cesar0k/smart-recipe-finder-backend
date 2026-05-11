"""Tests for the favorites system."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Dict

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.recipe import Recipe

BASE_RECIPE: dict[str, Any] = {
    "title": "Favoritable Recipe",
    "ingredients": ["a", "b"],
    "instructions": "Mix.",
    "cooking_time_in_minutes": 10,
    "difficulty": "easy",
    "cuisine": "TestCuisine",
}


@pytest.fixture
async def session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with async_sessionmaker(bind=db_engine, expire_on_commit=False)() as s:
        yield s


async def _create_recipe(
    async_client: AsyncClient,
    auth_headers: Dict[str, str],
    *,
    title: str,
) -> Dict[str, Any]:
    payload = {**BASE_RECIPE, "title": title}
    resp = await async_client.post("/api/v1/recipes/", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.crud
@pytest.mark.asyncio
class TestFavorites:
    async def test_favorite_requires_auth(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        recipe = await _create_recipe(async_client, auth_headers, title="No-auth fav")
        resp = await async_client.post(f"/api/v1/favorites/{recipe['id']}")
        assert resp.status_code == 401

    async def test_favorite_unfavorite_toggle(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        recipe = await _create_recipe(async_client, auth_headers, title="Toggle me")

        resp = await async_client.post(f"/api/v1/favorites/{recipe['id']}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_favorited"] is True
        assert body["favorites_count"] == 1

        # Idempotent — second favorite must not double-count
        resp2 = await async_client.post(f"/api/v1/favorites/{recipe['id']}", headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["favorites_count"] == 1

        # The detail endpoint reflects is_favorited + favorites_count for the caller
        detail = await async_client.get(f"/api/v1/recipes/{recipe['id']}", headers=auth_headers)
        assert detail.status_code == 200
        d = detail.json()
        assert d["is_favorited"] is True
        assert d["favorites_count"] == 1

        # Anonymous detail: count visible, is_favorited False
        anon_detail = await async_client.get(f"/api/v1/recipes/{recipe['id']}")
        assert anon_detail.status_code == 200
        ad = anon_detail.json()
        assert ad["is_favorited"] is False
        assert ad["favorites_count"] == 1

        # Unfavorite — count drops, idempotent
        resp3 = await async_client.delete(f"/api/v1/favorites/{recipe['id']}", headers=auth_headers)
        assert resp3.status_code == 200
        assert resp3.json()["is_favorited"] is False
        assert resp3.json()["favorites_count"] == 0

        resp4 = await async_client.delete(f"/api/v1/favorites/{recipe['id']}", headers=auth_headers)
        assert resp4.status_code == 200
        assert resp4.json()["favorites_count"] == 0

    async def test_cannot_favorite_unapproved_recipe(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        session: AsyncSession,
    ) -> None:
        recipe = await _create_recipe(async_client, auth_headers, title="Pending one")
        # Force the recipe into "pending" status directly (admin-created recipes
        # land as approved by default).
        await session.execute(
            update(Recipe).where(Recipe.id == recipe["id"]).values(status="pending")
        )
        await session.commit()

        resp = await async_client.post(f"/api/v1/favorites/{recipe['id']}", headers=auth_headers)
        assert resp.status_code == 400

    async def test_my_favorites_list_recency_and_pagination(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        a = await _create_recipe(async_client, auth_headers, title="Fav A")
        b = await _create_recipe(async_client, auth_headers, title="Fav B")
        c = await _create_recipe(async_client, auth_headers, title="Fav C")

        # Order matters: A first, then B, then C — list should return newest-first.
        for r in (a, b, c):
            resp = await async_client.post(f"/api/v1/favorites/{r['id']}", headers=auth_headers)
            assert resp.status_code == 200

        listing = await async_client.get("/api/v1/favorites/", headers=auth_headers)
        assert listing.status_code == 200
        rows = listing.json()
        ids = [r["id"] for r in rows if r["title"].startswith("Fav ")]
        # Most-recent favorite first
        assert ids[:3] == [c["id"], b["id"], a["id"]]
        # Every row carries is_favorited=True
        assert all(r["is_favorited"] is True for r in rows[:3])

        # Pagination — limit=2 must return 2 newest
        page = await async_client.get("/api/v1/favorites/?limit=2", headers=auth_headers)
        assert page.status_code == 200
        page_ids = [r["id"] for r in page.json()]
        assert page_ids[:2] == [c["id"], b["id"]]

    async def test_favorite_disappears_when_recipe_unapproved(
        self,
        async_client: AsyncClient,
        auth_headers: Dict[str, str],
        session: AsyncSession,
    ) -> None:
        recipe = await _create_recipe(async_client, auth_headers, title="Visible")
        resp = await async_client.post(f"/api/v1/favorites/{recipe['id']}", headers=auth_headers)
        assert resp.status_code == 200

        # Pull it out of approved → must vanish from the user's list.
        await session.execute(
            update(Recipe).where(Recipe.id == recipe["id"]).values(status="pending")
        )
        await session.commit()

        listing = await async_client.get("/api/v1/favorites/", headers=auth_headers)
        assert listing.status_code == 200
        ids = [r["id"] for r in listing.json()]
        assert recipe["id"] not in ids

        # Re-approve — recipe reappears (join row was preserved).
        await session.execute(
            update(Recipe).where(Recipe.id == recipe["id"]).values(status="approved")
        )
        await session.commit()

        listing2 = await async_client.get("/api/v1/favorites/", headers=auth_headers)
        assert listing2.status_code == 200
        ids2 = [r["id"] for r in listing2.json()]
        assert recipe["id"] in ids2

    async def test_check_favorites_returns_only_favorited_subset(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        a = await _create_recipe(async_client, auth_headers, title="Check A")
        b = await _create_recipe(async_client, auth_headers, title="Check B")
        c = await _create_recipe(async_client, auth_headers, title="Check C")

        # Favorite only A and C.
        for r in (a, c):
            resp = await async_client.post(f"/api/v1/favorites/{r['id']}", headers=auth_headers)
            assert resp.status_code == 200

        ids = f"{a['id']},{b['id']},{c['id']}"
        resp = await async_client.get(f"/api/v1/favorites/check?ids={ids}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["favorited_ids"]) == sorted([a["id"], c["id"]])

    async def test_check_favorites_requires_auth(self, async_client: AsyncClient) -> None:
        resp = await async_client.get("/api/v1/favorites/check?ids=1,2,3")
        assert resp.status_code == 401

    async def test_check_favorites_validates_input(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        # Non-integer ids → 400 (not a 422 — we parse manually for nicer UX).
        resp = await async_client.get("/api/v1/favorites/check?ids=1,abc,3", headers=auth_headers)
        assert resp.status_code == 400

        # Empty list → 200 with empty result (no IDs to look up).
        resp_empty = await async_client.get("/api/v1/favorites/check?ids=", headers=auth_headers)
        assert resp_empty.status_code == 200
        assert resp_empty.json() == {"favorited_ids": []}

        # Too many ids → 400.
        too_many = ",".join(str(i) for i in range(1, settings.FAVORITES_CHECK_MAX_IDS + 50))
        resp_big = await async_client.get(
            f"/api/v1/favorites/check?ids={too_many}", headers=auth_headers
        )
        assert resp_big.status_code == 400

    async def test_sort_popular_orders_by_favorites_count(
        self, async_client: AsyncClient, auth_headers: Dict[str, str]
    ) -> None:
        a = await _create_recipe(async_client, auth_headers, title="Pop A")
        b = await _create_recipe(async_client, auth_headers, title="Pop B")
        c = await _create_recipe(async_client, auth_headers, title="Pop C")

        # B gets a favorite, A and C don't.
        resp = await async_client.post(f"/api/v1/favorites/{b['id']}", headers=auth_headers)
        assert resp.status_code == 200

        listing = await async_client.get("/api/v1/recipes/?sort=popular")
        assert listing.status_code == 200
        rows = listing.json()
        relevant = [r for r in rows if r["title"].startswith("Pop ")]
        # B is most popular; A and C tie at 0 (id DESC tiebreaker → C before A)
        assert relevant[0]["id"] == b["id"]
        zero_count_ids = [r["id"] for r in relevant if r["favorites_count"] == 0]
        assert zero_count_ids == [c["id"], a["id"]]
