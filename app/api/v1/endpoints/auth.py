from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.db.session import get_db
from app.services import auth_service
from app.services.auth_service import DeactivatedUserError
from app.services.google_auth_service import (
    GoogleAuthError,
    authenticate_or_create_google_user,
    exchange_code_for_user_info,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=schemas.UserResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="register_user",
)
async def register(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_in: schemas.UserCreate,
) -> schemas.UserResponse:
    try:
        user = await auth_service.register_user(
            db,
            email=user_in.email,
            username=user_in.username,
            display_name=user_in.display_name,
            password=user_in.password,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    return schemas.UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=schemas.TokenPair,
    operation_id="login_user",
)
async def login(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> schemas.TokenPair:
    user = await auth_service.authenticate_user(
        db,
        login=form_data.username,
        password=form_data.password,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    access_token, refresh_token = await auth_service.create_token_pair(
        db, user=user
    )
    return schemas.TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post(
    "/refresh",
    response_model=schemas.TokenPair,
    operation_id="refresh_tokens",
)
async def refresh(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: schemas.RefreshRequest,
) -> schemas.TokenPair:
    try:
        result = await auth_service.refresh_tokens(
            db, refresh_token_str=body.refresh_token
        )
    except DeactivatedUserError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    access_token, refresh_token = result
    return schemas.TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    operation_id="logout_user",
)
async def logout(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: schemas.RefreshRequest,
) -> dict[str, str]:
    await auth_service.logout(db, refresh_token_str=body.refresh_token)
    return {"message": "Successfully logged out"}


@router.post(
    "/google",
    response_model=schemas.TokenPair,
    operation_id="google_auth",
)
async def google_auth(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: schemas.GoogleAuthCode,
) -> schemas.TokenPair:
    try:
        google_user_info = await exchange_code_for_user_info(
            code=body.code, redirect_uri=body.redirect_uri
        )
    except GoogleAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    user = await authenticate_or_create_google_user(
        db, google_user_info=google_user_info
    )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    access_token, refresh_token = await auth_service.create_token_pair(
        db, user=user
    )
    return schemas.TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
    )
