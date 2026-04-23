from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel
from redis.asyncio import Redis, from_url

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_redis: Redis | None = None


async def init_redis() -> None:
    global _redis
    _redis = from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    await _redis.ping()  # type: ignore[misc]
    logger.info("Redis connected: %s", settings.REDIS_URL)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


class Cache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get_raw(self, key: str) -> str | None:
        value = await self._redis.get(key)
        return value  # type: ignore[no-any-return]

    async def set_raw(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set(key, value, ex=ttl or settings.REDIS_DEFAULT_TTL)

    async def get_model(self, key: str, model: type[T]) -> T | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return model.model_validate_json(raw)
        except ValueError:
            logger.warning("Failed to decode cached value for key=%s", key)
            await self._redis.delete(key)
            return None

    async def set_model(
        self, key: str, value: BaseModel, ttl: int | None = None
    ) -> None:
        await self._redis.set(
            key,
            value.model_dump_json(),
            ex=ttl or settings.REDIS_DEFAULT_TTL,
        )

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._redis.delete(*keys)

    async def incr(self, key: str) -> int:
        result = await self._redis.incr(key)
        return int(result)

    async def get_version(self, key: str) -> int:
        raw = await self._redis.get(key)
        return int(raw) if raw else 0


class NullCache(Cache):
    """No-op cache used when Redis is unavailable (tests, evaluate.py, etc.).

    All reads return misses, all writes are silently dropped, so the
    application runs correctly without Redis at the cost of no caching.
    """

    def __init__(self) -> None:
        pass

    async def get_raw(self, key: str) -> str | None:
        return None

    async def set_raw(self, key: str, value: str, ttl: int | None = None) -> None:
        return None

    async def get_model(self, key: str, model: type[T]) -> T | None:
        return None

    async def set_model(
        self, key: str, value: BaseModel, ttl: int | None = None
    ) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        return None

    async def incr(self, key: str) -> int:
        return 0

    async def get_version(self, key: str) -> int:
        return 0


async def get_cache() -> Cache:
    if _redis is None:
        return NullCache()
    return Cache(_redis)
