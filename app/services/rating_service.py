"""Star-rating service (1–5, one per user per recipe, mutable).

After every upsert/delete the denormalized ``average_rating``, ``ratings_count``,
and ``engagement_score`` on the Recipe row are recomputed atomically in the same
transaction, then the recipe detail cache is invalidated.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import Cache
from app.core.exceptions import NotFoundError
from app.models import Recipe
from app.models.recipe_rating import RecipeRating
from app.models.user import User
from app.services import cache_keys, search_cache, similar_cache


async def _recompute_rating_stats(db: AsyncSession, *, recipe_id: int) -> None:
    """Recount and recompute average_rating and ratings_count for one recipe."""
    result = await db.execute(
        select(func.count(RecipeRating.id), func.coalesce(func.avg(RecipeRating.rating), 0.0))
        .where(RecipeRating.recipe_id == recipe_id)
    )
    count, avg = result.one()
    await db.execute(
        update(Recipe)
        .where(Recipe.id == recipe_id)
        .values(ratings_count=int(count), average_rating=float(avg))
    )


async def recompute_engagement_score(db: AsyncSession, *, recipe_id: int) -> None:
    """Recompute engagement_score = favorites*1 + ratings*2 + comments*3."""
    await db.execute(
        update(Recipe)
        .where(Recipe.id == recipe_id)
        .values(
            engagement_score=(
                Recipe.favorites_count * 1.0
                + Recipe.ratings_count * 2.0
                + Recipe.comments_count * 3.0
            )
        )
    )


async def _bump_caches(cache: Cache | None, *, recipe_id: int) -> None:
    if cache is None:
        return
    await search_cache.bump_search_version(cache)
    await similar_cache.bump_similar_version(cache)
    await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)


async def _load_recipe(db: AsyncSession, *, recipe_id: int) -> Recipe | None:
    result = await db.execute(
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(selectinload(Recipe.owner), selectinload(Recipe.tags))
    )
    return result.scalar_one_or_none()


async def upsert_rating(
    db: AsyncSession,
    *,
    user: User,
    recipe_id: int,
    rating: int,
    cache: Cache | None = None,
) -> Recipe:
    """Create or update the user's rating for a recipe.

    Raises NotFoundError if the recipe does not exist or is not approved.
    """
    recipe = await _load_recipe(db, recipe_id=recipe_id)
    if recipe is None or recipe.status != "approved":
        raise NotFoundError("Recipe not found")

    stmt = (
        pg_insert(RecipeRating)
        .values(user_id=user.id, recipe_id=recipe_id, rating=rating)
        .on_conflict_do_update(
            index_elements=["user_id", "recipe_id"],
            set_={"rating": rating, "updated_at": func.now()},
        )
    )
    await db.execute(stmt)
    await _recompute_rating_stats(db, recipe_id=recipe_id)
    await recompute_engagement_score(db, recipe_id=recipe_id)
    await db.commit()
    await _bump_caches(cache, recipe_id=recipe_id)

    reloaded = await _load_recipe(db, recipe_id=recipe_id)
    return reloaded if reloaded is not None else recipe


async def delete_rating(
    db: AsyncSession,
    *,
    user: User,
    recipe_id: int,
    cache: Cache | None = None,
) -> Recipe:
    """Remove the user's rating. No-op if no rating exists."""
    recipe = await _load_recipe(db, recipe_id=recipe_id)
    if recipe is None:
        raise NotFoundError("Recipe not found")

    await db.execute(
        delete(RecipeRating).where(
            RecipeRating.user_id == user.id,
            RecipeRating.recipe_id == recipe_id,
        )
    )
    await _recompute_rating_stats(db, recipe_id=recipe_id)
    await recompute_engagement_score(db, recipe_id=recipe_id)
    await db.commit()
    await _bump_caches(cache, recipe_id=recipe_id)

    reloaded = await _load_recipe(db, recipe_id=recipe_id)
    return reloaded if reloaded is not None else recipe


async def get_user_rating(
    db: AsyncSession,
    *,
    user_id: int,
    recipe_id: int,
) -> RecipeRating | None:
    """Return the user's rating row, or None if not rated."""
    result = await db.execute(
        select(RecipeRating).where(
            RecipeRating.user_id == user_id,
            RecipeRating.recipe_id == recipe_id,
        )
    )
    return result.scalar_one_or_none()
