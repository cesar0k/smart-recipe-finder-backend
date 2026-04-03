from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import user_service

router = APIRouter()


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

    deleted = await user_service.delete_user(db=db, db_user=db_user)
    return schemas.UserResponse.model_validate(deleted)
