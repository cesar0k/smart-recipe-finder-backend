import argparse
import asyncio
import json
import sys
import uuid
from io import BytesIO
from pathlib import Path

from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.future import select

from app.core.s3_client import s3_client
from app.core.vector_store import vector_store
from app.db.session import AsyncSessionLocal
from app.models.recipe import Recipe
from app.models.user import User
from app.schemas import RecipeCreate
from app.services import recipe_service

DATASETS_PATH = Path(__file__).resolve().parents[1] / "datasets"
RECIPE_PHOTOS_PATH = DATASETS_PATH / "recipe_photos"

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


async def upload_local_photos(recipe_id: int, json_id: int) -> list[str]:
    """
    Upload photos from datasets/recipe_photos/{json_id}/ to Minio.
    Returns list of uploaded URLs, or empty list if no local photos found.
    """
    photo_dir = RECIPE_PHOTOS_PATH / str(json_id)

    if not photo_dir.exists() or not photo_dir.is_dir():
        return []

    photo_files = sorted(
        f for f in photo_dir.iterdir()
        if f.is_file() and f.suffix.lower() in PHOTO_EXTENSIONS
    )

    if not photo_files:
        return []

    urls: list[str] = []
    for photo_path in photo_files:
        ext = photo_path.suffix.lower()
        content_type = MIME_MAP.get(ext, "application/octet-stream")
        object_name = f"recipes/{recipe_id}/{uuid.uuid4()}{ext}"

        with open(photo_path, "rb") as f:
            file_bytes = BytesIO(f.read())

        url = await s3_client.upload_file(file_bytes, object_name, content_type)
        urls.append(url)

    return urls


async def seed(lang: str) -> None:
    """
    Seeds the database with sample recipes.
    If datasets/recipe_photos/{id}/ exists with photos, uploads them to Minio.
    Otherwise, uses image_urls from the JSON dataset.
    """
    print(f"Seeding database with '{lang}' recipes...")

    recipes_file = "recipe_samples.json"
    recipes_path = DATASETS_PATH / lang / recipes_file

    if not recipes_path.exists():
        print(f"Error: Recipes file not found at {recipes_path}")
        return

    print("Cleaning Vector Store...")
    vector_store.clear()

    has_photos_dir = RECIPE_PHOTOS_PATH.exists() and any(RECIPE_PHOTOS_PATH.iterdir())
    if has_photos_dir:
        print(f" - Found local photos at {RECIPE_PHOTOS_PATH}")
        await s3_client.ensure_bucket_exists()
    else:
        print(" - No local photos found, using image URLs from dataset")

    async with AsyncSessionLocal() as db:
        # Find an admin user to assign as recipe owner
        result = await db.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        admin_user = result.scalar_one_or_none()

        if admin_user is None:
            print("Error: No admin user found. Run create_admin.py first.")
            return

        print(" - Cleaning old data...")
        await db.execute(delete(Recipe))
        await db.commit()

        print(f" - Loading recipes from {recipes_path}...")
        with open(recipes_path, encoding="utf-8") as f:
            recipes_data = json.load(f)

        for r_data in recipes_data:
            json_id = r_data.get("id")
            r_input = r_data.copy()
            if "id" in r_input:
                del r_input["id"]

            recipe_in = RecipeCreate(**r_input)
            db_recipe = await recipe_service.create_recipe(
                db=db, recipe_in=recipe_in, current_user=admin_user
            )

            # Try to upload local photos for this recipe
            if has_photos_dir and json_id is not None:
                uploaded_urls = await upload_local_photos(db_recipe.id, json_id)
                if uploaded_urls:
                    db_recipe.image_urls = uploaded_urls
                    db.add(db_recipe)
                    await db.commit()
                    print(f"   Uploaded {len(uploaded_urls)} photo(s) for \"{db_recipe.title}\"")

        print(f"Successfully inserted {len(recipes_data)} recipes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed the database with sample recipes."
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="en",
        choices=["en", "ru"],
        help="Language of the recipes to seed (en or ru).",
    )
    args = parser.parse_args()
    asyncio.run(seed(lang=args.lang))
