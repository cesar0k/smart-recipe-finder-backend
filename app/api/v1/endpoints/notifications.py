from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.notification import NotificationResponse, UnreadCountResponse
from app.services import notification_service

router = APIRouter()


@router.get(
    "/",
    response_model=list[NotificationResponse],
    operation_id="list_notifications",
)
async def list_notifications(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[NotificationResponse]:
    notifs = await notification_service.get_user_notifications(
        db, user_id=current_user.id, skip=skip, limit=limit
    )
    return [NotificationResponse.model_validate(n) for n in notifs]


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    operation_id="get_unread_count",
)
async def get_unread_count(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UnreadCountResponse:
    count = await notification_service.get_unread_count(db, user_id=current_user.id)
    return UnreadCountResponse(count=count)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    operation_id="mark_notification_read",
)
async def mark_notification_read(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    notification_id: int,
) -> NotificationResponse:
    notif = await notification_service.mark_as_read(
        db, notification_id=notification_id, user_id=current_user.id
    )
    if notif is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationResponse.model_validate(notif)


@router.post(
    "/mark-all-read",
    operation_id="mark_all_notifications_read",
)
async def mark_all_read(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int]:
    count = await notification_service.mark_all_read(db, user_id=current_user.id)
    return {"marked": count}


@router.delete(
    "/{notification_id}",
    operation_id="delete_notification",
)
async def delete_notification(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    notification_id: int,
) -> dict[str, bool]:
    deleted = await notification_service.delete_notification(
        db, notification_id=notification_id, user_id=current_user.id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"deleted": True}


@router.delete(
    "/",
    operation_id="delete_all_notifications",
)
async def delete_all_notifications(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int]:
    count = await notification_service.delete_all_notifications(
        db, user_id=current_user.id
    )
    return {"deleted": count}
