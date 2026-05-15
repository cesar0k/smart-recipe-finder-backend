from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import get_current_user
from app.core.cache import Cache, get_cache
from app.db.session import get_db
from app.models.user import User
from app.services import rating_service

router = APIRouter()


@router.post(
    "/{recipe_id}",
    response_model=schemas.Recipe,
    operation_id="upsert_recipe_rating",
)
async def upsert_rating(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    body: schemas.RatingCreate,
) -> schemas.Recipe:
    """Create or update the current user's star rating for a recipe."""
    recipe = await rating_service.upsert_rating(
        db,
        user=current_user,
        recipe_id=recipe_id,
        rating=body.rating,
        cache=cache,
    )
    response = schemas.Recipe.model_validate(recipe)
    return response.model_copy(update={"user_rating": body.rating})


@router.delete(
    "/{recipe_id}",
    response_model=schemas.Recipe,
    operation_id="delete_recipe_rating",
)
async def delete_rating(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
) -> schemas.Recipe:
    """Remove the current user's rating for a recipe."""
    recipe = await rating_service.delete_rating(
        db,
        user=current_user,
        recipe_id=recipe_id,
        cache=cache,
    )
    response = schemas.Recipe.model_validate(recipe)
    return response.model_copy(update={"user_rating": None})


@router.get(
    "/{recipe_id}/my",
    response_model=schemas.RatingResponse | None,
    operation_id="get_my_recipe_rating",
)
async def get_my_rating(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    response: Response,
) -> schemas.RatingResponse | None:
    """Return the current user's rating for a recipe, or null if not rated."""
    row = await rating_service.get_user_rating(
        db, user_id=current_user.id, recipe_id=recipe_id
    )
    if row is None:
        response.status_code = 204
        return None
    return schemas.RatingResponse.model_validate(row)
