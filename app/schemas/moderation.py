from typing import Literal

from pydantic import BaseModel, Field


class ModerationAction(BaseModel):
    action: Literal["approve", "reject"]
    rejection_reason: str | None = Field(
        None,
        max_length=1000,
        description="Required when action is 'reject'",
    )
