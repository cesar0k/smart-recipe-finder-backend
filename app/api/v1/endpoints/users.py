import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import get_current_user, require_admin
from app.core.s3_client import s3_client
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service, image_service, user_service

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
    user_id: int,
) -> schemas.PublicUserResponse:
    """Get public user profile. Public endpoint."""
    profile = await user_service.get_public_profile(db, user_id=user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="User not found")
    return schemas.PublicUserResponse(**profile)


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
    current_user: Annotated[User, Depends(get_current_user)],
    body: schemas.UserSelfUpdate,
) -> schemas.UserResponse:
    try:
        updated = await auth_service.update_user_profile(
            db,
            user=current_user,
            username=body.username,
            display_name=body.display_name,
            email=body.email,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
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
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
) -> schemas.UserResponse:
    """Upload or replace user avatar."""
    valid_content = await image_service.validate_and_process_image(file)

    filename = file.filename or ""
    extension = filename.split(".")[-1] if "." in filename else "jpg"
    obj_name = f"avatars/{current_user.id}/{uuid.uuid4()}.{extension}"
    content_type = file.content_type or "application/octet-stream"

    if current_user.avatar_url:
        await s3_client.delete_image_from_s3(current_user.avatar_url)

    url = await s3_client.upload_file(valid_content, obj_name, content_type)
    current_user.avatar_url = url
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return schemas.UserResponse.model_validate(current_user)


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
    try:
        await auth_service.change_password(
            db,
            user=current_user,
            old_password=body.old_password,
            new_password=body.new_password,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return {"message": "Password changed successfully"}


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
    user = await user_service.get_user_by_id(db=db, user_id=user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return schemas.UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=schemas.UserResponse,
    operation_id="update_user",
)
async def update_user(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[User, Depends(require_admin)],
    user_id: int,
    user_in: schemas.UserUpdate,
) -> schemas.UserResponse:
    db_user = await user_service.get_user_by_id(db=db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if db_user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify your own account via this endpoint",
        )

    if db_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify admin account",
        )

    updated = await user_service.update_user(
        db=db,
        db_user=db_user,
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
    admin: Annotated[User, Depends(require_admin)],
    user_id: int,
) -> schemas.UserResponse:
    db_user = await user_service.get_user_by_id(db=db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if db_user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    if db_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete admin account",
        )

    deleted = await user_service.delete_user(db=db, db_user=db_user)
    return schemas.UserResponse.model_validate(deleted)
