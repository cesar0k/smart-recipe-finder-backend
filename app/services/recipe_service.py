import re
from collections.abc import Sequence
from typing import Any
from typing import cast as t_cast

from sqlalchemy import String, distinct, func, not_, or_
from sqlalchemy import cast as sa_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.selectable import Select

from app.core.s3_client import s3_client
from app.core.text_utils import get_word_forms
from app.core.vector_store import vector_store
from app.models import Recipe
from app.models.recipe_draft import RecipeDraft
from app.models.user import User
from app.schemas import RecipeCreate, RecipeUpdate

__all__ = [
    "create_recipe",
    "get_all_recipes",
    "get_user_recipes",
    "get_recipe_by_id",
    "update_recipe",
    "delete_recipe",
    "search_recipes_by_vector",
    "vector_store",
]


def _with_owner(query: Select[tuple[Recipe]]) -> Select[tuple[Recipe]]:
    """Add selectinload for owner relationship."""
    return query.options(selectinload(Recipe.owner))


def _create_semantic_document(recipe: Recipe) -> tuple[str, dict[str, Any]]:
    time_description = "Standard cooking time"
    t = recipe.cooking_time_in_minutes
    if t <= 15:
        time_description = "Very quick, instant meal"
    elif t <= 30:
        time_description = "Quick, standard meal"
    elif t > 120:
        time_description = "Slow cooked, long preparation"

    ingredients_str = ""
    ingredients = t_cast(Any, recipe.ingredients)
    if ingredients:
        names = [item.get("name", "") for item in ingredients]
        ingredients_str = ", ".join(names)

    description_str = f"Description: {recipe.description}. " if recipe.description else ""

    doc_to_embed = (
        f"Title: {recipe.title}. "
        f"{description_str}"
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

    return doc_to_embed, metadata


def _apply_filters(
    query: Select[tuple[Recipe]],
    include_str: str | None = None,
    exclude_str: str | None = None,
    min_time: int | None = None,
    max_time: int | None = None,
    difficulty: str | None = None,
    cuisine: str | None = None,
) -> Select[tuple[Recipe]]:
    """
    Apply all filters: ingredients, cooking time, difficulty, cuisine.
    """
    json_as_text = sa_cast(Recipe.ingredients, String)

    # Include ingredients
    if include_str:
        raw_items = [i.strip() for i in include_str.split(",") if i.strip()]
        for item in raw_items:
            terms = get_word_forms(item)

            term_conditions = []
            for term in terms:
                safe_term = re.escape(term)
                pattern = f"\\y{safe_term}\\y"
                term_conditions.append(json_as_text.op("~*")(pattern))

            query = query.where(or_(*term_conditions))

    # Exclude ingredients
    if exclude_str:
        raw_items = [i.strip() for i in exclude_str.split(",") if i.strip()]
        exclude_conditions = []
        for item in raw_items:
            terms = get_word_forms(item)
            for term in terms:
                safe_term = re.escape(term)
                pattern = f"\\y{safe_term}\\y"
                exclude_conditions.append(json_as_text.op("~*")(pattern))

        if exclude_conditions:
            query = query.where(not_(or_(*exclude_conditions)))

    # Cooking time range
    if min_time is not None:
        query = query.where(Recipe.cooking_time_in_minutes >= min_time)
    if max_time is not None:
        query = query.where(Recipe.cooking_time_in_minutes <= max_time)

    # Difficulty multi-select (comma-separated, case-insensitive)
    if difficulty:
        difficulties = [d.strip().lower() for d in difficulty.split(",") if d.strip()]
        if difficulties:
            query = query.where(func.lower(Recipe.difficulty).in_(difficulties))

    # Cuisine multi-select (comma-separated, case-insensitive)
    if cuisine:
        cuisines = [c.strip().lower() for c in cuisine.split(",") if c.strip()]
        if cuisines:
            query = query.where(func.lower(Recipe.cuisine).in_(cuisines))

    return query


async def get_distinct_cuisines(db: AsyncSession) -> list[str]:
    """Return sorted list of distinct cuisine values from approved recipes."""
    result = await db.execute(
        select(distinct(Recipe.cuisine))
        .where(Recipe.status == "approved")
        .where(Recipe.cuisine.isnot(None))
        .where(Recipe.cuisine != "")
        .order_by(Recipe.cuisine)
    )
    return [row[0] for row in result.all()]


async def create_recipe(
    db: AsyncSession,
    *,
    recipe_in: RecipeCreate,
    current_user: User,
) -> Recipe:
    recipe_data = recipe_in.model_dump(exclude={"ingredients"})
    json_ingredients = [{"name": name} for name in recipe_in.ingredients]

    # Moderators and admins get auto-approved, regular users go to pending
    status = "approved" if current_user.role in ("moderator", "admin") else "pending"

    db_recipe = Recipe(
        **recipe_data,
        ingredients=json_ingredients,
        owner_id=current_user.id,
        status=status,
    )

    db.add(db_recipe)
    await db.commit()
    await db.refresh(db_recipe)

    if db_recipe.status == "approved":
        text, meta = _create_semantic_document(db_recipe)
        await vector_store.upsert_recipe(
            recipe_id=db_recipe.id,
            title=db_recipe.title,
            full_text=text,
            metadata=meta,
        )

    # Notify moderators/admins about new pending recipe
    if db_recipe.status == "pending":
        from app.services import notification_service

        mod_query = select(User.id).where(
            User.role.in_(["moderator", "admin"]),
            User.is_active == True,  # noqa: E712
        )
        mod_result = await db.execute(mod_query)
        mod_ids = [row[0] for row in mod_result.all()]

        if mod_ids:
            await notification_service.notify_bulk_and_broadcast(
                db,
                user_ids=mod_ids,
                type="new_pending_recipe",
                title=db_recipe.title,
                message="",
                recipe_id=db_recipe.id,
            )
            await db.commit()

    return db_recipe


async def get_all_recipes(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
    include_str: str | None = None,
    exclude_str: str | None = None,
    min_time: int | None = None,
    max_time: int | None = None,
    difficulty: str | None = None,
    cuisine: str | None = None,
) -> Sequence[Recipe]:
    """Public feed — only approved recipes. Always."""
    query = _with_owner(select(Recipe).where(Recipe.status == "approved"))
    query = _apply_filters(
        query, include_str, exclude_str,
        min_time=min_time, max_time=max_time,
        difficulty=difficulty, cuisine=cuisine,
    )
    query = query.order_by(Recipe.id.desc())
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def get_user_recipes(
    db: AsyncSession,
    *,
    user_id: int,
    skip: int = 0,
    limit: int = 100,
    include_pending_drafts: bool = False,
    approved_only: bool = False,
) -> Sequence[Recipe]:
    """User's recipes. If approved_only=True, only return approved (for public view)."""
    base = select(Recipe).where(Recipe.owner_id == user_id)
    if approved_only:
        base = base.where(Recipe.status == "approved")
    query = _with_owner(
        base.order_by(Recipe.id.desc()).offset(skip).limit(limit)
    )
    result = await db.execute(query)
    recipes = result.scalars().all()

    # Optionally mark which recipes have pending drafts
    if include_pending_drafts and recipes:
        recipe_ids = [r.id for r in recipes]
        draft_result = await db.execute(
            select(RecipeDraft.recipe_id)
            .where(
                RecipeDraft.recipe_id.in_(recipe_ids),
                RecipeDraft.status == "pending",
            )
            .distinct()
        )
        draft_recipe_ids = {row[0] for row in draft_result.all()}
        for r in recipes:
            r.has_pending_draft = r.id in draft_recipe_ids

    return recipes


async def get_recipe_by_id(db: AsyncSession, *, recipe_id: int) -> Recipe | None:
    query = _with_owner(select(Recipe).where(Recipe.id == recipe_id))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def update_recipe(
    db: AsyncSession,
    *,
    db_recipe: Recipe,
    recipe_in: RecipeUpdate,
    current_user: User,
) -> Recipe | RecipeDraft:
    """Update a recipe.

    - Moderator/admin: update directly.
    - Regular user (owner): create a draft instead.
    """
    if current_user.role in ("moderator", "admin"):
        return await _update_recipe_directly(db, db_recipe=db_recipe, recipe_in=recipe_in)
    else:
        return await _create_draft(
            db, db_recipe=db_recipe, recipe_in=recipe_in, author=current_user
        )


async def _update_recipe_directly(
    db: AsyncSession,
    *,
    db_recipe: Recipe,
    recipe_in: RecipeUpdate,
) -> Recipe:
    """Apply update directly to recipe (for moderator/admin)."""
    update_data = recipe_in.model_dump(exclude_unset=True)

    if "image_urls" in update_data:
        raw_urls = update_data.pop("image_urls")

        if raw_urls is None:
            new_urls_list: list[str] = []
        else:
            new_urls_list = [str(url) for url in raw_urls]

        current_urls = set(db_recipe.image_urls) if db_recipe.image_urls else set()
        new_urls = set(new_urls_list)
        urls_to_delete = current_urls - new_urls

        db_recipe.image_urls = new_urls_list

        # Sync thumbnail_urls: keep only thumbs for remaining images, in same order
        current_thumbs = list(db_recipe.thumbnail_urls) if db_recipe.thumbnail_urls else []
        old_url_list = list(db_recipe.image_urls) if db_recipe.image_urls else []
        # Build a mapping from full URL → thumb URL using position
        url_to_thumb = {}
        for i, u in enumerate(old_url_list):
            if i < len(current_thumbs):
                url_to_thumb[u] = current_thumbs[i]
        # Reorder thumbnails to match new image_urls order
        new_thumbs = [url_to_thumb[u] for u in new_urls_list if u in url_to_thumb]
        db_recipe.thumbnail_urls = new_thumbs

        for url in urls_to_delete:
            await s3_client.delete_image_from_s3(url)
            await s3_client.delete_image_from_s3(_derive_thumb_url(url))

    if "ingredients" in update_data:
        raw_ingredients = update_data.pop("ingredients")
        json_ingredients = [{"name": i} for i in raw_ingredients]
        db_recipe.ingredients = json_ingredients

    for field, value in update_data.items():
        setattr(db_recipe, field, value)

    db.add(db_recipe)
    await db.commit()
    await db.refresh(db_recipe)

    # Re-index if approved
    if db_recipe.status == "approved":
        text, meta = _create_semantic_document(db_recipe)
        await vector_store.upsert_recipe(
            recipe_id=db_recipe.id,
            title=db_recipe.title,
            full_text=text,
            metadata=meta,
        )

    return db_recipe


async def _create_draft(
    db: AsyncSession,
    *,
    db_recipe: Recipe,
    recipe_in: RecipeUpdate,
    author: User,
) -> RecipeDraft:
    """Create a draft with proposed changes (for regular users)."""
    # Start with current recipe data
    draft_data: dict[str, Any] = {
        "title": db_recipe.title,
        "description": db_recipe.description,
        "instructions": db_recipe.instructions,
        "cooking_time_in_minutes": db_recipe.cooking_time_in_minutes,
        "difficulty": db_recipe.difficulty,
        "cuisine": db_recipe.cuisine,
        "ingredients": db_recipe.ingredients,
    }

    # Apply proposed changes on top
    update_data = recipe_in.model_dump(exclude_unset=True)

    # Exclude image_urls from draft (images are managed separately)
    update_data.pop("image_urls", None)

    if "ingredients" in update_data:
        raw_ingredients = update_data.pop("ingredients")
        draft_data["ingredients"] = [{"name": i} for i in raw_ingredients]

    for field, value in update_data.items():
        if field in draft_data:
            draft_data[field] = value

    draft = RecipeDraft(
        recipe_id=db_recipe.id,
        author_id=author.id,
        status="pending",
        **draft_data,
    )

    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def resubmit_recipe(
    db: AsyncSession,
    *,
    db_recipe: Recipe,
    recipe_in: RecipeUpdate,
) -> Recipe:
    """Re-submit a rejected recipe with corrections. Sets status back to pending."""
    update_data = recipe_in.model_dump(exclude_unset=True)

    # Exclude image_urls — managed separately
    update_data.pop("image_urls", None)

    if "ingredients" in update_data:
        raw_ingredients = update_data.pop("ingredients")
        db_recipe.ingredients = [{"name": i} for i in raw_ingredients]

    for field, value in update_data.items():
        setattr(db_recipe, field, value)

    db_recipe.status = "pending"
    db_recipe.rejection_reason = None

    db.add(db_recipe)
    await db.commit()
    await db.refresh(db_recipe)

    # Notify moderators about re-submitted recipe
    from app.services import notification_service

    mod_query = select(User.id).where(
        User.role.in_(["moderator", "admin"]),
        User.is_active == True,  # noqa: E712
    )
    mod_result = await db.execute(mod_query)
    mod_ids = [row[0] for row in mod_result.all()]

    if mod_ids:
        await notification_service.notify_bulk_and_broadcast(
            db,
            user_ids=mod_ids,
            type="new_pending_recipe",
            title=db_recipe.title,
            message="",
            recipe_id=db_recipe.id,
        )
        await db.commit()

    return db_recipe


async def delete_recipe(
    db: AsyncSession,
    *,
    recipe_id: int,
    deleted_by: User | None = None,
) -> Recipe | None:
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if db_recipe:
        # Notify owner if recipe is deleted by a mod/admin (not the owner)
        owner_id = db_recipe.owner_id
        title = db_recipe.title
        is_mod_delete = (
            deleted_by is not None
            and deleted_by.role in ("moderator", "admin")
            and deleted_by.id != owner_id
        )

        await db.delete(db_recipe)

        if is_mod_delete and owner_id is not None:
            from app.services import notification_service

            await notification_service.notify_and_broadcast(
                db,
                user_id=owner_id,
                type="recipe_deleted",
                title=title,
                message="",
                recipe_id=None,  # recipe is deleted, no link
            )

        await db.commit()
        await vector_store.delete_recipe(recipe_id)
    return db_recipe


def _derive_thumb_url(full_url: str) -> str:
    """Derive the thumbnail URL from a full image URL (e.g. .webp → _thumb.webp)."""
    if full_url.endswith(".webp"):
        return full_url[:-5] + "_thumb.webp"
    # Fallback: append _thumb before extension
    dot_idx = full_url.rfind(".")
    if dot_idx != -1:
        return full_url[:dot_idx] + "_thumb" + full_url[dot_idx:]
    return full_url + "_thumb"


async def delete_recipe_images(
    db: AsyncSession, *, recipe_id: int, urls_to_delete: list[str]
) -> Recipe | None:
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not db_recipe:
        return None

    current_urls = set(db_recipe.image_urls) if db_recipe.image_urls else set()
    target_urls = set(urls_to_delete)
    urls_to_process = current_urls.intersection(target_urls)

    if not urls_to_process:
        return db_recipe

    remaining_urls = list(current_urls - urls_to_process)
    db_recipe.image_urls = remaining_urls

    # Also remove corresponding thumbnails
    current_thumbs = list(db_recipe.thumbnail_urls) if db_recipe.thumbnail_urls else []
    thumb_urls_to_delete = {_derive_thumb_url(url) for url in urls_to_process}
    remaining_thumbs = [t for t in current_thumbs if t not in thumb_urls_to_delete]
    db_recipe.thumbnail_urls = remaining_thumbs

    db.add(db_recipe)
    await db.commit()
    await db.refresh(db_recipe)

    # Delete from S3: full images + their thumbnails
    for url in urls_to_process:
        await s3_client.delete_image_from_s3(url)
        await s3_client.delete_image_from_s3(_derive_thumb_url(url))

    return db_recipe


async def search_recipes_by_vector(
    db: AsyncSession,
    *,
    query_str: str,
    include_str: str | None = None,
    exclude_str: str | None = None,
    min_time: int | None = None,
    max_time: int | None = None,
    difficulty: str | None = None,
    cuisine: str | None = None,
) -> list[Recipe]:
    recipe_ids = await vector_store.search(query=query_str, n_results=50)

    if not recipe_ids:
        return []

    # Only return approved recipes in search results
    query = _with_owner(
        select(Recipe).where(
            Recipe.id.in_(recipe_ids),
            Recipe.status == "approved",
        )
    )

    query = _apply_filters(
        query, include_str, exclude_str,
        min_time=min_time, max_time=max_time,
        difficulty=difficulty, cuisine=cuisine,
    )

    result = await db.execute(query)
    recipes = result.scalars().unique().all()

    recipes_map = {r.id: r for r in recipes}
    ordered_recipes = []
    for rid in recipe_ids:
        if rid in recipes_map:
            ordered_recipes.append(recipes_map[rid])

    return ordered_recipes[:6]
