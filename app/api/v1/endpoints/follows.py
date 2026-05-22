from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import get_current_user, get_current_user_optional
from app.core.cache import Cache, get_cache
from app.db.session import get_db
from app.models.auth.user import User
from app.services.social import follow_service
router = APIRouter()


class FollowCheckResponse(BaseModel):
    """Subset of the requested user IDs that the current user follows."""

    following_ids: list[int]


@router.post(
    "/{user_id}",
    response_model=schemas.PublicUserResponse,
    operation_id="follow_user",
)
async def follow_user(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    user_id: int,
) -> schemas.PublicUserResponse:
    """Idempotently follow a user. Returns updated target profile."""
    target = await follow_service.add_follow(
        db, user=current_user, followed_id=user_id, cache=cache
    )
    response = schemas.PublicUserResponse.model_validate(target)
    return response.model_copy(update={"is_following": True})


@router.delete(
    "/{user_id}",
    response_model=schemas.PublicUserResponse,
    operation_id="unfollow_user",
)
async def unfollow_user(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    user_id: int,
) -> schemas.PublicUserResponse:
    """Idempotently unfollow a user. Returns updated target profile."""
    target = await follow_service.remove_follow(
        db, user=current_user, followed_id=user_id, cache=cache
    )
    response = schemas.PublicUserResponse.model_validate(target)
    return response.model_copy(update={"is_following": False})


@router.get(
    "/{user_id}/followers",
    response_model=list[schemas.PublicUserResponse],
    operation_id="get_followers",
)
async def get_followers(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_current_user_optional)],
    user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[schemas.PublicUserResponse]:
    """List followers of a user. Optional auth for is_following."""
    followers = await follow_service.get_followers(db, user_id=user_id, skip=skip, limit=limit)
    if not followers:
        return []

    follower_dicts = [
        schemas.PublicUserResponse.model_validate(u) for u in followers
    ]

    if viewer:
        following_set = await follow_service.get_following_ids_for_viewer(
            db,
            viewer_id=viewer.id,
            user_ids=[u.id for u in followers],
        )
        return [
            r.model_copy(update={"is_following": r.id in following_set})
            for r in follower_dicts
        ]

    return follower_dicts


@router.get(
    "/{user_id}/following",
    response_model=list[schemas.PublicUserResponse],
    operation_id="get_following",
)
async def get_following(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_current_user_optional)],
    user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[schemas.PublicUserResponse]:
    """List users that user_id follows. Optional auth for is_following."""
    following = await follow_service.get_following(db, user_id=user_id, skip=skip, limit=limit)
    if not following:
        return []

    following_dicts = [
        schemas.PublicUserResponse.model_validate(u) for u in following
    ]

    if viewer:
        following_set = await follow_service.get_following_ids_for_viewer(
            db,
            viewer_id=viewer.id,
            user_ids=[u.id for u in following],
        )
        return [
            r.model_copy(update={"is_following": r.id in following_set})
            for r in following_dicts
        ]

    return following_dicts


@router.get(
    "/check",
    response_model=FollowCheckResponse,
    operation_id="check_following",
)
async def check_following(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    ids: str = Query(
        ...,
        description="Comma-separated user IDs to check (up to 200).",
        max_length=4000,
    ),
) -> FollowCheckResponse:
    """Batch check which of the given user IDs the current user follows."""
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

    if len(parsed) > 200:
        raise HTTPException(status_code=400, detail="too many ids (max 200)")

    if not parsed:
        return FollowCheckResponse(following_ids=[])

    following = await follow_service.get_following_ids_for_viewer(
        db, viewer_id=current_user.id, user_ids=parsed
    )
    return FollowCheckResponse(following_ids=sorted(following))
