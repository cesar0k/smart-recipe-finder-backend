from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    type: str
    title: str
    message: str
    is_read: bool
    recipe_id: int | None = None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime, _info: object) -> str:
        """Ensure created_at is serialized as UTC ISO with Z suffix."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
