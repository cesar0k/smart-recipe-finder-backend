import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import schemas
from app.api.deps import get_current_user, get_current_user_optional, require_admin
from app.core.cache import Cache, get_cache
from app.core.exceptions import ValidationError
from app.db.session import get_db
from app.models.notification.email_notification_preference import EmailNotificationPreference
from app.models.auth.user import User
from app.services.auth import auth_service
from app.services.notification import email_service
from app.services.social import user_service
logger = logging.getLogger(__name__)
router = APIRouter()


# --- Public endpoints ---


@router.get(
    "/search",
    response_model=list[schemas.PublicUserResponse],
    operation_id="search_users",
)
async def search_users(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(..., min_length=1, max_length=100, description="Search by username"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
) -> list[schemas.PublicUserResponse]:
    """Search users by username. Public endpoint."""
    results = await user_service.search_users(db, query=q, skip=skip, limit=limit)
    return [schemas.PublicUserResponse(**r) for r in results]


@router.get(
    "/{user_id}/profile",
    response_model=schemas.PublicUserResponse,
    operation_id="get_user_profile",
)
async def get_user_profile(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    viewer: Annotated[User | None, Depends(get_current_user_optional)],
    user_id: int,
) -> schemas.PublicUserResponse:
    """Get public user profile. Optional auth — adds is_following for viewer."""
    return await user_service.get_public_profile_cached(
        db,
        user_id=user_id,
        cache=cache,
        viewer_user_id=viewer.id if viewer else None,
    )


# --- Current user endpoints (authenticated) ---


@router.get(
    "/me",
    response_model=schemas.UserResponse,
    operation_id="get_current_user_info",
)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> schemas.UserResponse:
    return schemas.UserResponse.model_validate(current_user)


@router.patch(
    "/me",
    response_model=schemas.UserResponse,
    operation_id="update_current_user",
)
async def update_me(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    body: schemas.UserSelfUpdate,
) -> schemas.UserResponse:
    updated, pending_token = await auth_service.update_user_profile(
        db,
        cache=cache,
        user=current_user,
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        language=body.language,
    )
    if pending_token is not None:
        # Send confirmation to the new (pending) email address
        pending_addr = updated.pending_email
        if pending_addr:
            asyncio.create_task(
                email_service.send_email_change_confirmation(updated, pending_token, pending_addr)
            )
    return schemas.UserResponse.model_validate(updated)


@router.post(
    "/me/avatar",
    response_model=schemas.UserResponse,
    operation_id="upload_avatar",
)
async def upload_avatar(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
) -> schemas.UserResponse:
    """Upload or replace user avatar."""
    updated = await user_service.upload_avatar(db, cache=cache, user=current_user, file=file)
    return schemas.UserResponse.model_validate(updated)


@router.post(
    "/me/change-password",
    operation_id="change_password",
)
async def change_password(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    body: schemas.PasswordChange,
) -> dict[str, str]:
    await auth_service.change_password(
        db,
        user=current_user,
        old_password=body.old_password,
        new_password=body.new_password,
    )
    return {"message": "Password changed successfully"}


@router.get(
    "/me/email-preferences",
    response_model=list[schemas.EmailPrefResponse],
    operation_id="get_email_preferences",
)
async def get_email_preferences(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[schemas.EmailPrefResponse]:
    """Return all email notification preferences for the current user.

    Types with no explicit row default to enabled=True.
    Returns the full list of known types merged with any saved preferences.
    """
    result = await db.execute(
        select(EmailNotificationPreference).where(
            EmailNotificationPreference.user_id == current_user.id
        )
    )
    saved = {p.type: p.enabled for p in result.scalars().all()}

    prefs = [
        schemas.EmailPrefResponse(type=t, enabled=saved.get(t, True))
        for t in schemas.EMAIL_NOTIFICATION_TYPES
    ]
    return prefs


@router.put(
    "/me/email-preferences",
    response_model=schemas.EmailPrefResponse,
    operation_id="update_email_preference",
)
async def update_email_preference(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    body: schemas.EmailPrefUpdate,
) -> schemas.EmailPrefResponse:
    """Create or update a single email notification preference."""
    if body.type not in schemas.EMAIL_NOTIFICATION_TYPES:
        raise ValidationError(f"Unknown notification type: {body.type}")

    stmt = (
        pg_insert(EmailNotificationPreference)
        .values(user_id=current_user.id, type=body.type, enabled=body.enabled)
        .on_conflict_do_update(
            constraint="uq_email_prefs_user_type",
            set_={"enabled": body.enabled},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return schemas.EmailPrefResponse(type=body.type, enabled=body.enabled)


# --- Admin-only endpoints ---


@router.get(
    "/",
    response_model=list[schemas.UserResponse],
    operation_id="list_users",
)
async def list_users(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[schemas.UserResponse]:
    users = await user_service.get_all_users(db=db, skip=skip, limit=limit)
    return [schemas.UserResponse.model_validate(u) for u in users]


@router.get(
    "/{user_id}",
    response_model=schemas.UserResponse,
    operation_id="get_user",
)
async def get_user(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(require_admin)],
    user_id: int,
) -> schemas.UserResponse:
    user = await user_service.get_user_or_raise(db=db, user_id=user_id)
    return schemas.UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=schemas.UserResponse,
    operation_id="update_user",
)
async def update_user(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    admin: Annotated[User, Depends(require_admin)],
    user_id: int,
    user_in: schemas.UserUpdate,
) -> schemas.UserResponse:
    updated = await user_service.update_user(
        db=db,
        cache=cache,
        user_id=user_id,
        admin=admin,
        role=user_in.role,
        is_active=user_in.is_active,
    )
    return schemas.UserResponse.model_validate(updated)


@router.delete(
    "/{user_id}",
    response_model=schemas.UserResponse,
    operation_id="delete_user",
)
async def delete_user(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Cache, Depends(get_cache)],
    admin: Annotated[User, Depends(require_admin)],
    user_id: int,
) -> schemas.UserResponse:
    deleted = await user_service.delete_user(db=db, cache=cache, user_id=user_id, admin=admin)
    return schemas.UserResponse.model_validate(deleted)
