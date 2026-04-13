from collections.abc import Sequence

from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.recipe import Recipe
from app.models.refresh_token import RefreshToken
from app.models.user import User


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


async def update_user(
    db: AsyncSession,
    *,
    db_user: User,
    role: str | None = None,
    is_active: bool | None = None,
) -> User:
    if role is not None:
        db_user.role = role
    if is_active is not None:
        db_user.is_active = is_active

        if is_active is False:
            await db.execute(
                sa_delete(RefreshToken).where(RefreshToken.user_id == db_user.id)
            )

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def delete_user(db: AsyncSession, *, db_user: User) -> User:
    await db.delete(db_user)
    await db.commit()
    return db_user


async def search_users(
    db: AsyncSession,
    *,
    query: str,
    skip: int = 0,
    limit: int = 20,
) -> list[dict]:
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
) -> dict | None:
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
