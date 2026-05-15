import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.deps import get_current_user
from app.core.exceptions import InvalidCredentialsError
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service, email_service
from app.services.auth_service import DeactivatedUserError
from app.services.google_auth_service import (
    GoogleAuthError,
    authenticate_or_create_google_user,
    exchange_code_for_user_info,
)

logger = logging.getLogger(__name__)
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
    user = await auth_service.register_user(
        db,
        email=user_in.email,
        username=user_in.username,
        display_name=user_in.display_name,
        password=user_in.password,
        language=user_in.language,
    )
    # Fire-and-forget verification email — does not block registration
    try:
        raw_token = await auth_service.request_email_verification(db, user=user)
        asyncio.create_task(email_service.send_verification_email(user, raw_token))
    except Exception:
        logger.debug("Could not schedule verification email after registration")
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
    access_token, refresh_token = await auth_service.login(
        db, login=form_data.username, password=form_data.password
    )
    return schemas.TokenPair(access_token=access_token, refresh_token=refresh_token)


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
    access_token, refresh_token = await auth_service.rotate_refresh_token(
        db, refresh_token_str=body.refresh_token
    )
    return schemas.TokenPair(access_token=access_token, refresh_token=refresh_token)


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
        raise InvalidCredentialsError(str(e)) from e

    user = await authenticate_or_create_google_user(db, google_user_info=google_user_info)

    if not user.is_active:
        raise DeactivatedUserError("User account is deactivated")

    access_token, refresh_token = await auth_service.create_token_pair(db, user=user)
    return schemas.TokenPair(access_token=access_token, refresh_token=refresh_token)


# Email verification endpoints
@router.post(
    "/send-verification-email",
    status_code=status.HTTP_200_OK,
    operation_id="send_verification_email",
)
async def send_verification_email(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    """Send (or re-send) an email verification link to the current user."""
    raw_token = await auth_service.request_email_verification(db, user=current_user)
    asyncio.create_task(email_service.send_verification_email(current_user, raw_token))
    return {"message": "Verification email sent"}


@router.post(
    "/verify-email",
    response_model=schemas.UserResponse,
    operation_id="verify_email",
)
async def verify_email(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: schemas.VerifyEmailRequest,
) -> schemas.UserResponse:
    """Confirm email ownership using the token from the verification link."""
    user = await auth_service.verify_email_token(db, token=body.token)
    return schemas.UserResponse.model_validate(user)


# Password reset endpoints
@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    operation_id="forgot_password",
)
async def forgot_password(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: schemas.ForgotPasswordRequest,
) -> dict[str, str]:
    """Request a password reset email.

    Always returns 200 to prevent email enumeration.
    If the account was registered via Google, returns detail='google_auth_user'.
    """
    raw_token = await auth_service.request_password_reset(db, email=str(body.email))
    if raw_token is not None:
        # Load the user again to get the object for the email (request_password_reset
        # doesn't return it to avoid extra query in the not-found branch)
        from app.services.user_service import get_user_by_email  # local import

        user = await get_user_by_email(db, email=str(body.email))
        if user is not None:
            asyncio.create_task(email_service.send_password_reset_email(user, raw_token))
    return {"message": "If the account exists, a reset email has been sent"}


@router.post(
    "/reset-password",
    response_model=schemas.UserResponse,
    operation_id="reset_password",
)
async def reset_password(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    body: schemas.ResetPasswordRequest,
) -> schemas.UserResponse:
    """Apply a new password using the reset token from the email link."""
    user = await auth_service.reset_password(
        db, token=body.token, new_password=body.new_password
    )
    return schemas.UserResponse.model_validate(user)
