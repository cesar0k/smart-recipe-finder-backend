from pydantic import BaseModel, Field


class RecipeBase(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=255)
    description: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=50000)
    cooking_time_in_minutes: int | None = None
    difficulty: str | None = Field(None, max_length=50)
    cuisine: str | None = Field(None, max_length=50)
