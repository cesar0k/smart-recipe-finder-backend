import logging

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.future import select

from app.core.security import decode_access_token
from app.core.ws_manager import ws_manager
from app.db.session import AsyncSessionLocal
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authenticate_ws(token: str) -> int | None:
    """Validate JWT and return user_id, or None if invalid."""
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            return None

    return user_id


@router.websocket("/notifications")
async def ws_notifications(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    """WebSocket endpoint for real-time notifications."""
    user_id = await _authenticate_ws(token)
    if user_id is None:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # Wait for client messages (heartbeat pings)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS error for user_id=%d", user_id)
    finally:
        ws_manager.disconnect(user_id, websocket)
