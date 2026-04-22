from __future__ import annotations

import hashlib
import json
import logging
import random

from app.core.cache import Cache

logger = logging.getLogger(__name__)

SEARCH_VERSION_KEY = "search:version"
SEARCH_TTL_SECONDS = 900
SEARCH_TTL_JITTER = 120


def _normalize_query(query: str) -> str:
    return " ".join(query.lower().strip().split())


def _hash_query(query: str) -> str:
    normalized = _normalize_query(query)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


async def _build_key(cache: Cache, query: str) -> str:
    version = await cache.get_version(SEARCH_VERSION_KEY)
    return f"search:v{version}:{_hash_query(query)}"


def _ttl_with_jitter() -> int:
    return SEARCH_TTL_SECONDS + random.randint(-SEARCH_TTL_JITTER, SEARCH_TTL_JITTER)


async def get_cached_search_ids(cache: Cache, query: str) -> list[int] | None:
    key = await _build_key(cache, query)
    raw = await cache.get_raw(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("cached search payload is not a list")
        return [int(x) for x in data]
    except (ValueError, TypeError):
        logger.warning("Invalid cached search payload for key=%s; dropping", key)
        await cache.delete(key)
        return None


async def cache_search_ids(cache: Cache, query: str, ids: list[int]) -> None:
    key = await _build_key(cache, query)
    await cache.set_raw(key, json.dumps(ids), ttl=_ttl_with_jitter())


async def bump_search_version(cache: Cache) -> int:
    new_version = await cache.incr(SEARCH_VERSION_KEY)
    logger.info("Search cache version bumped to %s", new_version)
    return new_version
