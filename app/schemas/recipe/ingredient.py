from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    name: str = Field(..., max_length=255)
