from pydantic import BaseModel, ConfigDict, Field


class RecipeTagsPublic(BaseModel):
    """Public-facing tag data returned in Recipe responses.

    All fields are optional — NULL means tags haven't been generated yet
    (background task pending or LLM classification failed).
    """

    model_config = ConfigDict(from_attributes=True)

    vegetarian: bool | None = None
    vegan: bool | None = None
    gluten_free: bool | None = None
    dairy_free: bool | None = None
    meal_type: str | None = None
    main_protein: str | None = None
    allergens: list[str] = Field(default_factory=list)
    cooking_method: str | None = None
    spice_level: str | None = None
    occasion: str | None = None
    cost_tier: str | None = None
    technique_difficulty: str | None = None
    cultural_sub_region: str | None = None
