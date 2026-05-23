import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.cache import Cache
from app.core.config import settings
from app.core.exceptions import (
    InvalidCredentialsError,
    InvalidStateError,
    NotAuthorizedError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token_value,
    hash_password,
    verify_password,
)
from app.models._base.enums import UserLanguage
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.user import User
from app.services.recipe import cache_keys
from app.services.social.user_service import (
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
    language: str = "ru",
) -> User:
    """Create a new user with hashed password.

    Raises ValidationError if email or username already taken.
    """
    existing = await get_user_by_email(db, email=email)
    if existing:
        raise ValidationError("Email already registered")

    existing = await get_user_by_username(db, username=username)
    if existing:
        raise ValidationError("Username already taken")

    user = User(
        email=email,
        username=username,
        display_name=display_name,
        hashed_password=hash_password(password),
        role="user",
        language=language if language in ("ru", "en") else "ru",
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
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db_refresh = RefreshToken(
        user_id=user.id,
        token=refresh_value,
        expires_at=expires_at,
    )
    db.add(db_refresh)
    await db.commit()

    return access_token, refresh_value


class DeactivatedUserError(NotAuthorizedError):
    """Raised when an action is attempted for a deactivated user (HTTP 403)."""


async def refresh_tokens(
    db: AsyncSession,
    *,
    refresh_token_str: str,
) -> tuple[str, str] | None:
    """Validate refresh token, rotate it, return new pair."""
    result = await db.execute(select(RefreshToken).where(RefreshToken.token == refresh_token_str))
    db_token = result.scalar_one_or_none()

    if db_token is None:
        return None

    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await db.delete(db_token)
        await db.commit()
        return None

    user_result = await db.execute(select(User).where(User.id == db_token.user_id))
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


async def login(
    db: AsyncSession,
    *,
    login: str,
    password: str,
) -> tuple[str, str]:
    """Authenticate by email/username + password and return token pair.

    Raises InvalidCredentialsError or DeactivatedUserError.
    """
    user = await authenticate_user(db, login=login, password=password)
    if user is None:
        raise InvalidCredentialsError("Invalid email or password")
    if not user.is_active:
        raise DeactivatedUserError("User account is deactivated")
    return await create_token_pair(db, user=user)


async def rotate_refresh_token(
    db: AsyncSession,
    *,
    refresh_token_str: str,
) -> tuple[str, str]:
    """Validate and rotate refresh token.

    Raises InvalidCredentialsError on missing/expired token,
    DeactivatedUserError when the underlying user is inactive.
    """
    pair = await refresh_tokens(db, refresh_token_str=refresh_token_str)
    if pair is None:
        raise InvalidCredentialsError("Invalid or expired refresh token")
    return pair


async def logout(
    db: AsyncSession,
    *,
    refresh_token_str: str,
) -> bool:
    """Delete refresh token from DB."""
    result = await db.execute(select(RefreshToken).where(RefreshToken.token == refresh_token_str))
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
    language: str | None = None,
    cache: Cache | None = None,
) -> tuple[User, str | None]:
    """Update the current user's profile (self-edit).

    When `email` differs from current, initiates a pending-email-change flow
    instead of immediately replacing the address. Returns (user, raw_token)
    where raw_token is not None when a confirmation email must be sent.

    Raises ValidationError on username / email collision.
    """
    if display_name is not None:
        user.display_name = display_name

    if language is not None:
        user.language = UserLanguage(language)

    if username is not None and username != user.username:
        existing = await get_user_by_username(db, username=username)
        if existing and existing.id != user.id:
            raise ValidationError("Username already taken")
        user.username = username

    pending_email_token: str | None = None
    if email is not None and email != user.email:
        # Start pending email change — don't update email directly
        pending_email_token = await request_email_change(db, user=user, new_email=email)
        # request_email_change commits and refreshes user; don't double-commit below
        if cache is not None:
            await cache_keys.invalidate_on_user_change(cache, user_id=user.id)
        return user, pending_email_token

    db.add(user)
    await db.commit()
    await db.refresh(user)

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=user.id)
    return user, None


async def change_password(
    db: AsyncSession,
    *,
    user: User,
    old_password: str,
    new_password: str,
) -> bool:
    """Change password for local auth users. Returns True on success.

    Raises InvalidStateError when user has no local-auth password or
    old password is wrong.
    """
    if user.auth_provider != "local":
        raise InvalidStateError("password_change_not_available")

    if user.hashed_password is None:
        raise InvalidStateError("password_not_set")

    if not verify_password(old_password, user.hashed_password):
        raise InvalidStateError("password_incorrect")

    user.hashed_password = hash_password(new_password)
    db.add(user)
    await db.commit()
    return True


# Email verification
_VERIFICATION_RATE_LIMIT_SECONDS = 120  # min seconds between re-send requests


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).

    PostgreSQL DateTime columns are stored without timezone; passing an
    offset-aware datetime causes asyncpg to raise DataError.
    """
    return datetime.utcnow()


async def request_email_verification(
    db: AsyncSession,
    *,
    user: User,
) -> str:
    """Generate a verification token, store its hash in the user row, and
    return the raw token so the caller can send it via email.

    Rate-limited: raises ValidationError if a token was already sent within
    the last 2 minutes.
    Only applicable to local-auth users; Google users are always verified.
    """
    if user.email_verified:
        raise ValidationError("email_already_verified")

    now = _utcnow()
    if user.email_verification_sent_at is not None:
        elapsed = (now - user.email_verification_sent_at).total_seconds()
        if elapsed < _VERIFICATION_RATE_LIMIT_SECONDS:
            raise ValidationError("verification_email_rate_limited")

    raw_token = secrets.token_urlsafe(32)
    user.email_verification_token = _hash_token(raw_token)
    user.email_verification_sent_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return raw_token


async def verify_email_token(db: AsyncSession, *, token: str) -> User:
    """Verify the raw token sent to the user's current email.

    Sets email_verified=True and clears the token fields.
    Also handles pending-email-change confirmation (see confirm_pending_email).
    Raises ValidationError on invalid / expired token.
    """
    hashed = _hash_token(token)

    # Check if this is a pending-email-change token first
    result = await db.execute(select(User).where(User.pending_email_token == hashed))
    user = result.scalar_one_or_none()
    if user is not None:
        return await _apply_pending_email(db, user)

    # Regular email verification token
    result = await db.execute(select(User).where(User.email_verification_token == hashed))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValidationError("invalid_verification_token")

    if user.email_verification_sent_at is not None:
        expire_hours = settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS
        elapsed = (_utcnow() - user.email_verification_sent_at).total_seconds()
        if elapsed > expire_hours * 3600:
            raise ValidationError("verification_token_expired")

    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# Pending email change
async def request_email_change(
    db: AsyncSession,
    *,
    user: User,
    new_email: str,
) -> str:
    """Start an email-change flow: store the new address as pending and return
    a raw token to be emailed to the new address for confirmation.

    Raises ValidationError if the new email is already taken by another account.
    """
    # Check uniqueness against current email column
    existing = await get_user_by_email(db, email=new_email)
    if existing and existing.id != user.id:
        raise ValidationError("Email already registered")

    # Rate-limit: reuse email_verification_sent_at
    now = _utcnow()
    if user.email_verification_sent_at is not None:
        elapsed = (now - user.email_verification_sent_at).total_seconds()
        if elapsed < _VERIFICATION_RATE_LIMIT_SECONDS:
            raise ValidationError("verification_email_rate_limited")

    raw_token = secrets.token_urlsafe(32)
    user.pending_email = new_email
    user.pending_email_token = _hash_token(raw_token)
    user.email_verification_sent_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return raw_token


async def _apply_pending_email(db: AsyncSession, user: User) -> User:
    """Apply the confirmed pending email change."""
    if not user.pending_email:
        raise ValidationError("invalid_verification_token")
    user.email = user.pending_email
    user.pending_email = None
    user.pending_email_token = None
    user.email_verification_sent_at = None
    user.email_verified = True
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# Password reset
async def request_password_reset(db: AsyncSession, *, email: str) -> str | None:
    """Generate a password reset token for a local-auth user.

    Returns the raw token string on success, or None if email not found
    (caller should respond with 200 regardless — prevents email enumeration).

    Raises ValidationError("google_auth_user") for Google-only accounts.
    """
    user = await get_user_by_email(db, email=email)
    if user is None:
        return None  # Silent — no enumeration

    if user.auth_provider != "local":
        raise ValidationError("google_auth_user")

    raw_token = secrets.token_urlsafe(32)
    expire_hours = settings.PASSWORD_RESET_TOKEN_EXPIRE_HOURS
    user.password_reset_token = _hash_token(raw_token)
    user.password_reset_expires_at = _utcnow() + timedelta(hours=expire_hours)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return raw_token


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> User:
    """Apply a password reset using the raw token from the email link.

    Raises ValidationError on invalid / expired token.
    """
    hashed = _hash_token(token)
    result = await db.execute(select(User).where(User.password_reset_token == hashed))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValidationError("invalid_reset_token")

    if user.password_reset_expires_at is None:
        raise ValidationError("invalid_reset_token")

    if _utcnow() > user.password_reset_expires_at:
        raise ValidationError("reset_token_expired")

    user.hashed_password = hash_password(new_password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
