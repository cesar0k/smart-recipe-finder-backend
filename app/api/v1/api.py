from fastapi import APIRouter

from .endpoints import (
    auth,
    comments,
    favorites,
    follows,
    health,
    moderation,
    notifications,
    ratings,
    recipes,
    users,
)
from .ws import notifications as ws_notifications

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(recipes.router, prefix="/recipes", tags=["recipes"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(favorites.router, prefix="/favorites", tags=["favorites"])
api_router.include_router(follows.router, prefix="/follows", tags=["follows"])
api_router.include_router(ratings.router, prefix="/ratings", tags=["ratings"])
api_router.include_router(comments.router, prefix="/comments", tags=["comments"])
api_router.include_router(moderation.router, prefix="/moderation", tags=["moderation"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(ws_notifications.router, prefix="/ws", tags=["websocket"])
