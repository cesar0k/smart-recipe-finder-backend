from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text

from app.core.cache import NullCache, get_cache
from app.core.s3_client import s3_client
from app.core.vector_store import vector_store
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "fail"]
OverallStatus = Literal["ok", "degraded", "fail"]

CRITICAL_DEPENDENCIES = {"postgres"}
CHECK_TIMEOUT_SECONDS = 2.0


class CheckResult(BaseModel):
    status: CheckStatus
    latency_ms: float
    critical: bool
    error: str | None = None


class HealthReport(BaseModel):
    status: OverallStatus
    checks: dict[str, CheckResult]


async def _timed(check_name: str, coro) -> CheckResult:  # type: ignore[no-untyped-def]
    critical = check_name in CRITICAL_DEPENDENCIES
    start = time.perf_counter()
    try:
        await asyncio.wait_for(coro, timeout=CHECK_TIMEOUT_SECONDS)
        return CheckResult(
            status="ok",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            critical=critical,
        )
    except Exception as exc:
        return CheckResult(
            status="fail",
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
            critical=critical,
            error=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
        )


async def _check_postgres() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))


async def _check_redis() -> None:
    cache = await get_cache()
    if isinstance(cache, NullCache):
        raise RuntimeError("Redis not initialized")
    await cache._redis.ping()  # type: ignore[misc] # noqa: SLF001


async def _check_chroma() -> None:
    await asyncio.to_thread(vector_store.client.heartbeat)


async def _check_minio() -> None:
    from app.core.config import settings

    await asyncio.to_thread(
        s3_client.client.head_bucket, Bucket=settings.S3_BUCKET_NAME
    )


async def run_all_checks() -> HealthReport:
    names = ["postgres", "redis", "chroma", "minio"]
    coros = [
        _check_postgres(),
        _check_redis(),
        _check_chroma(),
        _check_minio(),
    ]
    results = await asyncio.gather(
        *(_timed(name, coro) for name, coro in zip(names, coros, strict=True))
    )
    checks = dict(zip(names, results, strict=True))

    overall = _overall_status(checks)
    return HealthReport(status=overall, checks=checks)


def _overall_status(checks: dict[str, CheckResult]) -> OverallStatus:
    has_critical_fail = any(
        r.status == "fail" and r.critical for r in checks.values()
    )
    if has_critical_fail:
        return "fail"

    has_any_fail = any(r.status == "fail" for r in checks.values())
    if has_any_fail:
        return "degraded"

    return "ok"
