from fastapi import APIRouter

from .endpoints import auth, moderation, notifications, recipes, users

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(recipes.router, prefix="/recipes", tags=["recipes"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(
    moderation.router, prefix="/moderation", tags=["moderation"]
)
api_router.include_router(
    notifications.router, prefix="/notifications", tags=["notifications"]
)
