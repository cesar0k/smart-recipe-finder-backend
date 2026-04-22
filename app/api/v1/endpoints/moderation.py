from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import require_moderator
from app.core.cache import Cache, get_cache
from app.db.session import get_db
from app.models.recipe_draft import RecipeDraft
from app.models.user import User
from app.services import (
    moderation_log_service,
    moderation_service,
    recipe_service,
    search_cache,
)

router = APIRouter()


@router.get(
    "/pending-count",
    response_model=schemas.PendingCountResponse,
    operation_id="get_pending_count",
)
async def get_pending_count(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
) -> schemas.PendingCountResponse:
    counts = await moderation_service.get_pending_counts(db)
    return schemas.PendingCountResponse(**counts)


@router.get(
    "/history",
    response_model=list[schemas.ModerationLogResponse],
    operation_id="list_moderation_history",
)
async def list_moderation_history(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    recipe_id: int | None = Query(None, description="Filter by recipe ID"),
    search: str | None = Query(None, description="Search by recipe title", max_length=255),
) -> list[schemas.ModerationLogResponse]:
    logs = await moderation_log_service.get_history(
        db, skip=skip, limit=limit, recipe_id=recipe_id, search=search
    )
    return [schemas.ModerationLogResponse.model_validate(log) for log in logs]


@router.delete(
    "/history/{log_id}",
    operation_id="delete_moderation_log",
)
async def delete_moderation_log(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
    log_id: int,
) -> dict[str, bool]:
    deleted = await moderation_log_service.delete_log(db, log_id=log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return {"deleted": True}


@router.delete(
    "/history",
    operation_id="delete_all_moderation_history",
)
async def delete_all_moderation_history(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
) -> dict[str, int]:
    count = await moderation_log_service.delete_all_logs(db)
    return {"deleted": count}


@router.get(
    "/recipes",
    response_model=list[schemas.Recipe],
    operation_id="list_pending_recipes",
)
async def list_pending_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
) -> list[schemas.Recipe]:
    recipes = await moderation_service.get_pending_recipes(db)
    return [schemas.Recipe.model_validate(r) for r in recipes]


@router.post(
    "/recipes/{recipe_id}",
    response_model=schemas.Recipe,
    operation_id="moderate_recipe",
)
async def moderate_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    mod: Annotated[User, Depends(require_moderator)],
    recipe_id: int,
    body: schemas.ModerationAction,
) -> schemas.Recipe:
    recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    if recipe.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Recipe is already '{recipe.status}', not pending",
        )

    if body.action == "reject" and not body.rejection_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rejection reason is required",
        )

    updated = await moderation_service.moderate_recipe(
        db,
        recipe=recipe,
        action=body.action,
        moderator_id=mod.id,
        rejection_reason=body.rejection_reason,
    )
    await search_cache.bump_search_version(cache)
    return schemas.Recipe.model_validate(updated)


@router.get(
    "/drafts",
    response_model=list[schemas.RecipeDraftResponse],
    operation_id="list_pending_drafts",
)
async def list_pending_drafts(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
) -> list[schemas.RecipeDraftResponse]:
    drafts = await moderation_service.get_pending_drafts(db)
    return [schemas.RecipeDraftResponse.model_validate(d) for d in drafts]


@router.post(
    "/drafts/{draft_id}",
    response_model=schemas.RecipeDraftResponse,
    operation_id="moderate_draft",
)
async def moderate_draft(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    mod: Annotated[User, Depends(require_moderator)],
    draft_id: int,
    body: schemas.ModerationAction,
) -> schemas.RecipeDraftResponse:
    from sqlalchemy.future import select

    result = await db.execute(
        select(RecipeDraft).where(RecipeDraft.id == draft_id)
    )
    draft = result.scalar_one_or_none()

    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Draft is already '{draft.status}', not pending",
        )

    if body.action == "reject" and not body.rejection_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rejection reason is required",
        )

    updated = await moderation_service.moderate_draft(
        db,
        draft=draft,
        action=body.action,
        moderator_id=mod.id,
        rejection_reason=body.rejection_reason,
    )
    await search_cache.bump_search_version(cache)
    return schemas.RecipeDraftResponse.model_validate(updated)
