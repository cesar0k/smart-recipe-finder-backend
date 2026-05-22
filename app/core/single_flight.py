"""In-process request coalescing ("single-flight").

When N concurrent requests need the same expensive computation (e.g. the
embedding + LLM passes behind /search for the same query) and the Redis
cache hasn't been populated yet, naively each one launches the work.

`coalesce()` deduplicates these: only the first caller actually runs the
coroutine; the rest await the same in-flight future and receive the same
result. Once the work completes, the entry is removed from the registry,
so the next miss will run again — by which point the Redis cache is
usually populated and the question doesn't come up.

Scope: per-process. With multiple uvicorn workers each one has its own
flight registry. That's fine for our use case: 4 redundant inferences
across 4 workers is much better than 40 across the same worker, and the
Redis cache makes the cross-worker case a non-issue after one of them
finishes first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class SingleFlight:
    """Registry of in-flight futures keyed by an arbitrary string."""

    def __init__(self) -> None:
        # We intentionally don't lock on the dict — asyncio is single-threaded
        # within an event loop, so check-then-set on a dict is atomic between
        # awaits. Each flight is its own asyncio.Future, awaited by callers.
        self._flights: dict[str, asyncio.Future[Any]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Run `fn()` once per key while other callers await the same result.

        If a flight for `key` is already in progress, simply await it. Otherwise
        register a new future, run `fn()`, set the result (or exception) and
        clean up the registry.
        """
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
