"""Cuisine reference-table helpers (find-or-create + autocomplete)."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recipe.cuisine import Cuisine


async def get_or_create_by_name(db: AsyncSession, *, name: str | None) -> Cuisine | None:
    """Find-or-create a cuisine (trimmed, case-sensitive). None/empty → None."""
    if name is None:
        return None
    trimmed = name.strip()
    if not trimmed:
        return None

    # Hot path: SELECT existing.
    res = await db.execute(select(Cuisine).where(Cuisine.name == trimmed))
    cuisine = res.scalar_one_or_none()
    if cuisine is not None:
        return cuisine

    # Race-safe INSERT; if another tx won the race, re-select.
    stmt = (
        pg_insert(Cuisine)
        .values(name=trimmed)
        .on_conflict_do_nothing(index_elements=["name"])
        .returning(Cuisine)
    )
    res = await db.execute(stmt)
    cuisine = res.scalar_one_or_none()
    if cuisine is not None:
        return cuisine

    res = await db.execute(select(Cuisine).where(Cuisine.name == trimmed))
    return res.scalar_one()


async def list_all(db: AsyncSession) -> Sequence[Cuisine]:
    res = await db.execute(select(Cuisine).order_by(Cuisine.name))
    return res.scalars().all()
