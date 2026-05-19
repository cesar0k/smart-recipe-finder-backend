"""Periodic background job: classify tags for approved recipes that have none.

Runs every TAG_BACKFILL_INTERVAL_SECONDS inside the FastAPI lifespan. Each
cycle finds up to TAG_BACKFILL_BATCH_SIZE recipes with no RecipeTags row and
calls classify_recipe_tags() for each one sequentially to avoid hammering
fal.ai. Stops gracefully when the app shuts down.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import outerjoin, select

from app.db.session import AsyncSessionLocal
from app.models.recipe import Recipe
from app.models.recipe_tags import RecipeTags
from app.services.tag_service import classify_recipe_tags

log = logging.getLogger(__name__)

TAG_BACKFILL_INTERVAL_SECONDS = 600  # 10 minutes between cycles
TAG_BACKFILL_BATCH_SIZE = 20         # recipes per cycle


async def _backfill_cycle() -> None:
    """Find untagged approved recipes and classify them one by one."""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Recipe.id)
            .select_from(
                outerjoin(Recipe, RecipeTags, Recipe.id == RecipeTags.recipe_id)
            )
            .where(
                Recipe.status == "approved",
                RecipeTags.id.is_(None),
            )
            .limit(TAG_BACKFILL_BATCH_SIZE)
        )
        result = await db.execute(stmt)
        recipe_ids = [row[0] for row in result.all()]

    if not recipe_ids:
        return

    log.info("tag_backfill: found %d untagged recipe(s), classifying...", len(recipe_ids))
    for recipe_id in recipe_ids:
        await classify_recipe_tags(recipe_id)


async def run_tag_backfill_loop(stop_event: asyncio.Event) -> None:
    """Run _backfill_cycle periodically until stop_event is set."""
    log.info(
        "tag_backfill: started (interval=%ds, batch=%d)",
        TAG_BACKFILL_INTERVAL_SECONDS,
        TAG_BACKFILL_BATCH_SIZE,
    )
    while not stop_event.is_set():
        try:
            await _backfill_cycle()
        except Exception as exc:  # noqa: BLE001
            log.warning("tag_backfill: cycle failed: %s", exc)

        try:
            await asyncio.wait_for(
                asyncio.shield(stop_event.wait()),
                timeout=TAG_BACKFILL_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass  # normal — interval elapsed, run next cycle

    log.info("tag_backfill: stopped")
