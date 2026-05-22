"""Facade — re-exports each ORM class. Inside the codebase prefer the full
path (``from app.models.recipe.recipe import Recipe``) to keep the domain
visible at the call site."""

from app.models._base.base import Base
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.user import User
from app.models.comment.recipe_comment import RecipeComment
from app.models.comment.recipe_comment_report import RecipeCommentReport
from app.models.moderation.moderation_log import ModerationLog
from app.models.notification.email_notification_preference import EmailNotificationPreference
from app.models.notification.notification import Notification
from app.models.recipe.cuisine import Cuisine
from app.models.recipe.ingredient import Ingredient
from app.models.recipe.recipe import Recipe
from app.models.recipe.recipe_draft import RecipeDraft
from app.models.recipe.recipe_favorite import RecipeFavorite
from app.models.recipe.recipe_image import RecipeImage
from app.models.recipe.recipe_ingredient import RecipeIngredient
from app.models.recipe.recipe_rating import RecipeRating
from app.models.recipe.recipe_tags import RecipeTags
from app.models.social.user_follow import UserFollow

__all__ = [
    "Base",
    "Cuisine",
    "EmailNotificationPreference",
    "Ingredient",
    "ModerationLog",
    "Notification",
    "Recipe",
    "RecipeComment",
    "RecipeCommentReport",
    "RecipeDraft",
    "RecipeFavorite",
    "RecipeImage",
    "RecipeIngredient",
    "RecipeRating",
    "RecipeTags",
    "RefreshToken",
    "User",
    "UserFollow",
]
