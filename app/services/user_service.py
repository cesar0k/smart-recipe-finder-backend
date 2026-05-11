import logging
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app import schemas
from app.core.cache import Cache
from app.core.config import settings
from app.core.exceptions import (
    InvalidStateError,
    NotAuthorizedError,
    NotFoundError,
)
from app.core.s3_client import s3_client
from app.db.session import AsyncSessionLocal
from app.models.recipe import Recipe
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import cache_keys, image_service

# Google-CDN avatar URL substrings — referenced by the backfill script too.
GOOGLE_AVATAR_DOMAINS: tuple[str, ...] = ("googleusercontent.com", "googleapis.com")

logger = logging.getLogger(__name__)


class _RemoteImageError(Exception):
    """Raised for non-retryable failures while fetching a remote avatar."""


async def get_user_by_id(db: AsyncSession, *, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, *, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, *, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_all_users(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
) -> Sequence[User]:
    query = select(User).order_by(User.id.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


def _ensure_admin_can_modify(target: User, admin: User) -> None:
    """Domain rule: admin cannot modify themselves or other admins."""
    if target.id == admin.id:
        raise InvalidStateError("Cannot modify your own account via this endpoint")
    if target.role == "admin":
        raise NotAuthorizedError("Cannot modify admin account")


async def update_user(
    db: AsyncSession,
    *,
    user_id: int,
    admin: User,
    role: str | None = None,
    is_active: bool | None = None,
    cache: Cache | None = None,
) -> User:
    """Admin update of role / is_active for another user.

    Raises NotFoundError, InvalidStateError (self-modify), NotAuthorizedError (admin).
    """
    db_user = await get_user_by_id(db=db, user_id=user_id)
    if db_user is None:
        raise NotFoundError("User not found")
    _ensure_admin_can_modify(db_user, admin)

    if role is not None:
        db_user.role = role
    if is_active is not None:
        db_user.is_active = is_active

        if is_active is False:
            await db.execute(sa_delete(RefreshToken).where(RefreshToken.user_id == db_user.id))

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=user_id)
    return db_user


async def delete_user(
    db: AsyncSession,
    *,
    user_id: int,
    admin: User,
    cache: Cache | None = None,
) -> User:
    """Admin delete a user. Same authorization rules as update_user."""
    db_user = await get_user_by_id(db=db, user_id=user_id)
    if db_user is None:
        raise NotFoundError("User not found")
    _ensure_admin_can_modify(db_user, admin)

    await db.delete(db_user)
    await db.commit()

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=user_id)
    return db_user


async def get_user_or_raise(db: AsyncSession, *, user_id: int) -> User:
    """Same as get_user_by_id, but raises NotFoundError instead of returning None."""
    user = await get_user_by_id(db=db, user_id=user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user


async def search_users(
    db: AsyncSession,
    *,
    query: str,
    skip: int = 0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search active users by username. Returns list of dicts with recipe_count."""
    stmt = (
        select(
            User.id,
            User.username,
            User.display_name,
            User.avatar_url,
            User.role,
            User.created_at,
            sa_func.count(Recipe.id).label("recipe_count"),
        )
        .outerjoin(Recipe, (Recipe.owner_id == User.id) & (Recipe.status == "approved"))
        .where(
            User.is_active == True,  # noqa: E712
            User.username.ilike(f"%{query}%"),
        )
        .group_by(User.id)
        .order_by(User.username)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        {
            "id": row.id,
            "username": row.username,
            "display_name": row.display_name,
            "avatar_url": row.avatar_url,
            "role": row.role,
            "created_at": row.created_at,
            "recipe_count": row.recipe_count,
        }
        for row in result.all()
    ]


async def get_public_profile(
    db: AsyncSession,
    *,
    user_id: int,
) -> dict[str, Any] | None:
    """Get public profile: user info + approved recipe count."""
    stmt = (
        select(
            User.id,
            User.username,
            User.display_name,
            User.avatar_url,
            User.role,
            User.created_at,
            sa_func.count(Recipe.id).label("recipe_count"),
        )
        .outerjoin(Recipe, (Recipe.owner_id == User.id) & (Recipe.status == "approved"))
        .where(User.id == user_id, User.is_active == True)  # noqa: E712
        .group_by(User.id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "avatar_url": row.avatar_url,
        "role": row.role,
        "created_at": row.created_at,
        "recipe_count": row.recipe_count,
    }


async def get_public_profile_cached(
    db: AsyncSession,
    *,
    user_id: int,
    cache: Cache | None = None,
) -> schemas.PublicUserResponse:
    """Read-through cache wrapper around get_public_profile.

    Raises NotFoundError when the user does not exist.
    """
    key = cache_keys.user_profile(user_id)
    if cache is not None:
        cached = await cache.get_model(key, schemas.PublicUserResponse)
        if cached is not None:
            return cached

    profile = await get_public_profile(db, user_id=user_id)
    if profile is None:
        raise NotFoundError("User not found")

    response = schemas.PublicUserResponse(**profile)
    if cache is not None:
        await cache.set_model(key, response, ttl=cache_keys.TTL_USER_PROFILE)
    return response


async def upload_avatar(
    db: AsyncSession,
    *,
    user: User,
    file: UploadFile,
    cache: Cache | None = None,
) -> User:
    """Upload or replace user avatar.

    Validates and converts the image, deletes the old S3 object if present,
    uploads the new one, persists the URL, and invalidates the user profile cache.
    """
    valid_content = await image_service.validate_and_process_image(file)
    converted, content_type, extension = image_service.ensure_browser_compatible(
        valid_content.getvalue()
    )
    obj_name = f"avatars/{user.id}/{uuid.uuid4()}.{extension}"

    if user.avatar_url:
        await s3_client.delete_image_from_s3(user.avatar_url)

    url = await s3_client.upload_file(converted, obj_name, content_type)
    user.avatar_url = url
    db.add(user)
    await db.commit()
    await db.refresh(user)

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=user.id)
    return user


async def _fetch_remote_image_bytes(url: str) -> bytes:
    """Stream a remote image into memory, capped at GOOGLE_AVATAR_MAX_BYTES.

    Raises ``_RemoteImageError`` (non-retryable) for HTTP 4xx and oversize
    responses; lets ``httpx.HTTPError`` (timeouts, connect errors, 5xx)
    bubble up so the surrounding ``tenacity`` decorator can retry.
    """
    timeout = httpx.Timeout(settings.GOOGLE_AVATAR_FETCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            if 400 <= response.status_code < 500:
                raise _RemoteImageError(f"Remote image responded with {response.status_code}")
            response.raise_for_status()

            buf = bytearray()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > settings.GOOGLE_AVATAR_MAX_BYTES:
                    raise _RemoteImageError(
                        f"Remote image exceeds {settings.GOOGLE_AVATAR_MAX_BYTES} bytes"
                    )
            return bytes(buf)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
async def _fetch_remote_image_bytes_with_retry(url: str) -> bytes:
    return await _fetch_remote_image_bytes(url)


async def set_avatar_from_remote_url(
    db: AsyncSession,
    *,
    user: User,
    url: str,
    cache: Cache | None = None,
) -> User:
    """Best-effort: download a remote image, validate it, and store it in our S3.

    On any failure (network, validation, S3) the user is returned unchanged
    and a warning is logged. Used for Google profile pictures captured at
    OAuth registration and for the offline backfill script.
    """
    try:
        raw = await _fetch_remote_image_bytes_with_retry(url)
    except (_RemoteImageError, httpx.HTTPError) as exc:
        logger.warning(
            "set_avatar_from_remote_url: download failed for user %s url=%s: %s",
            user.id,
            url,
            exc,
        )
        return user

    try:
        validated = image_service.validate_image_bytes(raw)
        converted, content_type, extension = image_service.ensure_browser_compatible(validated)
    except HTTPException as exc:
        logger.warning(
            "set_avatar_from_remote_url: validation failed for user %s url=%s: %s",
            user.id,
            url,
            exc.detail,
        )
        return user

    obj_name = f"avatars/{user.id}/{uuid.uuid4()}.{extension}"
    previous_avatar = user.avatar_url

    try:
        new_url = await s3_client.upload_file(converted, obj_name, content_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_avatar_from_remote_url: S3 upload failed for user %s: %s", user.id, exc)
        return user

    user.avatar_url = new_url
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Best-effort cleanup of the previous avatar (only deletes if it points
    # at our own bucket — see s3_client.delete_image_from_s3 for the guard).
    if previous_avatar:
        await s3_client.delete_image_from_s3(previous_avatar)

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=user.id)

    return user


async def set_avatar_from_remote_url_background(user_id: int, url: str) -> None:
    """Run ``set_avatar_from_remote_url`` in its own DB session.

    Designed to be scheduled with ``asyncio.create_task`` after the
    originating HTTP response has been returned, so registration latency is
    not bound to Google CDN download speed. Errors are already swallowed by
    ``set_avatar_from_remote_url``; this wrapper only adds session
    management and a final safety-net log.
    """
    try:
        async with AsyncSessionLocal() as db:
            user = await get_user_by_id(db, user_id=user_id)
            if user is None:
                return
            await set_avatar_from_remote_url(db, user=user, url=url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "set_avatar_from_remote_url_background: crashed for user %s: %s",
            user_id,
            exc,
        )
