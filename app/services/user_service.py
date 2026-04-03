from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

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

    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def delete_user(db: AsyncSession, *, db_user: User) -> User:
    await db.delete(db_user)
    await db.commit()
    return db_user
