from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import require_moderator
from app.db.session import get_db
from app.models.recipe_draft import RecipeDraft
from app.models.user import User
from app.services import moderation_service, recipe_service

router = APIRouter()


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
    _mod: Annotated[User, Depends(require_moderator)],
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
        rejection_reason=body.rejection_reason,
    )
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
    _mod: Annotated[User, Depends(require_moderator)],
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
        rejection_reason=body.rejection_reason,
    )
    return schemas.RecipeDraftResponse.model_validate(updated)
