from pydantic import BaseModel, HttpUrl


class RecipeImagesDelete(BaseModel):
    image_urls: list[HttpUrl]
