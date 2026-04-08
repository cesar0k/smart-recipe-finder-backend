from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class ModerationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recipe_id: int | None = None
    draft_id: int | None = None
    moderator_id: int
    action: str
    reason: str | None = None
    recipe_title: str | None = None
    moderator_username: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime, _info: object) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
