from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import get_current_user
from app.core.cache import Cache, get_cache
from app.core.config import settings
from app.db.session import get_db
from app.models.auth.user import User
from app.services.recipe import favorite_service
from app.services.recipe import recipe_service
router = APIRouter()


class FavoritesCheckResponse(BaseModel):
    """Subset of the requested IDs that the current user has favorited."""

    favorited_ids: list[int]


@router.post(
    "/{recipe_id}",
    response_model=schemas.Recipe,
    operation_id="favorite_recipe",
)
async def favorite_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
) -> schemas.Recipe:
    """Idempotently mark the recipe as favorited by the current user."""
    recipe = await favorite_service.add_favorite(
        db, user=current_user, recipe_id=recipe_id, cache=cache
    )
    response = schemas.Recipe.model_validate(recipe)
    return response.model_copy(update={"is_favorited": True})


@router.delete(
    "/{recipe_id}",
    response_model=schemas.Recipe,
    operation_id="unfavorite_recipe",
)
async def unfavorite_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
) -> schemas.Recipe:
    """Idempotently remove the favorite link."""
    recipe = await favorite_service.remove_favorite(
        db, user=current_user, recipe_id=recipe_id, cache=cache
    )
    response = schemas.Recipe.model_validate(recipe)
    return response.model_copy(update={"is_favorited": False})


@router.get(
    "/",
    response_model=list[schemas.Recipe],
    operation_id="read_my_favorites",
)
async def read_my_favorites(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[schemas.Recipe]:
    """Return the current user's favorited (approved-only) recipes."""
    recipes = await favorite_service.get_user_favorites(
        db, user_id=current_user.id, skip=skip, limit=limit
    )
    enriched = await recipe_service.enrich_recipes_for_caller(
        db, recipes=recipes, viewer=current_user
    )
    # Every row came from the join table — guaranteed favorited.
    return [r.model_copy(update={"is_favorited": True}) for r in enriched]


@router.get(
    "/check",
    response_model=FavoritesCheckResponse,
    operation_id="check_favorites",
)
async def check_favorites(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    ids: str = Query(
        ...,
        description=(
            "Comma-separated recipe IDs to check. Up to "
            f"{settings.FAVORITES_CHECK_MAX_IDS} ids per request."
        ),
        max_length=4000,
    ),
) -> FavoritesCheckResponse:
    """Batched lookup of favorited state — used by shelves overlay (shared cache)."""
    try:
        parsed: list[int] = []
        seen: set[int] = set()
        for piece in ids.split(","):
            stripped = piece.strip()
            if not stripped:
                continue
            value = int(stripped)
            if value not in seen:
                seen.add(value)
                parsed.append(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ids must be comma-separated integers") from exc

    if len(parsed) > settings.FAVORITES_CHECK_MAX_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"too many ids (max {settings.FAVORITES_CHECK_MAX_IDS})",
        )

    if not parsed:
        return FavoritesCheckResponse(favorited_ids=[])

    favorited = await favorite_service.get_favorited_recipe_ids(
        db, user_id=current_user.id, recipe_ids=parsed
    )
    return FavoritesCheckResponse(favorited_ids=sorted(favorited))
