"""Cuisine reference-table helpers (find-or-create + autocomplete)."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cuisine import Cuisine


async def get_or_create_by_name(db: AsyncSession, *, name: str | None) -> Cuisine | None:
    """Look up a cuisine by name (case-sensitive, trimmed); create if absent.

    `None`/empty input → `None`. Uses ``INSERT ... ON CONFLICT DO NOTHING
    RETURNING`` to be safe under concurrent inserts.
    """
    if name is None:
        return None
    trimmed = name.strip()
    if not trimmed:
        return None

    # First, try plain SELECT — covers the hot path where the cuisine exists.
    res = await db.execute(select(Cuisine).where(Cuisine.name == trimmed))
    cuisine = res.scalar_one_or_none()
    if cuisine is not None:
        return cuisine

    # Slow path: race-safe INSERT.
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

    # ON CONFLICT swallowed the insert — another transaction won. Re-select.
    res = await db.execute(select(Cuisine).where(Cuisine.name == trimmed))
    return res.scalar_one()


async def list_all(db: AsyncSession) -> Sequence[Cuisine]:
    res = await db.execute(select(Cuisine).order_by(Cuisine.name))
    return res.scalars().all()
