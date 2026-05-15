from pydantic import BaseModel, ConfigDict

# All notification types that support per-user email preference.
EMAIL_NOTIFICATION_TYPES = [
    "new_comment",
    "comment_reply",
    "comment_reported",
    "new_pending_recipe",
    "recipe_approved",
    "recipe_rejected",
    "draft_approved",
    "draft_rejected",
    "recipe_deleted",
    "user_followed",
    "followed_user_published",
]


class EmailPrefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    enabled: bool


class EmailPrefUpdate(BaseModel):
    type: str
    enabled: bool
