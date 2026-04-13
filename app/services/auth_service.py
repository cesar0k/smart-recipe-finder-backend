from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token_value,
    hash_password,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.user_service import (
    get_user_by_email,
    get_user_by_username,
)


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    username: str,
    display_name: str | None = None,
    password: str,
) -> User:
    """Create a new user with hashed password.

    Raises ValueError if email or username already taken.
    """
    existing = await get_user_by_email(db, email=email)
    if existing:
        raise ValueError("Email already registered")

    existing = await get_user_by_username(db, username=username)
    if existing:
        raise ValueError("Username already taken")

    user = User(
        email=email,
        username=username,
        display_name=display_name,
        hashed_password=hash_password(password),
        role="user",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(
    db: AsyncSession,
    *,
    login: str,
    password: str,
) -> User | None:
    user = await get_user_by_email(db, email=login)
    if user is None:
        user = await get_user_by_username(db, username=login)

    if user is None:
        return None
    if user.hashed_password is None:
        # Google-only user — can't log in with password
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def create_token_pair(
    db: AsyncSession,
    *,
    user: User,
) -> tuple[str, str]:
    """Create access + refresh token pair."""
    access_token = create_access_token(user.id, user.role)

    refresh_value = create_refresh_token_value()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    db_refresh = RefreshToken(
        user_id=user.id,
        token=refresh_value,
        expires_at=expires_at,
    )
    db.add(db_refresh)
    await db.commit()

    return access_token, refresh_value


class DeactivatedUserError(Exception):
    """Raised when refresh is attempted for a deactivated user."""

    pass


async def refresh_tokens(
    db: AsyncSession,
    *,
    refresh_token_str: str,
) -> tuple[str, str] | None:
    """Validate refresh token, rotate it, return new pair."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token == refresh_token_str)
    )
    db_token = result.scalar_one_or_none()

    if db_token is None:
        return None

    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await db.delete(db_token)
        await db.commit()
        return None

    user_result = await db.execute(
        select(User).where(User.id == db_token.user_id)
    )
    user = user_result.scalar_one_or_none()

    if user is None:
        await db.delete(db_token)
        await db.commit()
        return None

    if not user.is_active:
        await db.delete(db_token)
        await db.commit()
        raise DeactivatedUserError()

    await db.delete(db_token)
    await db.commit()

    return await create_token_pair(db, user=user)


async def logout(
    db: AsyncSession,
    *,
    refresh_token_str: str,
) -> bool:
    """Delete refresh token from DB."""
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token == refresh_token_str)
    )
    db_token = result.scalar_one_or_none()

    if db_token is None:
        return False

    await db.delete(db_token)
    await db.commit()
    return True


async def update_user_profile(
    db: AsyncSession,
    *,
    user: User,
    username: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
) -> User:
    """Update the current user's profile (self-edit)."""
    if display_name is not None:
        user.display_name = display_name

    if username is not None and username != user.username:
        existing = await get_user_by_username(db, username=username)
        if existing and existing.id != user.id:
            raise ValueError("Username already taken")
        user.username = username

    if email is not None and email != user.email:
        existing = await get_user_by_email(db, email=email)
        if existing and existing.id != user.id:
            raise ValueError("Email already registered")
        user.email = email

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def change_password(
    db: AsyncSession,
    *,
    user: User,
    old_password: str,
    new_password: str,
) -> bool:
    """Change password for local auth users. Returns True on success."""
    if user.auth_provider != "local":
        raise ValueError("password_change_not_available")

    if user.hashed_password is None:
        raise ValueError("password_not_set")

    if not verify_password(old_password, user.hashed_password):
        raise ValueError("password_incorrect")

    user.hashed_password = hash_password(new_password)
    db.add(user)
    await db.commit()
    return True
