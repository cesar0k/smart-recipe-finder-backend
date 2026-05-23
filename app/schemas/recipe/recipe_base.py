from pydantic import BaseModel, Field, field_validator


class RecipeBase(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=255)
    description: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=50000)
    cooking_time_in_minutes: int | None = None
    difficulty: str | None = Field(None, max_length=50)
    cuisine: str | None = Field(None, max_length=50)

    @field_validator("difficulty", mode="before")
    @classmethod
    def _normalize_difficulty(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower()
        return v
