import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.future import select

from app.core.vector_store import vector_store
from app.db.session import AsyncSessionLocal
from app.models.recipe import Recipe
from app.models.user import User
from app.schemas.recipe_create import RecipeCreate
from app.services import recipe_service

DATASETS_PATH = Path(__file__).resolve().parents[1] / "datasets"


async def seed(lang: str) -> None:
    """
    Seeds the database with sample recipes.
    """
    print(f"Seeding database with '{lang}' recipes...")

    recipes_file = "recipe_samples.json"
    recipes_path = DATASETS_PATH / lang / recipes_file

    if not recipes_path.exists():
        print(f"Error: Recipes file not found at {recipes_path}")
        return

    print("Cleaning Vector Store...")
    vector_store.clear()

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
            r_input = r_data.copy()
            if "id" in r_input:
                del r_input["id"]

            recipe_in = RecipeCreate(**r_input)
            await recipe_service.create_recipe(
                db=db, recipe_in=recipe_in, current_user=admin_user
            )

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
