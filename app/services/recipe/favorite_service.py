"""Favorite-recipe service: toggle, list, batch-lookup helpers.

Writes refuse non-approved recipes, recompute the denormalised
``recipes.favorites_count`` from the join table (race-safe), and bump
the recipe-related Redis caches so popular sort and detail views stay
consistent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import Cache
from app.core.exceptions import InvalidStateError, NotFoundError
from app.models import Recipe, RecipeFavorite
from app.models.auth.user import User
from app.services.recipe import cache_keys
from app.services.recipe import search_cache
from app.services.recipe import similar_cache
async def _bump_caches(cache: Cache | None, *, recipe_id: int) -> None:
    """Mirror ``recipe_service._bump_recipe_caches`` (local copy avoids cycle)."""
    if cache is None:
        return
    await search_cache.bump_search_version(cache)
    await similar_cache.bump_similar_version(cache)
    await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)


async def _recompute_favorites_count(db: AsyncSession, *, recipe_id: int) -> int:
    """Recount and persist ``recipes.favorites_count`` for one recipe."""
    count_q = select(func.count()).where(RecipeFavorite.recipe_id == recipe_id)
    new_count = (await db.execute(count_q)).scalar_one()
    await db.execute(update(Recipe).where(Recipe.id == recipe_id).values(favorites_count=new_count))
    return int(new_count)


async def _load_recipe_with_relations(db: AsyncSession, *, recipe_id: int) -> Recipe | None:
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(selectinload(Recipe.owner), selectinload(Recipe.tags))
    )
    return result.scalar_one_or_none()


async def add_favorite(
    db: AsyncSession,
    *,
    user: User,
    recipe_id: int,
    cache: Cache | None = None,
) -> Recipe:
    """Idempotently mark ``recipe_id`` as favorited by ``user``.

    Raises ``NotFoundError`` if the recipe doesn't exist, ``InvalidStateError``
    if it's not approved.
    """
    recipe = await _load_recipe_with_relations(db, recipe_id=recipe_id)
    if recipe is None:
        raise NotFoundError("Recipe not found")
    if recipe.status != "approved":
        raise InvalidStateError("Only approved recipes can be favorited")

    stmt = (
        pg_insert(RecipeFavorite)
        .values(user_id=user.id, recipe_id=recipe_id)
        .on_conflict_do_nothing(index_elements=["user_id", "recipe_id"])
    )
    await db.execute(stmt)

    new_count = await _recompute_favorites_count(db, recipe_id=recipe_id)
    from app.services.recipe.rating_service import recompute_engagement_score
    await recompute_engagement_score(db, recipe_id=recipe_id)
    await db.commit()

    await _bump_caches(cache, recipe_id=recipe_id)
    reloaded = await _load_recipe_with_relations(db, recipe_id=recipe_id)
    if reloaded is not None:
        reloaded.favorites_count = new_count
        return reloaded
    recipe.favorites_count = new_count
    return recipe


async def remove_favorite(
    db: AsyncSession,
    *,
    user: User,
    recipe_id: int,
    cache: Cache | None = None,
) -> Recipe:
    """Idempotently remove the favorite link.

    Returns the recipe (with fresh ``favorites_count``). Raises
    ``NotFoundError`` if the recipe doesn't exist; missing favorite row is
    treated as a no-op so double-clicks don't 404.
    """
    recipe = await _load_recipe_with_relations(db, recipe_id=recipe_id)
    if recipe is None:
        raise NotFoundError("Recipe not found")

    await db.execute(
        delete(RecipeFavorite).where(
            RecipeFavorite.user_id == user.id,
            RecipeFavorite.recipe_id == recipe_id,
        )
    )

    new_count = await _recompute_favorites_count(db, recipe_id=recipe_id)
    from app.services.recipe.rating_service import recompute_engagement_score
    await recompute_engagement_score(db, recipe_id=recipe_id)
    await db.commit()

    await _bump_caches(cache, recipe_id=recipe_id)
    reloaded = await _load_recipe_with_relations(db, recipe_id=recipe_id)
    if reloaded is not None:
        reloaded.favorites_count = new_count
        return reloaded
    recipe.favorites_count = new_count
    return recipe


async def get_user_favorites(
    db: AsyncSession,
    *,
    user_id: int,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[Recipe]:
    """Return the user's favorited recipes, most-recent-first.

    Filters to ``status == 'approved'`` so moderator pulls are invisible but
    the join row is preserved (the recipe can reappear after re-approval).
    """
    query = (
        select(Recipe)
        .join(RecipeFavorite, RecipeFavorite.recipe_id == Recipe.id)
        .where(
            RecipeFavorite.user_id == user_id,
            Recipe.status == "approved",
        )
        .options(selectinload(Recipe.owner), selectinload(Recipe.tags))
        .order_by(RecipeFavorite.created_at.desc(), Recipe.id.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_favorited_recipe_ids(
    db: AsyncSession,
    *,
    user_id: int,
    recipe_ids: Iterable[int],
) -> set[int]:
    """Single-query lookup powering ``is_favorited`` enrichment on lists."""
    ids = list(recipe_ids)
    if not ids:
        return set()
    result = await db.execute(
        select(RecipeFavorite.recipe_id).where(
            RecipeFavorite.user_id == user_id,
            RecipeFavorite.recipe_id.in_(ids),
        )
    )
    return {row[0] for row in result.all()}
