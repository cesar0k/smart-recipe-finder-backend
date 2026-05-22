"""Find-or-create helpers for the Ingredient reference table."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recipe.ingredient import Ingredient


def _normalise(name: str) -> str:
    """Trim + lowercase so 'Garlic' and 'garlic ' map to the same row."""
    return name.strip().lower()


async def get_or_create_many(
    db: AsyncSession, *, names: list[str]
) -> dict[str, Ingredient]:
    """Find-or-create ingredients; returns {canonical_name -> row}. Race-safe
    via INSERT ... ON CONFLICT DO NOTHING + re-select."""
    canonical = sorted({_normalise(n) for n in names if n and n.strip()})
    if not canonical:
        return {}

    await db.execute(
        pg_insert(Ingredient)
        .values([{"name": n} for n in canonical])
        .on_conflict_do_nothing(index_elements=["name"])
    )
    res = await db.execute(select(Ingredient).where(Ingredient.name.in_(canonical)))
    return {row.name: row for row in res.scalars().all()}
