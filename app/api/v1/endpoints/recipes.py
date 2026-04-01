import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.core.s3_client import s3_client
from app.db.session import get_db
from app.services import image_service, recipe_service

router = APIRouter()


@router.post(
    "/", response_model=schemas.Recipe, status_code=201, operation_id="create_recipe"
)
async def create_new_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    recipe_in: schemas.RecipeCreate,
) -> schemas.Recipe:
    db_recipe = await recipe_service.create_recipe(db=db, recipe_in=recipe_in)
    return schemas.Recipe.model_validate(db_recipe)


@router.get("/", response_model=list[schemas.Recipe], operation_id="read_recipes")
async def read_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    include_ingredients: str | None = Query(
        None, description="Comma-separated ingredient to include", max_length=500
    ),
    exclude_ingredients: str | None = Query(
        None, description="Comma-separated ingredient to exclude", max_length=500
    ),
) -> list[schemas.Recipe]:
    recipes = await recipe_service.get_all_recipes(
        db=db,
        skip=skip,
        limit=limit,
        include_str=include_ingredients,
        exclude_str=exclude_ingredients,
    )
    return [schemas.Recipe.model_validate(r) for r in recipes]


@router.get(
    "/{recipe_id}", response_model=schemas.Recipe, operation_id="read_recipe_by_id"
)
async def read_recipe_by_id(
    *, db: Annotated[AsyncSession, Depends(get_db)], recipe_id: int
) -> schemas.Recipe:
    recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return schemas.Recipe.model_validate(recipe)


@router.patch(
    "/{recipe_id}", response_model=schemas.Recipe, operation_id="update_recipe"
)
async def update_existing_recipe(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    recipe_id: int,
    recipe_in: schemas.RecipeUpdate,
) -> schemas.Recipe:
    db_recipe: models.Recipe | None = await recipe_service.get_recipe_by_id(
        db=db, recipe_id=recipe_id
    )
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    updated_recipe = await recipe_service.update_recipe(
        db=db, db_recipe=db_recipe, recipe_in=recipe_in
    )
    return schemas.Recipe.model_validate(updated_recipe)


@router.delete(
    "/{recipe_id}", response_model=schemas.Recipe, operation_id="delete_recipe"
)
async def delete_existing_recipe(
    *, db: Annotated[AsyncSession, Depends(get_db)], recipe_id: int
) -> schemas.Recipe:
    deleted_recipe = await recipe_service.delete_recipe(db=db, recipe_id=recipe_id)
    if not deleted_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return schemas.Recipe.model_validate(deleted_recipe)


@router.get(
    "/search/", response_model=list[schemas.Recipe], operation_id="search_recipes"
)
async def search_recipes(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(
        ..., description="Search query for recipes using vector search", max_length=200
    ),
    include_ingredients: str | None = Query(
        None, description="Comma-separated ingredient to include", max_length=500
    ),
    exclude_ingredients: str | None = Query(
        None, description="Comma-separated ingredient to exclude", max_length=500
    ),
) -> list[schemas.Recipe]:
    recipes = await recipe_service.search_recipes_by_vector(
        db=db,
        query_str=q,
        include_str=include_ingredients,
        exclude_str=exclude_ingredients,
    )
    return [schemas.Recipe.model_validate(r) for r in recipes]


@router.post(
    "/{recipe_id}/image",
    response_model=schemas.Recipe,
    operation_id="upload_recipe_images",
)
async def upload_recipe_images(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    recipe_id: int,
    files: Annotated[list[UploadFile], File(...)],
) -> schemas.Recipe:
    recipe = await recipe_service.get_recipe_by_id(db=db, recipe_id=recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    if len(files) > 5:
        raise HTTPException(
            status_code=400, detail="Too many files sent. Max 5 allowed."
        )

    async def process_file(file: UploadFile) -> str:
        valid_content = await image_service.validate_and_process_image(file)

        filename = file.filename or ""
        extension = filename.split(".")[-1] if "." in filename else "jpg"
        obj_name = f"recipes/{recipe_id}/{uuid.uuid4()}.{extension}"

        content_type = file.content_type or "application/octet-stream"

        return await s3_client.upload_file(valid_content, obj_name, content_type)

    uploaded_urls = await asyncio.gather(*[process_file(f) for f in files])

    current_urls = list(recipe.image_urls) if recipe.image_urls else []
    recipe.image_urls = current_urls + list(uploaded_urls)

    db.add(recipe)
    await db.commit()
    await db.refresh(recipe)

    return schemas.Recipe.model_validate(recipe)


@router.delete(
    "/{recipe_id}/images",
    response_model=schemas.Recipe,
    operation_id="delete_recipe_images",
)
async def delete_recipe_images(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    recipe_id: int,
    delete_data: schemas.RecipeImagesDelete,
) -> schemas.Recipe:
    urls_as_strings = [str(url) for url in delete_data.image_urls]

    updated_recipe = await recipe_service.delete_recipe_images(
        db=db, recipe_id=recipe_id, urls_to_delete=urls_as_strings
    )

    if not updated_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    return schemas.Recipe.model_validate(updated_recipe)
