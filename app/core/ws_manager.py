import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections per user."""

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)
        logger.info("WS connected: user_id=%d (total=%d)", user_id, len(self._connections[user_id]))

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        self._connections[user_id].discard(websocket)
        if not self._connections[user_id]:
            del self._connections[user_id]
        logger.info("WS disconnected: user_id=%d", user_id)

    async def send_to_user(self, user_id: int, data: dict) -> None:
        """Send JSON to all connections of a specific user."""
        connections = self._connections.get(user_id, set())
        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[user_id].discard(ws)

    async def broadcast(self, user_ids: list[int], data: dict) -> None:
        """Send JSON to multiple users."""
        for uid in user_ids:
            await self.send_to_user(uid, data)


ws_manager = ConnectionManager()
