from __future__ import annotations

import json
import logging
import random

from app.core.cache import Cache

logger = logging.getLogger(__name__)

SIMILAR_VERSION_KEY = "similar:version"
SIMILAR_TTL_SECONDS = 1800
SIMILAR_TTL_JITTER = 180


async def _build_key(cache: Cache, recipe_id: int) -> str:
    version = await cache.get_version(SIMILAR_VERSION_KEY)
    return f"similar:v{version}:{recipe_id}"


def _ttl_with_jitter() -> int:
    return SIMILAR_TTL_SECONDS + random.randint(
        -SIMILAR_TTL_JITTER, SIMILAR_TTL_JITTER
    )


async def get_cached_similar_pairs(
    cache: Cache, recipe_id: int
) -> list[tuple[int, float]] | None:
    key = await _build_key(cache, recipe_id)
    raw = await cache.get_raw(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("cached similar payload is not a list")
        return [(int(rid), float(dist)) for rid, dist in data]
    except (ValueError, TypeError):
        logger.warning("Invalid cached similar payload for key=%s; dropping", key)
        await cache.delete(key)
        return None


async def cache_similar_pairs(
    cache: Cache, recipe_id: int, pairs: list[tuple[int, float]]
) -> None:
    key = await _build_key(cache, recipe_id)
    serializable = [[rid, dist] for rid, dist in pairs]
    await cache.set_raw(key, json.dumps(serializable), ttl=_ttl_with_jitter())


async def bump_similar_version(cache: Cache) -> int:
    new_version = await cache.incr(SIMILAR_VERSION_KEY)
    logger.info("Similar cache version bumped to %s", new_version)
    return new_version
