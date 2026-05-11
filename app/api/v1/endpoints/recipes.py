from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api.deps import get_current_user, get_current_user_optional
from app.core.cache import Cache, get_cache
from app.core.config import settings
from app.core.health import is_embedding_model_ready
from app.db.session import get_db
from app.models.user import User
from app.services import recipe_service, tag_service

router = APIRouter()


@router.post("/", response_model=schemas.Recipe, status_code=201, operation_id="create_recipe")
async def create_new_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    recipe_in: schemas.RecipeCreate,
) -> schemas.Recipe:
    db_recipe = await recipe_service.create_recipe(
        db=db, cache=cache, recipe_in=recipe_in, current_user=current_user
    )
    if db_recipe.status == "approved":
        background_tasks.add_task(tag_service.classify_recipe_tags, db_recipe.id)
    return schemas.Recipe.model_validate(db_recipe)


@router.get("/cuisines", response_model=list[str], operation_id="get_cuisines")
async def get_cuisines(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> list[str]:
    """Return distinct cuisine values from approved recipes."""
    return await recipe_service.get_distinct_cuisines_cached(db, cache=cache)


@router.get("/categories", operation_id="get_recipe_categories")
async def get_recipe_categories(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    limit_per: int = Query(6, ge=2, le=20, description="Recipes per category"),
) -> list[dict[str, Any]]:
    """Return recipes grouped by meal type for the homepage category shelves.

    Each item: {meal_type, label, recipes[]}. Only categories with enough recipes
    are included. Results are cached.
    """
    return await recipe_service.get_recipes_by_categories(db, limit_per=limit_per, cache=cache)


@router.get("/", response_model=list[schemas.Recipe], operation_id="read_recipes")
async def read_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    include_ingredients: str | None = Query(
        None, description="Comma-separated ingredients to include", max_length=500
    ),
    exclude_ingredients: str | None = Query(
        None, description="Comma-separated ingredients to exclude", max_length=500
    ),
    min_time: int | None = Query(None, ge=0, description="Min cooking time in minutes"),
    max_time: int | None = Query(None, ge=0, description="Max cooking time in minutes"),
    difficulty: str | None = Query(None, description="Comma-separated: easy,medium,hard"),
    cuisine: str | None = Query(None, description="Comma-separated cuisine values"),
    meal_type: str | None = Query(None, description="Filter by meal_type tag (e.g. soup, dessert)"),
    sort: str = Query(
        "newest",
        pattern="^(newest|popular)$",
        description="Sort order: newest (default) or popular (by favorites_count)",
    ),
) -> list[schemas.Recipe]:
    recipes = await recipe_service.get_all_recipes(
        db=db,
        skip=skip,
        limit=limit,
        include_str=include_ingredients,
        exclude_str=exclude_ingredients,
        min_time=min_time,
        max_time=max_time,
        difficulty=difficulty,
        cuisine=cuisine,
        meal_type=meal_type,
        sort=sort,
    )
    return await recipe_service.enrich_recipes_for_caller(db, recipes=recipes, viewer=current_user)


@router.get("/my/", response_model=list[schemas.Recipe], operation_id="read_my_recipes")
async def read_my_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[schemas.Recipe]:
    recipes = await recipe_service.get_user_recipes(
        db=db,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        include_pending_drafts=True,
    )
    return await recipe_service.enrich_recipes_for_caller(db, recipes=recipes, viewer=current_user)


@router.get(
    "/user/{user_id}",
    response_model=list[schemas.Recipe],
    operation_id="read_user_recipes",
)
async def read_user_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[schemas.Recipe]:
    """View recipes of a specific user."""
    recipes = await recipe_service.get_user_recipes_for_caller(
        db=db,
        user_id=user_id,
        viewer=current_user,
        skip=skip,
        limit=limit,
    )
    return await recipe_service.enrich_recipes_for_caller(db, recipes=recipes, viewer=current_user)


@router.get("/search/", response_model=list[schemas.Recipe], operation_id="search_recipes")
async def search_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    q: str = Query(..., description="Search query for recipes using vector search", max_length=200),
    include_ingredients: str | None = Query(
        None, description="Comma-separated ingredients to include", max_length=500
    ),
    exclude_ingredients: str | None = Query(
        None, description="Comma-separated ingredients to exclude", max_length=500
    ),
    min_time: int | None = Query(None, ge=0, description="Min cooking time in minutes"),
    max_time: int | None = Query(None, ge=0, description="Max cooking time in minutes"),
    difficulty: str | None = Query(None, description="Comma-separated: easy,medium,hard"),
    cuisine: str | None = Query(None, description="Comma-separated cuisine values"),
    sort: str = Query(
        "newest",
        pattern="^(newest|popular)$",
        description="Sort order applied AFTER relevance filtering: "
        "'newest' (default) keeps vector-distance order, 'popular' re-orders "
        "the result set by favorites_count.",
    ),
) -> list[schemas.Recipe]:
    if not is_embedding_model_ready():
        raise HTTPException(
            status_code=503,
            detail="Embedding model is warming up, please retry in a moment",
        )

    recipes = await recipe_service.search_recipes_by_vector(
        db=db,
        query_str=q,
        include_str=include_ingredients,
        exclude_str=exclude_ingredients,
        min_time=min_time,
        max_time=max_time,
        difficulty=difficulty,
        cuisine=cuisine,
        sort=sort,
        cache=cache,
    )
    return await recipe_service.enrich_recipes_for_caller(db, recipes=recipes, viewer=current_user)


@router.get("/{recipe_id}", response_model=schemas.Recipe, operation_id="read_recipe_by_id")
async def read_recipe_by_id(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    recipe_id: int,
) -> schemas.Recipe:
    return await recipe_service.get_recipe_for_caller(
        db=db, cache=cache, recipe_id=recipe_id, current_user=current_user
    )


@router.get(
    "/{recipe_id}/similar",
    response_model=list[schemas.Recipe],
    operation_id="read_similar_recipes",
)
async def read_similar_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    recipe_id: int,
    limit: int = Query(settings.SIMILAR_RECIPES_MAX, ge=1, le=20),
    threshold: float = Query(
        settings.SIMILAR_RECIPES_THRESHOLD,
        ge=0.0,
        le=2.0,
        description="Max distance to consider recipes similar",
    ),
) -> list[schemas.Recipe]:
    if not is_embedding_model_ready():
        raise HTTPException(
            status_code=503,
            detail="Embedding model is warming up, please retry in a moment",
        )
    recipes = await recipe_service.get_similar_recipes(
        db=db, recipe_id=recipe_id, threshold=threshold, limit=limit, cache=cache
    )
    return await recipe_service.enrich_recipes_for_caller(db, recipes=recipes, viewer=current_user)


@router.patch(
    "/{recipe_id}",
    response_model=schemas.Recipe | schemas.RecipeDraftResponse,
    operation_id="update_recipe",
)
async def update_existing_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    recipe_id: int,
    recipe_in: schemas.RecipeUpdate,
) -> schemas.Recipe | schemas.RecipeDraftResponse:
    result = await recipe_service.update_recipe(
        db=db,
        cache=cache,
        recipe_id=recipe_id,
        recipe_in=recipe_in,
        current_user=current_user,
    )
    if isinstance(result, models.RecipeDraft):
        return schemas.RecipeDraftResponse.model_validate(result)
    # Re-classify tags whenever the recipe content is updated
    background_tasks.add_task(tag_service.classify_recipe_tags, recipe_id)
    return schemas.Recipe.model_validate(result)


@router.post(
    "/{recipe_id}/resubmit",
    response_model=schemas.Recipe,
    operation_id="resubmit_recipe",
)
async def resubmit_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    recipe_in: schemas.RecipeUpdate,
) -> schemas.Recipe:
    """Re-submit a rejected recipe with corrections. Only the owner can resubmit."""
    updated = await recipe_service.resubmit_recipe(
        db=db,
        cache=cache,
        recipe_id=recipe_id,
        recipe_in=recipe_in,
        current_user=current_user,
    )
    return schemas.Recipe.model_validate(updated)


@router.delete("/{recipe_id}", response_model=schemas.Recipe, operation_id="delete_recipe")
async def delete_existing_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
) -> schemas.Recipe:
    deleted = await recipe_service.delete_recipe(
        db=db, cache=cache, recipe_id=recipe_id, current_user=current_user
    )
    return schemas.Recipe.model_validate(deleted)


@router.post(
    "/{recipe_id}/image",
    response_model=schemas.Recipe,
    operation_id="upload_recipe_images",
)
async def upload_recipe_images(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    files: Annotated[list[UploadFile], File(...)],
) -> schemas.Recipe:
    recipe = await recipe_service.upload_recipe_images(
        db=db,
        cache=cache,
        recipe_id=recipe_id,
        files=files,
        current_user=current_user,
    )
    return schemas.Recipe.model_validate(recipe)


@router.delete(
    "/{recipe_id}/images",
    response_model=schemas.Recipe,
    operation_id="delete_recipe_images",
)
async def delete_recipe_images(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    delete_data: schemas.RecipeImagesDelete,
) -> schemas.Recipe:
    urls_as_strings = [str(url) for url in delete_data.image_urls]
    updated = await recipe_service.delete_recipe_images(
        db=db,
        cache=cache,
        recipe_id=recipe_id,
        urls_to_delete=urls_as_strings,
        current_user=current_user,
    )
    return schemas.Recipe.model_validate(updated)
