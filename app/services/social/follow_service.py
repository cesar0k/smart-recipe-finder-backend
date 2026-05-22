"""User follow service: subscribe / unsubscribe, list, batch-lookup helpers.

Pattern mirrors favorite_service.py exactly.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import Cache
from app.core.exceptions import InvalidStateError, NotFoundError
from app.models.auth.user import User
from app.models.social.user_follow import UserFollow
from app.services.recipe import cache_keys
async def _load_user(db: AsyncSession, *, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _recompute_followers_count(db: AsyncSession, *, user_id: int) -> int:
    """Recount and persist ``users.followers_count`` for one user."""
    count_q = select(func.count()).where(UserFollow.followed_id == user_id)
    new_count = (await db.execute(count_q)).scalar_one()
    await db.execute(
        update(User).where(User.id == user_id).values(followers_count=new_count)
    )
    return int(new_count)


async def add_follow(
    db: AsyncSession,
    *,
    user: User,
    followed_id: int,
    cache: Cache | None = None,
) -> User:
    """Idempotently follow ``followed_id``.

    Raises NotFoundError if target doesn't exist, InvalidStateError when
    trying to follow yourself.
    Returns the reloaded target user with updated followers_count.
    """
    if user.id == followed_id:
        raise InvalidStateError("Cannot follow yourself")

    target = await _load_user(db, user_id=followed_id)
    if target is None or not target.is_active:
        raise NotFoundError("User not found")

    stmt = (
        pg_insert(UserFollow)
        .values(follower_id=user.id, followed_id=followed_id)
        .on_conflict_do_nothing(index_elements=["follower_id", "followed_id"])
    )
    await db.execute(stmt)

    new_count = await _recompute_followers_count(db, user_id=followed_id)
    await db.commit()

    # Send in-app + email notification to the followed user (fire-and-forget)
    from app.services.notification import notification_service
    await notification_service.notify_and_broadcast(
        db,
        user_id=followed_id,
        type="user_followed",
        title=user.display_name or user.username,
        message=user.username,
        recipe_id=None,
        from_user_id=user.id,
    )
    await db.commit()

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=followed_id)

    reloaded = await _load_user(db, user_id=followed_id)
    if reloaded is not None:
        reloaded.followers_count = new_count
        return reloaded
    target.followers_count = new_count
    return target


async def remove_follow(
    db: AsyncSession,
    *,
    user: User,
    followed_id: int,
    cache: Cache | None = None,
) -> User:
    """Idempotently unfollow. Missing row is a no-op.

    Returns the reloaded target user.
    """
    target = await _load_user(db, user_id=followed_id)
    if target is None:
        raise NotFoundError("User not found")

    await db.execute(
        delete(UserFollow).where(
            UserFollow.follower_id == user.id,
            UserFollow.followed_id == followed_id,
        )
    )

    new_count = await _recompute_followers_count(db, user_id=followed_id)
    await db.commit()

    if cache is not None:
        await cache_keys.invalidate_on_user_change(cache, user_id=followed_id)

    reloaded = await _load_user(db, user_id=followed_id)
    if reloaded is not None:
        reloaded.followers_count = new_count
        return reloaded
    target.followers_count = new_count
    return target


async def get_followers(
    db: AsyncSession,
    *,
    user_id: int,
    skip: int = 0,
    limit: int = 50,
) -> Sequence[User]:
    """Return users who follow user_id, most-recent first."""
    query = (
        select(User)
        .join(UserFollow, UserFollow.follower_id == User.id)
        .where(UserFollow.followed_id == user_id, User.is_active == True)  # noqa: E712
        .order_by(UserFollow.created_at.desc(), User.id.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_following(
    db: AsyncSession,
    *,
    user_id: int,
    skip: int = 0,
    limit: int = 50,
) -> Sequence[User]:
    """Return users that user_id follows, most-recent first."""
    query = (
        select(User)
        .join(UserFollow, UserFollow.followed_id == User.id)
        .where(UserFollow.follower_id == user_id, User.is_active == True)  # noqa: E712
        .order_by(UserFollow.created_at.desc(), User.id.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_follower_ids(db: AsyncSession, *, user_id: int) -> set[int]:
    """Return all follower IDs for user_id (for bulk notifications)."""
    result = await db.execute(
        select(UserFollow.follower_id).where(UserFollow.followed_id == user_id)
    )
    return {row[0] for row in result.all()}


async def is_following(
    db: AsyncSession,
    *,
    follower_id: int,
    followed_id: int,
) -> bool:
    """Single check: does follower_id follow followed_id?"""
    result = await db.execute(
        select(UserFollow.follower_id).where(
            UserFollow.follower_id == follower_id,
            UserFollow.followed_id == followed_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_following_ids_for_viewer(
    db: AsyncSession,
    *,
    viewer_id: int,
    user_ids: list[int],
) -> set[int]:
    """Batch check: which of user_ids does viewer_id follow?

    Mirrors get_favorited_recipe_ids from favorite_service.
    """
    if not user_ids:
        return set()
    result = await db.execute(
        select(UserFollow.followed_id).where(
            UserFollow.follower_id == viewer_id,
            UserFollow.followed_id.in_(user_ids),
        )
    )
    return {row[0] for row in result.all()}
