import logging
from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult
from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.notification import Notification
from app.schemas.notification import NotificationResponse

logger = logging.getLogger(__name__)


async def _ws_notify_user(user_id: int, notif: Notification) -> None:
    """Send real-time WS notification. Best-effort — never raises."""
    try:
        from app.core.ws_manager import ws_manager

        notif_data = NotificationResponse.model_validate(notif).model_dump(mode="json")
        await ws_manager.send_to_user(user_id, {
            "type": "new_notification",
            "notification": notif_data,
        })
    except Exception:
        logger.debug("WS notify failed for user_id=%d (may not be connected)", user_id)


async def _ws_notify_users(user_ids: list[int], notifications: list[Notification]) -> None:
    """Send real-time WS notifications to multiple users. Best-effort."""
    try:
        from app.core.ws_manager import ws_manager

        for uid, notif in zip(user_ids, notifications):
            notif_data = NotificationResponse.model_validate(notif).model_dump(mode="json")
            await ws_manager.send_to_user(uid, {
                "type": "new_notification",
                "notification": notif_data,
            })
    except Exception:
        logger.debug("WS bulk notify failed")


async def notify_and_broadcast(
    db: AsyncSession,
    *,
    user_id: int,
    type: str,
    title: str,
    message: str,
    recipe_id: int | None = None,
) -> Notification:
    """Create notification, flush to DB, and send via WebSocket."""
    notif = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        recipe_id=recipe_id,
    )
    db.add(notif)
    await db.flush()
    await _ws_notify_user(user_id, notif)
    return notif


async def notify_bulk_and_broadcast(
    db: AsyncSession,
    *,
    user_ids: list[int],
    type: str,
    title: str,
    message: str,
    recipe_id: int | None = None,
) -> list[Notification]:
    """Create notifications for multiple users, flush, and send via WebSocket."""
    notifications = []
    for uid in user_ids:
        n = Notification(
            user_id=uid,
            type=type,
            title=title,
            message=message,
            recipe_id=recipe_id,
        )
        db.add(n)
        notifications.append(n)
    await db.flush()
    await _ws_notify_users(user_ids, notifications)
    return notifications


async def get_user_notifications(
    db: AsyncSession,
    *,
    user_id: int,
    skip: int = 0,
    limit: int = 50,
) -> Sequence[Notification]:
    query = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_unread_count(db: AsyncSession, *, user_id: int) -> int:
    query = select(sa_func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.is_read == False,  # noqa: E712
    )
    result = await db.execute(query)
    return result.scalar_one()


async def mark_as_read(
    db: AsyncSession, *, notification_id: int, user_id: int
) -> Notification | None:
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        return None
    notif.is_read = True
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    return notif


async def mark_all_read(db: AsyncSession, *, user_id: int) -> int:
    """Mark all notifications as read. Returns count of updated."""
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    result = cast(CursorResult[tuple[int, ...]], await db.execute(stmt))
    await db.commit()
    return result.rowcount


async def delete_notification(
    db: AsyncSession, *, notification_id: int, user_id: int
) -> bool:
    """Delete a single notification. Returns True if deleted."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        return False
    await db.delete(notif)
    await db.commit()
    return True


async def delete_all_notifications(db: AsyncSession, *, user_id: int) -> int:
    """Delete all notifications for a user. Returns count of deleted."""
    stmt = sa_delete(Notification).where(Notification.user_id == user_id)
    result = cast(CursorResult[tuple[int, ...]], await db.execute(stmt))
    await db.commit()
    return result.rowcount
