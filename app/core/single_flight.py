"""In-process request coalescing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class SingleFlight:
    """Registry of in-flight futures keyed by an arbitrary string."""

    def __init__(self) -> None:
        self._flights: dict[str, asyncio.Future[Any]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._flights.get(key)
        if existing is not None:
            return await existing  # type: ignore[no-any-return]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[T] = loop.create_future()
        self._flights[key] = fut
        try:
            result = await fn()
        except BaseException as exc:
            # Wake up all awaiters with the same exception, then re-raise.
            if not fut.done():
                fut.set_exception(exc)
            raise
        else:
            if not fut.done():
                fut.set_result(result)
            return result
        finally:
            # Always clear the slot so the next miss can run fresh.
            self._flights.pop(key, None)
