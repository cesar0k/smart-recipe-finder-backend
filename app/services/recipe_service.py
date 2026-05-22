import asyncio
import json
import re
import uuid
from collections.abc import Sequence
from typing import Any
from typing import cast as t_cast

from fastapi import UploadFile
from sqlalchemy import String, distinct, func, not_, or_  # noqa: F401
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.selectable import Select

from app import schemas
from app.core.cache import Cache
from app.core.config import settings
from app.core.exceptions import (
    InvalidStateError,
    NotAuthorizedError,
    NotFoundError,
)
from app.core.s3_client import s3_client
from app.core.text_utils import get_word_forms
from app.core.vector_store import vector_store
from app.models import Recipe, RecipeTags
from app.models.recipe_draft import RecipeDraft
from app.models.user import User
from app.schemas import RecipeCreate, RecipeUpdate
from app.services import cache_keys, favorite_service, image_service, search_cache, similar_cache

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
    """Eager-load the relationships the Pydantic Recipe schema needs to
    serialize: owner, cuisine, images, ingredients (with their joined
    Ingredient). Without these the ORM properties would raise from
    lazy="raise"/"noload"."""
    from app.models.recipe_ingredient import RecipeIngredient

    return query.options(
        selectinload(Recipe.owner),
        selectinload(Recipe.cuisine_ref),
        selectinload(Recipe.images),
        selectinload(Recipe.recipe_ingredients).selectinload(
            RecipeIngredient.ingredient
        ),
    )


def _with_relations(query: Select[tuple[Recipe]]) -> Select[tuple[Recipe]]:
    """_with_owner + tags."""
    from app.models.recipe_ingredient import RecipeIngredient

    return query.options(
        selectinload(Recipe.owner),
        selectinload(Recipe.tags),
        selectinload(Recipe.cuisine_ref),
        selectinload(Recipe.images),
        selectinload(Recipe.recipe_ingredients).selectinload(
            RecipeIngredient.ingredient
        ),
    )


async def _set_recipe_ingredients(
    db: AsyncSession, recipe: Recipe, names: list[str]
) -> None:
    """Replace recipe.recipe_ingredients so that, after the call, it matches
    *names* exactly (in given order).

    `names` come straight from the request (list of strings, no amount/unit
    today). We normalise each name, find-or-create the Ingredient row and
    rebuild the M2M with fresh positions.
    """
    from app.models.recipe_ingredient import RecipeIngredient
    from app.services import ingredient_service

    # Wipe previous rows for this recipe — cheap, and avoids the dance of
    # diffing existing vs new for amount/unit (none of which we currently
    # surface in the API).
    await db.execute(
        sa_delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id)
    )

    ingredients_map = await ingredient_service.get_or_create_many(db, names=names)

    seen: set[int] = set()
    for idx, raw_name in enumerate(names):
        canonical = raw_name.strip().lower()
        if not canonical:
            continue
        ing = ingredients_map.get(canonical)
        if ing is None or ing.id in seen:
            # No row (empty name) or duplicate in the same payload — skip.
            continue
        seen.add(ing.id)
        db.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=ing.id,
                position=idx,
            )
        )


async def _ensure_ingredients_loaded(db: AsyncSession, recipe: Recipe) -> None:
    """Eager-load recipe_ingredients AND their .ingredient so the
    Recipe.ingredients property doesn't see lazy="raise"."""
    await db.refresh(recipe, attribute_names=["recipe_ingredients"])
    for ri in recipe.recipe_ingredients:
        await db.refresh(ri, attribute_names=["ingredient"])


async def _ensure_images_loaded(db: AsyncSession, recipe: Recipe) -> None:
    """Eager-load Recipe.images so the image_urls / thumbnail_urls properties
    don't return an empty list after a fresh insert/refresh."""
    await db.refresh(recipe, attribute_names=["images"])


async def _add_recipe_images(
    db: AsyncSession, recipe: Recipe, pairs: list[tuple[str, str]]
) -> None:
    """Append (full_url, thumb_url) pairs to recipe.images, continuing the
    existing positions. Caller is responsible for committing."""
    from app.models.recipe_image import RecipeImage

    res = await db.execute(
        select(func.coalesce(func.max(RecipeImage.position), -1)).where(
            RecipeImage.recipe_id == recipe.id
        )
    )
    next_pos = (res.scalar_one() or -1) + 1
    for full_url, thumb_url in pairs:
        db.add(
            RecipeImage(
                recipe_id=recipe.id,
                full_url=full_url,
                thumbnail_url=thumb_url,
                position=next_pos,
            )
        )
        next_pos += 1


async def _set_recipe_images(
    db: AsyncSession, recipe: Recipe, new_full_urls: list[str]
) -> None:
    """Replace recipe.images so that, after the call, the relationship matches
    new_full_urls *exactly*:
      - rows whose full_url is in new_full_urls survive (keeping their thumb)
      - rows whose full_url is absent are deleted (and their S3 objects too)
      - position is rewritten to match the order of new_full_urls.
    """
    from app.models.recipe_image import RecipeImage

    existing_res = await db.execute(
        select(RecipeImage).where(RecipeImage.recipe_id == recipe.id)
    )
    existing = {img.full_url: img for img in existing_res.scalars().all()}

    new_set = set(new_full_urls)
    for full_url, img in existing.items():
        if full_url not in new_set:
            await s3_client.delete_image_from_s3(full_url)
            await s3_client.delete_image_from_s3(_derive_thumb_url(full_url))
            await db.delete(img)

    for idx, full_url in enumerate(new_full_urls):
        if full_url in existing:
            existing[full_url].position = idx
            db.add(existing[full_url])
        else:
            # No matching existing row — derive thumbnail from URL pattern.
            db.add(
                RecipeImage(
                    recipe_id=recipe.id,
                    full_url=full_url,
                    thumbnail_url=_derive_thumb_url(full_url),
                    position=idx,
                )
            )


async def _ensure_cuisine_loaded(db: AsyncSession, recipe: Recipe) -> None:
    """After a refresh, eager-load Recipe.cuisine_ref if the FK is set but the
    relationship attribute hasn't been populated yet. Cheap no-op when not
    needed."""
    if recipe.cuisine_id is None:
        return
    await db.refresh(recipe, attribute_names=["cuisine_ref"])


async def _reload_recipe(db: AsyncSession, recipe_id: int) -> Recipe | None:
    """Reload a recipe with all relationships (owner + tags) eagerly loaded."""
    result = await db.execute(_with_relations(select(Recipe).where(Recipe.id == recipe_id)))
    return result.scalar_one_or_none()


def _ensure_can_modify(recipe: Recipe, user: User) -> None:
    """Raise NotAuthorizedError unless user is owner / moderator / admin."""
    if user.role in ("moderator", "admin"):
        return
    if recipe.owner_id != user.id:
        raise NotAuthorizedError("Not authorized to modify this recipe")


async def _bump_recipe_caches(cache: Cache | None, *, recipe_id: int | None = None) -> None:
    """Invalidate all recipe-related caches after a write."""
    if cache is None:
        return
    await search_cache.bump_search_version(cache)
    await similar_cache.bump_similar_version(cache)
    await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)


def _apply_tag_filter(
    recipes: list[Recipe],
    tag_filter: dict[str, Any],
) -> list[Recipe]:
    """Post-filter vector search results by LLM-detected tag constraints.

    Recipes without tags (tags is None) are always included — tags may not have
    been generated yet and we must not incorrectly exclude them.
    """
    if not tag_filter:
        return recipes

    result = []
    for recipe in recipes:
        # With lazy="noload", recipe.tags is None if not loaded via selectinload.
        # Tags not generated yet → include recipe (don't exclude on missing data).
        tags = recipe.tags

        if tags is None:
            # Tags not generated yet — include to avoid false exclusion
            result.append(recipe)
            continue

        excluded = False
        for field, expected in tag_filter.items():
            actual = getattr(tags, field, None)
            if actual is None:
                # Missing tag value — don't exclude
                continue
            if isinstance(expected, bool):
                # e.g. {"vegetarian": True} → exclude if tags.vegetarian is False
                if expected is True and actual is False:
                    excluded = True
                    break
                if expected is False and actual is True:
                    excluded = True
                    break
            elif isinstance(expected, list):
                # e.g. {"spice_level": ["hot","very_hot"]} → keep only if matches
                if actual not in expected:
                    excluded = True
                    break
            elif isinstance(expected, str):
                # e.g. {"meal_type": "soup"}
                if actual != expected:
                    excluded = True
                    break

        if not excluded:
            result.append(recipe)

    return result


def _is_positive_only_intent(tag_filter: dict[str, Any]) -> bool:
    """Return True if tag_filter contains ONLY positive constraints suitable for SQL-first.

    Fields allowed for SQL-first:
      vegetarian, vegan, gluten_free, dairy_free, meal_type, main_protein

    Fields that stay in vector-first path:
      spice_level, occasion, cost_tier, technique_difficulty, allergens
    """
    SQL_FIRST_FIELDS = {
        "vegetarian",
        "vegan",
        "gluten_free",
        "dairy_free",
        "meal_type",
        "main_protein",
    }  # noqa: E501
    negation_indicators = {"main_protein": "none"}

    for field, expected in tag_filter.items():
        # Only route to SQL-first if ALL fields are in the allowed set
        if field not in SQL_FIRST_FIELDS:
            return False
        # Bool False = exclusion → not positive-only
        if isinstance(expected, bool) and expected is False:
            return False
        # Known negation patterns
        if field in negation_indicators and expected == negation_indicators[field]:
            return False
    return True


async def _sql_tag_search(
    db: AsyncSession,
    tag_filter: dict[str, Any],
    query_str: str,
    include_str: str | None,
    exclude_str: str | None,
    min_time: int | None,
    max_time: int | None,
    difficulty: str | None,
    cuisine: str | None,
    hard_limit: int,
    sort: str = "newest",
) -> list[Recipe]:
    """SQL-first search: fetch all recipes matching tags, re-rank by vector similarity."""
    # Build SQL filter for RecipeTags
    join_conditions = []
    for field, expected in tag_filter.items():
        col = getattr(RecipeTags, field, None)
        if col is None:
            continue
        if isinstance(expected, bool):
            join_conditions.append(col == expected)
        elif isinstance(expected, list):
            join_conditions.append(col.in_(expected))
        elif isinstance(expected, str):
            join_conditions.append(col == expected)

    base_query = _with_owner(
        select(Recipe)
        .join(RecipeTags, Recipe.id == RecipeTags.recipe_id)
        .where(Recipe.status == "approved", *join_conditions)
        .options(selectinload(Recipe.tags))
    )
    base_query = _apply_filters(
        base_query,
        include_str,
        exclude_str,
        min_time=min_time,
        max_time=max_time,
        difficulty=difficulty,
        cuisine=cuisine,
    )

    result = await db.execute(base_query)
    candidates = result.scalars().unique().all()

    if not candidates:
        return []

    # Re-rank by vector similarity to the query
    candidate_ids = {r.id for r in candidates}
    vector_pairs = await vector_store.search(query=query_str, n_results=len(candidates) + 10)
    # Keep only candidates that matched the SQL filter, preserving vector order
    ranked = [(rid, dist) for rid, dist in vector_pairs if rid in candidate_ids]
    # Add any SQL matches not in vector results at the end (new recipes not yet embedded)
    ranked_ids = {rid for rid, _ in ranked}
    unranked = [(r.id, 1.0) for r in candidates if r.id not in ranked_ids]
    ranked.extend(unranked)

    recipes_map = {r.id: r for r in candidates}
    top = ranked[:hard_limit]
    final = [recipes_map[rid] for rid, _ in top if rid in recipes_map]

    if sort == "popular":
        final.sort(key=lambda r: (-(r.engagement_score or 0), -r.id))
    elif sort == "top_rated":
        final.sort(key=lambda r: (-(r.average_rating or 0), -(r.ratings_count or 0), -r.id))
    elif sort == "most_favorited":
        final.sort(key=lambda r: (-(r.favorites_count or 0), -r.id))
    return final


def _apply_adaptive_limit(
    pairs: list[tuple[int, float]],
    abs_max: float,
    rel_margin: float,
    hard_limit: int,
) -> list[tuple[int, float]]:
    """
    Filter (recipe_id, distance) pairs adaptively.

    Returns an empty list if the best match exceeds abs_max (nothing relevant).
    Otherwise keeps all pairs within rel_margin of the best distance, capped at
    abs_max and hard_limit.
    """
    if not pairs:
        return []
    min_dist = pairs[0][1]
    if min_dist > abs_max:
        return []
    threshold = min(min_dist + rel_margin, abs_max)
    filtered = [(rid, d) for rid, d in pairs if d <= threshold]
    return filtered[:hard_limit]


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

    # Include LLM-generated tags in the document when available.
    # This significantly improves semantic search quality — e.g. a recipe tagged
    # "vegetarian, soup" will score higher for "vegetarian soup" queries.
    tags_str = ""
    tags = recipe.tags  # None if not yet generated (lazy="noload" → no exception)
    if tags is not None:
        tag_parts: list[str] = []
        if tags.vegetarian:
            tag_parts.append("vegetarian")
        if tags.vegan:
            tag_parts.append("vegan")
        if tags.gluten_free:
            tag_parts.append("gluten-free")
        if tags.dairy_free:
            tag_parts.append("dairy-free")
        if tags.meal_type:
            tag_parts.append(tags.meal_type)
        if tags.main_protein and tags.main_protein != "none":
            tag_parts.append(tags.main_protein)
        if tags.spice_level and tags.spice_level != "none":
            tag_parts.append(f"{tags.spice_level} spice")
        if tags.cooking_method:
            tag_parts.append(tags.cooking_method)
        if tags.cultural_sub_region:
            tag_parts.append(tags.cultural_sub_region)
        if tag_parts:
            tags_str = f"Tags: {', '.join(tag_parts)}. "

    doc_to_embed = (
        f"Title: {recipe.title}. "
        f"{description_str}"
        f"{tags_str}"
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

    Ingredient filters now go through the normalised
    ``recipe_ingredients`` ↔ ``ingredients`` join (instead of the old JSONB
    regex). The behaviour is preserved: a hit on any morphological form of
    a query term counts, and "include foo, bar" means the recipe must match
    both foo AND bar.
    """
    from app.models.ingredient import Ingredient
    from app.models.recipe_ingredient import RecipeIngredient

    def _ingredient_name_matches_any(terms: list[str]):
        # OR-list of case-insensitive ``\yterm\y`` regex matches against
        # ingredients.name. Postgres' POSIX regex (`~*`) supports `\y` word
        # boundaries.
        clauses = []
        for term in terms:
            safe = re.escape(term)
            clauses.append(Ingredient.name.op("~*")(f"\\y{safe}\\y"))
        return or_(*clauses)

    # Include ingredients: per item, recipe must have at least one ingredient
    # matching that item's word-forms.
    if include_str:
        raw_items = [i.strip() for i in include_str.split(",") if i.strip()]
        for item in raw_items:
            terms = get_word_forms(item)
            subq = (
                select(RecipeIngredient.recipe_id)
                .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
                .where(_ingredient_name_matches_any(terms))
            )
            query = query.where(Recipe.id.in_(subq))

    # Exclude ingredients: recipe must NOT have any ingredient that matches
    # any of the word-forms for any of the excluded items.
    if exclude_str:
        raw_items = [i.strip() for i in exclude_str.split(",") if i.strip()]
        all_terms: list[str] = []
        for item in raw_items:
            all_terms.extend(get_word_forms(item))
        if all_terms:
            subq = (
                select(RecipeIngredient.recipe_id)
                .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
                .where(_ingredient_name_matches_any(all_terms))
            )
            query = query.where(not_(Recipe.id.in_(subq)))

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

    # Cuisine multi-select (comma-separated, case-insensitive).
    # Joins to the cuisines reference table and filters by name.
    if cuisine:
        cuisines = [c.strip().lower() for c in cuisine.split(",") if c.strip()]
        if cuisines:
            from app.models.cuisine import Cuisine

            query = query.join(Cuisine, Recipe.cuisine_id == Cuisine.id).where(
                func.lower(Cuisine.name).in_(cuisines)
            )

    return query


async def get_distinct_cuisines(db: AsyncSession) -> list[str]:
    """Return sorted list of distinct cuisine names that are actually used by
    at least one approved recipe."""
    from app.models.cuisine import Cuisine

    result = await db.execute(
        select(distinct(Cuisine.name))
        .join(Recipe, Recipe.cuisine_id == Cuisine.id)
        .where(Recipe.status == "approved")
        .order_by(Cuisine.name)
    )
    return [row[0] for row in result.all()]


async def get_distinct_cuisines_cached(db: AsyncSession, cache: Cache | None = None) -> list[str]:
    """Read-through cache wrapper around get_distinct_cuisines."""
    if cache is None:
        return await get_distinct_cuisines(db)

    key = cache_keys.cuisines()
    cached = await cache.get_raw(key)
    if cached is not None:
        return list(json.loads(cached))

    result = await get_distinct_cuisines(db)
    await cache.set_raw(key, json.dumps(result), ttl=cache_keys.TTL_CUISINES)
    return result


# Ordered list of meal_type categories to show on the homepage.
# Only categories with enough recipes are shown; others are skipped.
HOMEPAGE_CATEGORIES: list[tuple[str, str]] = [
    ("soup", "Супы"),
    ("dinner", "Ужины"),
    ("breakfast", "Завтраки"),
    ("dessert", "Десерты"),
    ("salad", "Салаты"),
    ("side", "Гарниры"),
    ("snack", "Закуски"),
    ("lunch", "Обеды"),
]
_MIN_RECIPES_PER_CATEGORY = 2


async def get_recipes_by_categories(
    db: AsyncSession,
    *,
    limit_per: int = 6,
    cache: Cache | None = None,
) -> list[dict[str, Any]]:
    """Return recipes grouped by meal_type for the homepage category shelves.

    Each item: {"meal_type": str, "label": str, "recipes": list[Recipe]}
    Only categories with at least _MIN_RECIPES_PER_CATEGORY recipes are included.
    Results are cached per limit_per value.
    """
    if cache is not None:
        key = cache_keys.categories(limit_per)
        cached = await cache.get_raw(key)
        if cached is not None:
            # Cached as JSON; we need ORM objects for schema validation.
            # Store only ids and re-fetch — or skip cache for ORM objects.
            # Simpler: cache the serialised list and return dicts directly.
            return json.loads(cached)  # type: ignore[no-any-return]

    result: list[dict[str, Any]] = []

    for meal_type, label in HOMEPAGE_CATEGORIES:
        query = (
            _with_owner(
                select(Recipe)
                .join(RecipeTags, Recipe.id == RecipeTags.recipe_id)
                .where(
                    Recipe.status == "approved",
                    RecipeTags.meal_type == meal_type,
                )
                .options(selectinload(Recipe.tags))
            )
            .order_by(Recipe.id.desc())
            .limit(limit_per)
        )
        rows = await db.execute(query)
        recipes = rows.scalars().unique().all()

        if len(recipes) < _MIN_RECIPES_PER_CATEGORY:
            continue

        from app import schemas as _schemas

        result.append(
            {
                "meal_type": meal_type,
                "label": label,
                "recipes": [
                    json.loads(_schemas.Recipe.model_validate(r).model_dump_json()) for r in recipes
                ],
            }
        )

    if cache is not None:
        key = cache_keys.categories(limit_per)
        await cache.set_raw(key, json.dumps(result), ttl=cache_keys.TTL_CATEGORIES)

    return result


async def create_recipe(
    db: AsyncSession,
    *,
    recipe_in: RecipeCreate,
    current_user: User,
    cache: Cache | None = None,
) -> Recipe:
    # image_urls/thumbnail_urls are excluded — the relationship is populated
    # via upload_recipe_images() after the recipe is created. cuisine is
    # converted to a FK via cuisine_service below.
    recipe_data = recipe_in.model_dump(
        exclude={"ingredients", "cuisine", "image_urls", "thumbnail_urls"}
    )

    # Moderators and admins get auto-approved, regular users go to pending
    status = "approved" if current_user.role in ("moderator", "admin") else "pending"

    # Normalise the free-form cuisine into a FK to the cuisines reference table.
    from app.services import cuisine_service

    cuisine_obj = await cuisine_service.get_or_create_by_name(db, name=recipe_in.cuisine)

    db_recipe = Recipe(
        **recipe_data,
        owner_id=current_user.id,
        status=status,
        cuisine_id=cuisine_obj.id if cuisine_obj else None,
    )

    db.add(db_recipe)
    await db.flush()  # need an id before we can attach the M2M rows
    await _set_recipe_ingredients(db, db_recipe, list(recipe_in.ingredients))
    await db.commit()
    await db.refresh(db_recipe)
    await _ensure_cuisine_loaded(db, db_recipe)
    await _ensure_images_loaded(db, db_recipe)
    await _ensure_ingredients_loaded(db, db_recipe)

    if db_recipe.status == "approved":
        text, meta = _create_semantic_document(db_recipe)
        await vector_store.upsert_recipe(
            recipe_id=db_recipe.id,
            title=db_recipe.title,
            full_text=text,
            metadata=meta,
        )

    from app.services import notification_service

    # Notify moderators/admins about new pending recipe
    if db_recipe.status == "pending":
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

    # Notify followers when admin/mod publishes a recipe directly (status=approved)
    if db_recipe.status == "approved":
        from app.services import follow_service

        follower_ids = await follow_service.get_follower_ids(db, user_id=db_recipe.owner_id)
        follower_ids.discard(db_recipe.owner_id)
        if follower_ids:
            # Fetch author username for the notification body
            owner_row = await db.execute(
                select(User.username, User.display_name).where(User.id == db_recipe.owner_id)
            )
            owner = owner_row.one_or_none()
            author_name = (owner.display_name or owner.username) if owner else ""
            await notification_service.notify_bulk_and_broadcast(
                db,
                user_ids=list(follower_ids),
                type="followed_user_published",
                title=db_recipe.title,
                message=author_name,
                recipe_id=db_recipe.id,
            )
            await db.commit()

    await _bump_recipe_caches(cache)
    # Reload with all relationships so callers can safely serialise the response
    reloaded = await _reload_recipe(db, db_recipe.id)
    return reloaded if reloaded is not None else db_recipe


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
    meal_type: str | None = None,
    has_comments: bool = False,
    sort: str = "newest",
) -> Sequence[Recipe]:
    """Public feed — only approved recipes. Always.

    ``sort`` accepts ``"newest"`` (default), ``"popular"`` (engagement score),
    ``"top_rated"`` (average rating), or ``"most_favorited"`` (favorites count).
    ``has_comments=True`` filters to recipes with at least one comment.
    """
    query = _with_owner(select(Recipe).where(Recipe.status == "approved"))

    if has_comments:
        query = query.where(Recipe.comments_count > 0)

    # meal_type filter via RecipeTags join (used by "Show all" on category shelves)
    if meal_type:
        query = query.join(RecipeTags, Recipe.id == RecipeTags.recipe_id).where(
            RecipeTags.meal_type == meal_type
        )

    query = _apply_filters(
        query,
        include_str,
        exclude_str,
        min_time=min_time,
        max_time=max_time,
        difficulty=difficulty,
        cuisine=cuisine,
    )
    if sort == "popular":
        query = query.order_by(Recipe.engagement_score.desc(), Recipe.id.desc())
    elif sort == "top_rated":
        query = query.order_by(
            Recipe.average_rating.desc(), Recipe.ratings_count.desc(), Recipe.id.desc()
        )
    elif sort == "most_favorited":
        query = query.order_by(Recipe.favorites_count.desc(), Recipe.id.desc())
    else:
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
    query = _with_owner(base.order_by(Recipe.id.desc()).offset(skip).limit(limit))
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


async def get_user_recipes_for_caller(
    db: AsyncSession,
    *,
    user_id: int,
    viewer: User | None,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[Recipe]:
    """Public 'view a user's recipes' flow.

    Moderators/admins see all statuses; everyone else sees only approved.
    """
    is_privileged = viewer is not None and viewer.role in ("moderator", "admin")
    return await get_user_recipes(
        db=db,
        user_id=user_id,
        skip=skip,
        limit=limit,
        approved_only=not is_privileged,
    )


async def get_recipe_for_caller(
    db: AsyncSession,
    *,
    recipe_id: int,
    current_user: User | None,
    cache: Cache | None = None,
) -> schemas.Recipe:
    """Public read flow used by GET /recipes/{id}.

    - Approved recipes are cached and visible to everyone.
    - Non-approved recipes are visible only to owner / mod / admin and never cached.
    - Raises NotFoundError when missing or not visible.

    ``is_favorited`` is set per-caller AFTER the cache read — the cached
    payload itself stays user-agnostic.
    """
    key = cache_keys.recipe_detail(recipe_id)
    response: schemas.Recipe | None = None

    if cache is not None:
        cached = await cache.get_model(key, schemas.Recipe)
        if cached is not None and cached.status == "approved":
            response = cached

    if response is None:
        recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
        if recipe is None:
            raise NotFoundError("Recipe not found")

        if recipe.status != "approved":
            is_owner = current_user is not None and recipe.owner_id == current_user.id
            is_mod = current_user is not None and current_user.role in (
                "moderator",
                "admin",
            )
            if not (is_owner or is_mod):
                raise NotFoundError("Recipe not found")

        response = schemas.Recipe.model_validate(recipe)
        if response.status == "approved" and cache is not None:
            await cache.set_model(key, response, ttl=cache_keys.TTL_RECIPE_DETAIL)

    if current_user is not None and response.status == "approved":
        from app.services import rating_service

        favorited, user_rating_row = await asyncio.gather(
            favorite_service.get_favorited_recipe_ids(
                db, user_id=current_user.id, recipe_ids=[recipe_id]
            ),
            rating_service.get_user_rating(
                db, user_id=current_user.id, recipe_id=recipe_id
            ),
        )
        # Fresh copy so we don't mutate the cached instance.
        response = response.model_copy(
            update={
                "is_favorited": recipe_id in favorited,
                "user_rating": user_rating_row.rating if user_rating_row else None,
            }
        )

    return response


async def enrich_recipes_for_caller(
    db: AsyncSession,
    *,
    recipes: Sequence[Recipe],
    viewer: User | None,
) -> list[schemas.Recipe]:
    """Validate ORM rows into ``Recipe`` schemas and attach ``is_favorited``.

    Single-batched lookup — N+1 safe. Anonymous viewers skip the DB hit.
    """
    response = [schemas.Recipe.model_validate(r) for r in recipes]
    if viewer is None or not response:
        return response

    favorited = await favorite_service.get_favorited_recipe_ids(
        db, user_id=viewer.id, recipe_ids=[r.id for r in response]
    )
    if not favorited:
        return response
    return [
        r.model_copy(update={"is_favorited": True}) if r.id in favorited else r for r in response
    ]


async def get_recipe_by_id(db: AsyncSession, *, recipe_id: int) -> Recipe | None:
    query = _with_owner(
        select(Recipe).where(Recipe.id == recipe_id).options(selectinload(Recipe.tags))
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def update_recipe(
    db: AsyncSession,
    *,
    recipe_id: int,
    recipe_in: RecipeUpdate,
    current_user: User,
    cache: Cache | None = None,
) -> Recipe | RecipeDraft:
    """Update a recipe.

    - Moderator/admin: update directly.
    - Regular user (owner): create a draft instead.

    Raises NotFoundError if missing, NotAuthorizedError if caller can't modify.
    """
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if db_recipe is None:
        raise NotFoundError("Recipe not found")
    _ensure_can_modify(db_recipe, current_user)

    if current_user.role in ("moderator", "admin"):
        result: Recipe | RecipeDraft = await _update_recipe_directly(
            db, db_recipe=db_recipe, recipe_in=recipe_in
        )
        await _bump_recipe_caches(cache, recipe_id=recipe_id)
        return result

    return await _create_draft(db, db_recipe=db_recipe, recipe_in=recipe_in, author=current_user)


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
        new_urls_list: list[str] = (
            [str(url) for url in raw_urls] if raw_urls else []
        )
        # Apply via the normalised recipe_images table — keeps the survivors,
        # drops the removed ones (and their S3 objects), reorders by the new
        # client-supplied sequence.
        await _set_recipe_images(db, db_recipe, new_urls_list)

    new_ingredient_names: list[str] | None = None
    if "ingredients" in update_data:
        new_ingredient_names = list(update_data.pop("ingredients"))

    if "cuisine" in update_data:
        from app.services import cuisine_service

        new_cuisine_name = update_data.pop("cuisine")
        cuisine_obj = await cuisine_service.get_or_create_by_name(db, name=new_cuisine_name)
        db_recipe.cuisine_id = cuisine_obj.id if cuisine_obj else None

    for field, value in update_data.items():
        setattr(db_recipe, field, value)

    db.add(db_recipe)
    if new_ingredient_names is not None:
        await _set_recipe_ingredients(db, db_recipe, new_ingredient_names)
    await db.commit()
    await db.refresh(db_recipe)
    await _ensure_cuisine_loaded(db, db_recipe)
    await _ensure_images_loaded(db, db_recipe)
    await _ensure_ingredients_loaded(db, db_recipe)

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
    recipe_id: int,
    recipe_in: RecipeUpdate,
    current_user: User,
    cache: Cache | None = None,
) -> Recipe:
    """Re-submit a rejected recipe with corrections.

    Only the owner can resubmit, and only a rejected recipe.
    Raises NotFoundError, NotAuthorizedError, or InvalidStateError.
    """
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if db_recipe is None:
        raise NotFoundError("Recipe not found")
    if db_recipe.owner_id != current_user.id:
        raise NotAuthorizedError("Only the recipe owner can resubmit")
    if db_recipe.status != "rejected":
        raise InvalidStateError("Only rejected recipes can be resubmitted")

    update_data = recipe_in.model_dump(exclude_unset=True)

    # Exclude image_urls — managed separately
    update_data.pop("image_urls", None)

    new_ingredient_names: list[str] | None = None
    if "ingredients" in update_data:
        new_ingredient_names = list(update_data.pop("ingredients"))

    if "cuisine" in update_data:
        from app.services import cuisine_service

        cuisine_obj = await cuisine_service.get_or_create_by_name(
            db, name=update_data.pop("cuisine")
        )
        db_recipe.cuisine_id = cuisine_obj.id if cuisine_obj else None

    for field, value in update_data.items():
        setattr(db_recipe, field, value)

    db_recipe.status = "pending"
    db_recipe.rejection_reason = None

    db.add(db_recipe)
    if new_ingredient_names is not None:
        await _set_recipe_ingredients(db, db_recipe, new_ingredient_names)
    await db.commit()
    await db.refresh(db_recipe)
    await _ensure_cuisine_loaded(db, db_recipe)
    await _ensure_images_loaded(db, db_recipe)
    await _ensure_ingredients_loaded(db, db_recipe)

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

    if cache is not None:
        await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)
    return db_recipe


async def delete_recipe(
    db: AsyncSession,
    *,
    recipe_id: int,
    current_user: User,
    cache: Cache | None = None,
) -> Recipe:
    """Delete a recipe.

    Raises NotFoundError if missing, NotAuthorizedError if caller can't modify.
    Returns the deleted recipe (detached) for response purposes.
    """
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if db_recipe is None:
        raise NotFoundError("Recipe not found")
    _ensure_can_modify(db_recipe, current_user)

    # Notify owner if recipe is deleted by a mod/admin (not the owner)
    owner_id = db_recipe.owner_id
    title = db_recipe.title
    is_mod_delete = current_user.role in ("moderator", "admin") and current_user.id != owner_id

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
    await _bump_recipe_caches(cache, recipe_id=recipe_id)
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
    db: AsyncSession,
    *,
    recipe_id: int,
    urls_to_delete: list[str],
    current_user: User,
    cache: Cache | None = None,
) -> Recipe:
    """Remove images from a recipe.

    Raises NotFoundError if missing, NotAuthorizedError if caller can't modify.
    """
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if db_recipe is None:
        raise NotFoundError("Recipe not found")
    _ensure_can_modify(db_recipe, current_user)

    current_urls = set(db_recipe.image_urls) if db_recipe.image_urls else set()
    target_urls = set(urls_to_delete)
    urls_to_process = current_urls.intersection(target_urls)

    if not urls_to_process:
        return db_recipe

    remaining_urls = [u for u in db_recipe.image_urls if u not in urls_to_process]
    # _set_recipe_images keeps the survivors in the supplied order and removes
    # both the DB rows and the S3 objects for the dropped URLs.
    await _set_recipe_images(db, db_recipe, remaining_urls)
    await db.commit()
    await db.refresh(db_recipe)
    await _ensure_images_loaded(db, db_recipe)

    if cache is not None:
        await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)
    return db_recipe


async def upload_recipe_images(
    db: AsyncSession,
    *,
    recipe_id: int,
    files: list[UploadFile],
    current_user: User,
    cache: Cache | None = None,
    max_files: int = 5,
) -> Recipe:
    """Validate, compress and upload recipe images to S3, then attach the URLs.

    Raises NotFoundError if missing, NotAuthorizedError if caller can't modify,
    InvalidStateError if too many files.
    """
    db_recipe = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if db_recipe is None:
        raise NotFoundError("Recipe not found")
    _ensure_can_modify(db_recipe, current_user)

    if len(files) > max_files:
        raise InvalidStateError(f"Too many files sent. Max {max_files} allowed.")

    async def _process_file(file: UploadFile) -> tuple[str, str]:
        valid_content = await image_service.validate_and_process_image(file)
        original_bytes = valid_content.getvalue()
        versions = image_service.generate_compressed_versions(original_bytes)
        file_id = str(uuid.uuid4())

        full_key = f"recipes/{recipe_id}/{file_id}.webp"
        full_url = await s3_client.upload_file(versions["full"], full_key, "image/webp")

        thumb_key = f"recipes/{recipe_id}/{file_id}_thumb.webp"
        thumb_url = await s3_client.upload_file(versions["thumb"], thumb_key, "image/webp")
        return full_url, thumb_url

    results = await asyncio.gather(*[_process_file(f) for f in files])

    await _add_recipe_images(db, db_recipe, results)
    await db.commit()
    await db.refresh(db_recipe)
    await _ensure_images_loaded(db, db_recipe)

    if cache is not None:
        await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)
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
    sort: str = "newest",
    cache: Cache | None = None,
) -> list[Recipe]:
    from app.services import tag_service

    # Read all three caches in parallel to minimise Redis round-trips.
    cached_pairs: list[tuple[int, float]] | None = None
    cached_rewrite: str | None = None  # None=miss, ""=no rewrite, text=rewritten
    cached_intent: dict[str, Any] | None = None  # None=miss

    if cache is not None:
        cached_pairs, cached_rewrite, cached_intent = await asyncio.gather(
            search_cache.get_cached_search_pairs(cache, query_str),
            search_cache.get_cached_rewrite(cache, query_str),
            search_cache.get_cached_intent(cache, query_str),
        )

    need_rewrite = cached_rewrite is None
    need_intent = cached_intent is None
    need_pairs = cached_pairs is None

    # Fetch whatever is missing. rewrite, intent, and vector(original) are
    # mutually independent so they run concurrently via gather.
    llm_tasks: list[Any] = []
    if need_rewrite:
        llm_tasks.append(tag_service.rewrite_query(query_str))
    if need_intent:
        llm_tasks.append(tag_service.parse_query_intent(query_str))
    if need_pairs:
        llm_tasks.append(vector_store.search(query=query_str, n_results=50))

    if llm_tasks:
        gathered = await asyncio.gather(*llm_tasks)
        idx = 0
        if need_rewrite:
            cached_rewrite = gathered[idx]
            idx += 1
        if need_intent:
            cached_intent = gathered[idx]
            idx += 1
        if need_pairs:
            cached_pairs = gathered[idx]

    if cache is not None:
        if need_rewrite:
            await search_cache.cache_rewrite(cache, query_str, cached_rewrite)
        if need_intent:
            await search_cache.cache_intent(cache, query_str, cached_intent or {})
        if need_pairs and cached_pairs:
            await search_cache.cache_search_pairs(cache, query_str, cached_pairs)

    rewritten: str | None = cached_rewrite or None  # "" sentinel → None
    tag_filter: dict[str, Any] | None = cached_intent
    search_pairs: list[tuple[int, float]] = cached_pairs or []

    # If the query was rewritten, run a second vector search and min-merge the
    # two result sets — keeps original ranking intact while expanding recall.
    if rewritten and rewritten.lower() != query_str.lower():
        pairs_rewrite = await vector_store.search(query=rewritten, n_results=50)
        if pairs_rewrite:
            merged: dict[int, float] = {rid: dist for rid, dist in search_pairs}
            for rid, dist in pairs_rewrite:
                if rid not in merged or dist < merged[rid]:
                    merged[rid] = dist
            search_pairs = sorted(merged.items(), key=lambda x: x[1])

    if not search_pairs:
        return []

    candidate_ids = [rid for rid, _ in search_pairs]
    query = _with_owner(
        select(Recipe)
        .where(
            Recipe.id.in_(candidate_ids),
            Recipe.status == "approved",
        )
        .options(selectinload(Recipe.tags))
    )
    query = _apply_filters(
        query,
        include_str,
        exclude_str,
        min_time=min_time,
        max_time=max_time,
        difficulty=difficulty,
        cuisine=cuisine,
    )

    # Positive-only intent → SQL-first search re-ranked by vector.
    # Negation/exclusion intent → vector-first with post-filter.
    # No intent → pure vector.
    if tag_filter and _is_positive_only_intent(tag_filter):
        return await _sql_tag_search(
            db,
            tag_filter,
            query_str,
            include_str,
            exclude_str,
            min_time,
            max_time,
            difficulty,
            cuisine,
            hard_limit=settings.SEARCH_HARD_LIMIT,
            sort=sort,
        )

    result = await db.execute(query)
    recipes_map = {r.id: r for r in result.scalars().unique().all()}

    ordered_pairs = [(rid, dist) for rid, dist in search_pairs if rid in recipes_map]

    # Negation post-filter before adaptive limit
    if tag_filter:
        filtered_recipes = _apply_tag_filter(
            [recipes_map[rid] for rid, _ in ordered_pairs if rid in recipes_map],
            tag_filter,
        )
        filtered_ids = {r.id for r in filtered_recipes}
        ordered_pairs = [(rid, dist) for rid, dist in ordered_pairs if rid in filtered_ids]

    adaptive = _apply_adaptive_limit(
        ordered_pairs,
        abs_max=settings.SEARCH_ABSOLUTE_MAX_DIST,
        rel_margin=settings.SEARCH_RELATIVE_MARGIN,
        hard_limit=settings.SEARCH_HARD_LIMIT,
    )
    final = [recipes_map[rid] for rid, _ in adaptive]

    if sort == "popular":
        final.sort(key=lambda r: (-(r.engagement_score or 0), -r.id))
    elif sort == "top_rated":
        final.sort(key=lambda r: (-(r.average_rating or 0), -(r.ratings_count or 0), -r.id))
    elif sort == "most_favorited":
        final.sort(key=lambda r: (-(r.favorites_count or 0), -r.id))
    return final


async def get_similar_recipes(
    db: AsyncSession,
    *,
    recipe_id: int,
    threshold: float,
    limit: int,
    candidate_pool: int = 20,
    cache: Cache | None = None,
) -> list[Recipe]:
    source = await get_recipe_by_id(db=db, recipe_id=recipe_id)
    if source is None or source.status != "approved":
        return []

    pairs: list[tuple[int, float]] | None = None
    if cache is not None:
        pairs = await similar_cache.get_cached_similar_pairs(cache, recipe_id)

    if pairs is None:
        pairs = await vector_store.search_similar_by_id(
            recipe_id=recipe_id, n_results=candidate_pool
        )
        if cache is not None:
            await similar_cache.cache_similar_pairs(cache, recipe_id, pairs)

    # Adaptive limit: threshold acts as abs_max cap, limit as hard_limit cap.
    # Use the stricter of (threshold, SIMILAR_ABSOLUTE_MAX_DIST) and
    # (limit, SIMILAR_HARD_LIMIT) so explicit query params always override upward.
    abs_max = min(threshold, settings.SIMILAR_RECIPES_ABSOLUTE_MAX_DIST)
    hard_limit = min(limit, settings.SIMILAR_RECIPES_HARD_LIMIT)

    adaptive_pairs = _apply_adaptive_limit(
        pairs,
        abs_max=abs_max,
        rel_margin=settings.SIMILAR_RECIPES_RELATIVE_MARGIN,
        hard_limit=hard_limit,
    )
    if not adaptive_pairs:
        return []

    filtered_ids = [rid for rid, _ in adaptive_pairs]
    query = _with_owner(
        select(Recipe).where(
            Recipe.id.in_(filtered_ids),
            Recipe.status == "approved",
        )
    )
    result = await db.execute(query)
    recipes_map = {r.id: r for r in result.scalars().unique().all()}
    return [recipes_map[rid] for rid in filtered_ids if rid in recipes_map]
