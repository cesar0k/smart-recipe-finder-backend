from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .ingredient import Ingredient


class RecipeDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recipe_id: int
    author_id: int
    title: str
    description: str | None = None
    instructions: str
    cooking_time_in_minutes: int
    difficulty: str
    cuisine: str | None = None
    ingredients: list[Ingredient] = Field(default_factory=list)
    status: str
    rejection_reason: str | None = None
    created_at: datetime
