"""Ingredient reference-table helpers.

We don't expose these as endpoints (the UI sees the join via Recipe). They
exist to keep recipe_service light and to be the single owner of the
find-or-create pattern under concurrent writes.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingredient import Ingredient


def _normalise(name: str) -> str:
    """Trim + lowercase so two writers spelling 'Garlic' and 'garlic ' agree
    on the same canonical row."""
    return name.strip().lower()


async def get_or_create_many(
    db: AsyncSession, *, names: list[str]
) -> dict[str, Ingredient]:
    """Find-or-create ingredients for *names*; returns a {canonical_name -> row}
    map. Race-safe via ON CONFLICT DO NOTHING followed by a re-select.

    Empty/whitespace names are dropped.
    """
    canonical = sorted({_normalise(n) for n in names if n and n.strip()})
    if not canonical:
        return {}

    # Try a bulk insert; existing rows are silently skipped.
    await db.execute(
        pg_insert(Ingredient)
        .values([{"name": n} for n in canonical])
        .on_conflict_do_nothing(index_elements=["name"])
    )

    # Re-select all canonical names — covers both the rows we just inserted
    # and the ones another transaction had inserted first.
    res = await db.execute(select(Ingredient).where(Ingredient.name.in_(canonical)))
    return {row.name: row for row in res.scalars().all()}
