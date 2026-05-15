from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import get_current_user, get_current_user_optional
from app.core.cache import Cache, get_cache
from app.db.session import get_db
from app.models.user import User
from app.services import comment_service

router = APIRouter()


@router.get(
    "/{recipe_id}",
    response_model=list[schemas.CommentResponse],
    operation_id="list_recipe_comments",
)
async def list_comments(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _current_user: Annotated[User | None, Depends(get_current_user_optional)],
    recipe_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[schemas.CommentResponse]:
    """List top-level comments with their replies for a recipe."""
    return await comment_service.get_comments(db, recipe_id=recipe_id, skip=skip, limit=limit)


@router.post(
    "/{recipe_id}",
    response_model=schemas.CommentResponse,
    status_code=201,
    operation_id="create_recipe_comment",
)
async def create_comment(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    recipe_id: int,
    body: schemas.CommentCreate,
) -> schemas.CommentResponse:
    """Post a comment or reply on a recipe."""
    return await comment_service.create_comment(
        db,
        user=current_user,
        recipe_id=recipe_id,
        content=body.content,
        parent_comment_id=body.parent_comment_id,
        cache=cache,
    )


@router.delete(
    "/{comment_id}",
    status_code=204,
    operation_id="delete_recipe_comment",
)
async def delete_comment(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    comment_id: int,
) -> None:
    """Soft-delete a comment (owner or moderator/admin only)."""
    await comment_service.soft_delete_comment(
        db, user=current_user, comment_id=comment_id, cache=cache
    )


@router.post(
    "/{comment_id}/report",
    status_code=204,
    operation_id="report_recipe_comment",
)
async def report_comment(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    comment_id: int,
    body: schemas.CommentReportCreate,
) -> None:
    """Report a comment as abusive or spam. Notifies moderators."""
    await comment_service.report_comment(
        db, reporter=current_user, comment_id=comment_id, reason=body.reason
    )
