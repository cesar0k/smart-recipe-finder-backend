import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api.deps import get_current_user, get_current_user_optional
from app.core.cache import Cache, get_cache
from app.core.s3_client import s3_client
from app.db.session import get_db
from app.models.user import User
from app.services import image_service, recipe_service, search_cache

router = APIRouter()


def _check_recipe_owner(recipe: models.Recipe, user: User) -> None:
    if user.role in ("moderator", "admin"):
        return
    if recipe.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this recipe")


@router.post(
    "/", response_model=schemas.Recipe, status_code=201, operation_id="create_recipe"
)
async def create_new_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_in: schemas.RecipeCreate,
) -> schemas.Recipe:
    db_recipe = await recipe_service.create_recipe(
        db=db, recipe_in=recipe_in, current_user=current_user
    )
    await search_cache.bump_search_version(cache)
    return schemas.Recipe.model_validate(db_recipe)


@router.get("/cuisines", response_model=list[str], operation_id="get_cuisines")
async def get_cuisines(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[str]:
    """Return distinct cuisine values from approved recipes."""
    return await recipe_service.get_distinct_cuisines(db)


@router.get("/", response_model=list[schemas.Recipe], operation_id="read_recipes")
async def read_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
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
    )
    return [schemas.Recipe.model_validate(r) for r in recipes]


@router.get("/my/", response_model=list[schemas.Recipe], operation_id="read_my_recipes")
async def read_my_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[schemas.Recipe]:
    recipes = await recipe_service.get_user_recipes(
        db=db, user_id=current_user.id, skip=skip, limit=limit,
        include_pending_drafts=True,
    )
    return [schemas.Recipe.model_validate(r) for r in recipes]


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
    """
    View recipes of a specific user.
    """
    is_privileged = current_user is not None and current_user.role in ("moderator", "admin")
    recipes = await recipe_service.get_user_recipes(
        db=db, user_id=user_id, skip=skip, limit=limit,
        approved_only=not is_privileged,
    )
    return [schemas.Recipe.model_validate(r) for r in recipes]


@router.get(
    "/search/", response_model=list[schemas.Recipe], operation_id="search_recipes"
)
async def search_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    q: str = Query(
        ..., description="Search query for recipes using vector search", max_length=200
    ),
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
) -> list[schemas.Recipe]:
    recipes = await recipe_service.search_recipes_by_vector(
        db=db,
        query_str=q,
        include_str=include_ingredients,
        exclude_str=exclude_ingredients,
        min_time=min_time,
        max_time=max_time,
        difficulty=difficulty,
        cuisine=cuisine,
        cache=cache,
    )
    return [schemas.Recipe.model_validate(r) for r in recipes]


@router.get(
    "/{recipe_id}", response_model=schemas.Recipe, operation_id="read_recipe_by_id"
)
async def read_recipe_by_id(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_current_user_optional)],
    recipe_id: int,
) -> schemas.Recipe:
    recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Non-approved recipes are only visible to owner or moderator/admin
    if recipe.status != "approved":
        is_owner = current_user is not None and recipe.owner_id == current_user.id
        is_mod = current_user is not None and current_user.role in ("moderator", "admin")
        if not (is_owner or is_mod):
            raise HTTPException(status_code=404, detail="Recipe not found")

    return schemas.Recipe.model_validate(recipe)


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
    recipe_id: int,
    recipe_in: schemas.RecipeUpdate,
) -> schemas.Recipe | schemas.RecipeDraftResponse:
    db_recipe: models.Recipe | None = await recipe_service.get_recipe_by_id(
        db=db, recipe_id=recipe_id
    )
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    _check_recipe_owner(db_recipe, current_user)

    result = await recipe_service.update_recipe(
        db=db, db_recipe=db_recipe, recipe_in=recipe_in, current_user=current_user
    )

    # If a draft was created (regular user editing), return draft response
    if isinstance(result, models.RecipeDraft):
        return schemas.RecipeDraftResponse.model_validate(result)

    await search_cache.bump_search_version(cache)
    return schemas.Recipe.model_validate(result)


@router.post(
    "/{recipe_id}/resubmit",
    response_model=schemas.Recipe,
    operation_id="resubmit_recipe",
)
async def resubmit_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    recipe_in: schemas.RecipeUpdate,
) -> schemas.Recipe:
    """Re-submit a rejected recipe with corrections. Only the owner can resubmit."""
    db_recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    if db_recipe.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the recipe owner can resubmit")

    if db_recipe.status != "rejected":
        raise HTTPException(
            status_code=400, detail="Only rejected recipes can be resubmitted"
        )

    updated = await recipe_service.resubmit_recipe(
        db=db, db_recipe=db_recipe, recipe_in=recipe_in
    )
    return schemas.Recipe.model_validate(updated)


@router.delete(
    "/{recipe_id}", response_model=schemas.Recipe, operation_id="delete_recipe"
)
async def delete_existing_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
) -> schemas.Recipe:
    db_recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    _check_recipe_owner(db_recipe, current_user)

    deleted_recipe = await recipe_service.delete_recipe(
        db=db, recipe_id=recipe_id, deleted_by=current_user
    )
    if not deleted_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    await search_cache.bump_search_version(cache)
    return schemas.Recipe.model_validate(deleted_recipe)


@router.post(
    "/{recipe_id}/image",
    response_model=schemas.Recipe,
    operation_id="upload_recipe_images",
)
async def upload_recipe_images(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    files: Annotated[list[UploadFile], File(...)],
) -> schemas.Recipe:
    recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    _check_recipe_owner(recipe, current_user)

    if len(files) > 5:
        raise HTTPException(
            status_code=400, detail="Too many files sent. Max 5 allowed."
        )

    async def process_file(file: UploadFile) -> tuple[str, str]:
        valid_content = await image_service.validate_and_process_image(file)
        original_bytes = valid_content.getvalue()

        # Generate compressed versions
        versions = image_service.generate_compressed_versions(original_bytes)
        file_id = str(uuid.uuid4())

        # Upload full version (WebP)
        full_key = f"recipes/{recipe_id}/{file_id}.webp"
        full_url = await s3_client.upload_file(versions["full"], full_key, "image/webp")

        # Upload thumbnail (WebP)
        thumb_key = f"recipes/{recipe_id}/{file_id}_thumb.webp"
        thumb_url = await s3_client.upload_file(versions["thumb"], thumb_key, "image/webp")

        return full_url, thumb_url

    results = await asyncio.gather(*[process_file(f) for f in files])

    current_urls = list(recipe.image_urls) if recipe.image_urls else []
    current_thumbs = list(recipe.thumbnail_urls) if recipe.thumbnail_urls else []
    recipe.image_urls = current_urls + [r[0] for r in results]
    recipe.thumbnail_urls = current_thumbs + [r[1] for r in results]

    db.add(recipe)
    await db.commit()
    await db.refresh(recipe)

    return schemas.Recipe.model_validate(recipe)


@router.delete(
    "/{recipe_id}/images",
    response_model=schemas.Recipe,
    operation_id="delete_recipe_images",
)
async def delete_recipe_images(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    delete_data: schemas.RecipeImagesDelete,
) -> schemas.Recipe:
    recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    _check_recipe_owner(recipe, current_user)

    urls_as_strings = [str(url) for url in delete_data.image_urls]

    updated_recipe = await recipe_service.delete_recipe_images(
        db=db, recipe_id=recipe_id, urls_to_delete=urls_as_strings
    )

    if not updated_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return schemas.Recipe.model_validate(updated_recipe)
