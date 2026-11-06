from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import require_moderator
from app.core.cache import Cache, get_cache
from app.db.session import get_db
from app.models.auth.user import User
from app.services.comment import comment_service
from app.services.moderation import moderation_log_service, moderation_service
from app.services.recipe import tag_service

router = APIRouter()


@router.get(
    "/pending-count",
    response_model=schemas.PendingCountResponse,
    operation_id="get_pending_count",
)
async def get_pending_count(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    _mod: Annotated[User, Depends(require_moderator)],
) -> schemas.PendingCountResponse:
    return await moderation_service.get_pending_count_cached(db, cache=cache)


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
    await moderation_log_service.delete_log(db, log_id=log_id)
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
    background_tasks: BackgroundTasks,
    recipe_id: int,
    body: schemas.ModerationAction,
) -> schemas.Recipe:
    updated = await moderation_service.moderate_recipe(
        db,
        cache=cache,
        recipe_id=recipe_id,
        action=body.action,
        moderator_id=mod.id,
        rejection_reason=body.rejection_reason,
    )
    if body.action == "approve":
        background_tasks.add_task(tag_service.classify_recipe_tags, recipe_id)
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
    background_tasks: BackgroundTasks,
    draft_id: int,
    body: schemas.ModerationAction,
) -> schemas.RecipeDraftResponse:
    updated = await moderation_service.moderate_draft(
        db,
        cache=cache,
        draft_id=draft_id,
        action=body.action,
        moderator_id=mod.id,
        rejection_reason=body.rejection_reason,
    )
    if body.action == "approve" and updated.recipe_id is not None:
        background_tasks.add_task(tag_service.classify_recipe_tags, updated.recipe_id)
    return schemas.RecipeDraftResponse.model_validate(updated)


@router.get(
    "/comments",
    response_model=list[schemas.ReportedCommentResponse],
    operation_id="list_reported_comments",
)
async def list_reported_comments(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[schemas.ReportedCommentResponse]:
    """List comments with active reports for moderation."""
    items = await comment_service.get_reported_comments(db, skip=skip, limit=limit)
    return [schemas.ReportedCommentResponse(**item) for item in items]


@router.post(
    "/comments/{comment_id}/dismiss",
    status_code=204,
    operation_id="dismiss_comment_reports",
)
async def dismiss_comment_reports(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _mod: Annotated[User, Depends(require_moderator)],
    comment_id: int,
) -> None:
    """Dismiss all reports for a comment (keep comment, clear reports)."""
    await comment_service.dismiss_comment_reports(db, comment_id=comment_id)
