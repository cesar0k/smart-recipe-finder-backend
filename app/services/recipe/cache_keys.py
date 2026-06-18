from __future__ import annotations

from app.core.cache import Cache

TTL_RECIPE_DETAIL = 3600
TTL_CUISINES = 3600
TTL_CATEGORIES = 3600
TTL_USER_PROFILE = 1800
TTL_PENDING_COUNT = 30


def recipe_detail(recipe_id: int) -> str:
    return f"recipe:{recipe_id}:public"


def cuisines() -> str:
    return "recipes:cuisines"


def categories(limit_per: int) -> str:
    return f"recipes:categories:{limit_per}"


def user_profile(user_id: int) -> str:
    return f"user:profile:{user_id}"


def pending_count() -> str:
    return "moderation:pending_count"


async def invalidate_on_recipe_change(cache: Cache, recipe_id: int | None = None) -> None:
    # Invalidate categories with any limit_per (wildcard via scan would be ideal,
    # but we invalidate the common values used by the frontend instead)
    keys = [cuisines(), pending_count(), categories(4), categories(6), categories(8)]
    if recipe_id is not None:
        keys.append(recipe_detail(recipe_id))
    await cache.delete(*keys)


async def invalidate_on_user_change(cache: Cache, user_id: int) -> None:
    await cache.delete(user_profile(user_id))


async def invalidate_on_moderation(cache: Cache, recipe_id: int | None = None) -> None:
    keys = [pending_count(), cuisines(), categories(4), categories(6), categories(8)]
    if recipe_id is not None:
        keys.append(recipe_detail(recipe_id))
    await cache.delete(*keys)
