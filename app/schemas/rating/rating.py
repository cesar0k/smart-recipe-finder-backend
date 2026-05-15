from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RatingCreate(BaseModel):
    rating: int = Field(ge=1, le=5)


class RatingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recipe_id: int
    user_id: int
    rating: int
    created_at: datetime
    updated_at: datetime
