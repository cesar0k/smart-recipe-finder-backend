"""One-off backfill: compress recipe photos stored uncompressed (full_url == thumbnail_url)."""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.future import select  # noqa: E402

from app.core.s3_client import s3_client  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models.recipe.recipe_image import RecipeImage  # noqa: E402
from app.services.recipe import image_service  # noqa: E402


async def _recompress_one(image_id: int, sem: asyncio.Semaphore, apply: bool) -> str | None:
    async with sem:
        async with AsyncSessionLocal() as db:
            img = await db.get(RecipeImage, image_id)
            if img is None:
                return f"[img {image_id}] vanished before processing"

            if img.full_url != img.thumbnail_url:
                return None  # already compressed, skip silently

            old_key = s3_client.object_key_from_url(img.full_url)
            if old_key is None:
                return f"[img {image_id}] full_url not in our bucket: {img.full_url}"

            try:
                original = await s3_client.download_file(old_key)
            except Exception as ex:  # noqa: BLE001
                return f"[img {image_id}] download failed: {ex}"

            try:
                versions = image_service.generate_compressed_versions(original)
            except Exception as ex:  # noqa: BLE001
                return f"[img {image_id}] compression failed: {ex}"

            if not apply:
                return None

            recipe_id = img.recipe_id
            file_id = uuid.uuid4()
            full_key = f"recipes/{recipe_id}/{file_id}.webp"
            thumb_key = f"recipes/{recipe_id}/{file_id}_thumb.webp"

            full_url = await s3_client.upload_file(versions["full"], full_key, "image/webp")
            thumb_url = await s3_client.upload_file(versions["thumb"], thumb_key, "image/webp")

            img.full_url = full_url
            img.thumbnail_url = thumb_url
            await db.commit()

            try:
                await s3_client.delete_file(old_key)
            except Exception as ex:  # noqa: BLE001
                return f"[img {image_id}] recompressed OK but old object delete failed: {ex}"

            return None


async def run(apply: bool, concurrency: int) -> None:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(RecipeImage.id).where(RecipeImage.full_url == RecipeImage.thumbnail_url)
        )
        image_ids = [r[0] for r in rows.all()]

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] {len(image_ids)} uncompressed image(s) found (full_url == thumbnail_url).")
    if not image_ids:
        print("Nothing to do.")
        return

    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*[_recompress_one(i, sem, apply) for i in image_ids])
    errors = [e for e in results if e]
    ok = len(image_ids) - len(errors)

    if apply:
        print(f"Done. {ok} recompressed, {len(errors)} failed.")
    else:
        print(f"Dry-run complete. {ok} would be recompressed, {len(errors)} would fail.")
        print("Re-run with --apply to write changes.")
    if errors:
        print("\nIssues:")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without it the script only reports (dry-run).",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply, concurrency=args.concurrency))
