from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.vector_store import vector_store
from app.models.recipe import Recipe
from app.models.recipe_draft import RecipeDraft


async def get_pending_recipes(db: AsyncSession) -> Sequence[Recipe]:
    query = select(Recipe).where(Recipe.status == "pending").order_by(Recipe.id.desc())
    result = await db.execute(query)
    return result.scalars().all()


async def get_pending_drafts(db: AsyncSession) -> Sequence[RecipeDraft]:
    query = (
        select(RecipeDraft)
        .where(RecipeDraft.status == "pending")
        .order_by(RecipeDraft.id.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


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
    recipe: Recipe,
    action: str,
    rejection_reason: str | None = None,
) -> Recipe:
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
    await db.commit()
    await db.refresh(recipe)
    return recipe


async def moderate_draft(
    db: AsyncSession,
    *,
    draft: RecipeDraft,
    action: str,
    rejection_reason: str | None = None,
) -> RecipeDraft:
    """Approve or reject a draft."""
    if action == "approve":
        result = await db.execute(
            select(Recipe).where(Recipe.id == draft.recipe_id)
        )
        recipe = result.scalar_one_or_none()

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

        await db.delete(draft)
        await db.commit()

        draft.status = "approved"
        return draft

    elif action == "reject":
        draft.status = "rejected"
        draft.rejection_reason = rejection_reason
        db.add(draft)
        await db.commit()
        await db.refresh(draft)
        return draft

    return draft
