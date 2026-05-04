from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app import schemas
from app.core.cache import Cache
from app.core.exceptions import (
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from app.core.vector_store import vector_store
from app.models.recipe import Recipe
from app.models.recipe_draft import RecipeDraft
from app.services import (
    cache_keys,
    moderation_log_service,
    notification_service,
    search_cache,
    similar_cache,
)


async def get_pending_recipes(db: AsyncSession) -> Sequence[Recipe]:
    query = (
        select(Recipe)
        .where(Recipe.status == "pending")
        .options(selectinload(Recipe.owner))
        .order_by(Recipe.id.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_pending_drafts(db: AsyncSession) -> Sequence[RecipeDraft]:
    query = (
        select(RecipeDraft).where(RecipeDraft.status == "pending").order_by(RecipeDraft.id.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_pending_counts(db: AsyncSession) -> dict[str, int]:
    """Return counts of pending recipes and drafts."""
    recipe_q = select(sa_func.count(Recipe.id)).where(Recipe.status == "pending")
    draft_q = select(sa_func.count(RecipeDraft.id)).where(RecipeDraft.status == "pending")

    recipe_result = await db.execute(recipe_q)
    draft_result = await db.execute(draft_q)

    return {
        "recipes": recipe_result.scalar_one(),
        "drafts": draft_result.scalar_one(),
    }


async def get_pending_count_cached(
    db: AsyncSession, cache: Cache | None = None
) -> schemas.PendingCountResponse:
    """Read-through cache wrapper around get_pending_counts."""
    key = cache_keys.pending_count()
    if cache is not None:
        cached = await cache.get_model(key, schemas.PendingCountResponse)
        if cached is not None:
            return cached

    counts = await get_pending_counts(db)
    response = schemas.PendingCountResponse(**counts)
    if cache is not None:
        await cache.set_model(key, response, ttl=cache_keys.TTL_PENDING_COUNT)
    return response


def _validate_action(action: str, rejection_reason: str | None) -> None:
    """Domain validation shared by moderate_recipe and moderate_draft."""
    if action == "reject" and not rejection_reason:
        raise ValidationError("Rejection reason is required")


async def _bump_moderation_caches(cache: Cache | None, *, recipe_id: int) -> None:
    if cache is None:
        return
    await search_cache.bump_search_version(cache)
    await similar_cache.bump_similar_version(cache)
    await cache_keys.invalidate_on_moderation(cache, recipe_id=recipe_id)


def _create_semantic_document(recipe: Recipe) -> tuple[str, dict[str, Any]]:
    t = recipe.cooking_time_in_minutes
    time_description = "Standard cooking time"
    if t <= 15:
        time_description = "Very quick, instant meal"
    elif t <= 30:
        time_description = "Quick, standard meal"
    elif t > 120:
        time_description = "Slow cooked, long preparation"

    ingredients_str = ""
    ingredients = cast(Any, recipe.ingredients)
    if ingredients:
        names = [item.get("name", "") for item in ingredients]
        ingredients_str = ", ".join(names)

    doc = (
        f"Title: {recipe.title}. "
        f"Ingredients: {ingredients_str}. "
        f"Instructions: {recipe.instructions}. "
        f"Cooking time: {t} minutes ({time_description}). "
        f"Difficulty: {recipe.difficulty}. "
        f"Cuisine: {recipe.cuisine}."
    )

    metadata = {
        "title": recipe.title,
        "cooking_time": recipe.cooking_time_in_minutes,
        "difficulty": recipe.difficulty,
        "cuisine": recipe.cuisine or "",
    }

    return doc, metadata


async def moderate_recipe(
    db: AsyncSession,
    *,
    recipe_id: int,
    action: str,
    moderator_id: int,
    rejection_reason: str | None = None,
    cache: Cache | None = None,
) -> Recipe:
    """Approve or reject a pending recipe.

    Raises NotFoundError, InvalidStateError, ValidationError on bad input.
    """
    from app.services.recipe_service import get_recipe_by_id

    _validate_action(action, rejection_reason)

    recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if recipe is None:
        raise NotFoundError("Recipe not found")
    if recipe.status != "pending":
        raise InvalidStateError(f"Recipe is already '{recipe.status}', not pending")

    if action == "approve":
        recipe.status = "approved"
        recipe.rejection_reason = None

        text, meta = _create_semantic_document(recipe)
        await vector_store.upsert_recipe(
            recipe_id=recipe.id,
            title=recipe.title,
            full_text=text,
            metadata=meta,
        )
    elif action == "reject":
        recipe.status = "rejected"
        recipe.rejection_reason = rejection_reason

        try:
            await vector_store.delete_recipe(recipe.id)
        except Exception:
            pass

    db.add(recipe)

    # Audit log (denormalized names for display)
    from app.models.user import User as _User

    mod_result = await db.execute(select(_User.username).where(_User.id == moderator_id))
    mod_username = mod_result.scalar_one_or_none() or ""

    await moderation_log_service.create_log(
        db,
        moderator_id=moderator_id,
        recipe_id=recipe.id,
        action=action,
        reason=rejection_reason,
        recipe_title=recipe.title,
        moderator_username=mod_username,
    )

    # Notify recipe owner (keys, not hardcoded text — frontend translates)
    _action_past = {"approve": "approved", "reject": "rejected"}
    if recipe.owner_id is not None:
        notif_type = f"recipe_{_action_past[action]}"
        await notification_service.notify_and_broadcast(
            db,
            user_id=recipe.owner_id,
            type=notif_type,
            title=recipe.title,
            message=rejection_reason or "",
            recipe_id=recipe.id,
        )

    await db.commit()
    await db.refresh(recipe)

    await _bump_moderation_caches(cache, recipe_id=recipe.id)
    return recipe


async def moderate_draft(
    db: AsyncSession,
    *,
    draft_id: int,
    action: str,
    moderator_id: int,
    rejection_reason: str | None = None,
    cache: Cache | None = None,
) -> RecipeDraft:
    """Approve or reject a draft.

    Raises NotFoundError, InvalidStateError, ValidationError on bad input.
    """
    _validate_action(action, rejection_reason)

    draft_result = await db.execute(select(RecipeDraft).where(RecipeDraft.id == draft_id))
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise NotFoundError("Draft not found")
    if draft.status != "pending":
        raise InvalidStateError(f"Draft is already '{draft.status}', not pending")

    if action == "approve":
        recipe_result = await db.execute(select(Recipe).where(Recipe.id == draft.recipe_id))
        recipe = recipe_result.scalar_one_or_none()

        if recipe is None:
            draft.status = "rejected"
            draft.rejection_reason = "Original recipe no longer exists"
            db.add(draft)
            await db.commit()
            await db.refresh(draft)
            return draft

        recipe.title = draft.title
        recipe.instructions = draft.instructions
        recipe.cooking_time_in_minutes = draft.cooking_time_in_minutes
        recipe.difficulty = draft.difficulty
        recipe.cuisine = draft.cuisine
        recipe.ingredients = draft.ingredients
        db.add(recipe)

        text, meta = _create_semantic_document(recipe)
        await vector_store.upsert_recipe(
            recipe_id=recipe.id,
            title=recipe.title,
            full_text=text,
            metadata=meta,
        )

        draft.status = "approved"
        db.add(draft)

    elif action == "reject":
        draft.status = "rejected"
        draft.rejection_reason = rejection_reason
        db.add(draft)

    # Audit log (denormalized names for display)
    from app.models.user import User as _User

    mod_result = await db.execute(select(_User.username).where(_User.id == moderator_id))
    mod_username = mod_result.scalar_one_or_none() or ""

    await moderation_log_service.create_log(
        db,
        moderator_id=moderator_id,
        draft_id=draft.id,
        recipe_id=draft.recipe_id,
        action=action,
        reason=rejection_reason,
        recipe_title=draft.title,
        moderator_username=mod_username,
    )

    # Notify draft author (keys, not hardcoded text — frontend translates)
    _action_past_d = {"approve": "approved", "reject": "rejected"}
    notif_type = f"draft_{_action_past_d[action]}"
    await notification_service.notify_and_broadcast(
        db,
        user_id=draft.author_id,
        type=notif_type,
        title=draft.title,
        message=rejection_reason or "",
        recipe_id=draft.recipe_id,
    )

    await db.commit()
    await db.refresh(draft)

    await _bump_moderation_caches(cache, recipe_id=draft.recipe_id)
    return draft
