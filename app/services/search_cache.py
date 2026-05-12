from __future__ import annotations

import hashlib
import json
import logging
import random
from typing import Any

from app.core.cache import Cache

logger = logging.getLogger(__name__)

SEARCH_VERSION_KEY = "search:version"
SEARCH_TTL_SECONDS = 900
SEARCH_TTL_JITTER = 120

# Query intent is expensive (~1–3s via fal.ai) and deterministic for a given
# query text — it doesn't depend on which recipes exist, so no version-scoping.
INTENT_TTL_SECONDS = 900
_INTENT_PREFIX = "intent:"


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


async def get_cached_search_pairs(cache: Cache, query: str) -> list[tuple[int, float]] | None:
    key = await _build_key(cache, query)
    raw = await cache.get_raw(key)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("cached search payload is not a list")
        return [(int(item[0]), float(item[1])) for item in data]
    except (ValueError, TypeError, IndexError):
        logger.warning("Invalid cached search payload for key=%s; dropping", key)
        await cache.delete(key)
        return None


async def cache_search_pairs(cache: Cache, query: str, pairs: list[tuple[int, float]]) -> None:
    key = await _build_key(cache, query)
    await cache.set_raw(key, json.dumps(pairs), ttl=_ttl_with_jitter())


async def get_cached_intent(cache: Cache, query: str) -> dict[str, Any] | None:
    """Return cached intent dict, or None on cache miss.

    Caches both real constraints ({"vegetarian": true}) and empty result ({})
    so simple queries like "борщ" don't re-hit the LLM either.
    Returns None only when the key has never been set.
    """
    raw = await cache.get_raw(f"{_INTENT_PREFIX}{_hash_query(query)}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("cached intent payload is not a dict")
        return data  # type: ignore[return-value]
    except (ValueError, TypeError):
        logger.warning("Invalid cached intent payload for query=%r; dropping", query)
        await cache.delete(f"{_INTENT_PREFIX}{_hash_query(query)}")
        return None


async def cache_intent(cache: Cache, query: str, intent: dict[str, Any]) -> None:
    await cache.set_raw(
        f"{_INTENT_PREFIX}{_hash_query(query)}", json.dumps(intent), ttl=INTENT_TTL_SECONDS
    )


async def bump_search_version(cache: Cache) -> int:
    new_version = await cache.incr(SEARCH_VERSION_KEY)
    logger.info("Search cache version bumped to %s", new_version)
    return new_version
