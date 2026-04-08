from collections.abc import Sequence

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.moderation_log import ModerationLog


async def create_log(
    db: AsyncSession,
    *,
    moderator_id: int,
    action: str,
    recipe_id: int | None = None,
    draft_id: int | None = None,
    reason: str | None = None,
    recipe_title: str | None = None,
    moderator_username: str | None = None,
) -> ModerationLog:
    """Create a moderation audit log entry."""
    log = ModerationLog(
        recipe_id=recipe_id,
        draft_id=draft_id,
        moderator_id=moderator_id,
        action=action,
        reason=reason,
        recipe_title=recipe_title,
        moderator_username=moderator_username,
    )
    db.add(log)
    await db.flush()
    return log


async def get_history(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    recipe_id: int | None = None,
    search: str | None = None,
) -> Sequence[ModerationLog]:
    query = select(ModerationLog).order_by(ModerationLog.created_at.desc())
    if recipe_id is not None:
        query = query.where(ModerationLog.recipe_id == recipe_id)
    if search:
        query = query.where(ModerationLog.recipe_title.ilike(f"%{search}%"))
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def delete_log(
    db: AsyncSession, *, log_id: int
) -> bool:
    """Delete a single moderation log entry. Returns True if deleted."""
    result = await db.execute(
        select(ModerationLog).where(ModerationLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    if log is None:
        return False
    await db.delete(log)
    await db.commit()
    return True


async def delete_all_logs(db: AsyncSession) -> int:
    """Delete all moderation log entries. Returns count of deleted."""
    stmt = sa_delete(ModerationLog)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount  # type: ignore[return-value]
