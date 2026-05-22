"""SlowAPI Limiter singleton, separated from main.py to avoid circular imports.

The limiter is keyed by client IP and stores counters in Redis so that the
same bucket is shared across all uvicorn workers. main.py wires this into the
FastAPI app (app.state.limiter, the exception handler and the middleware);
endpoint modules import `limiter` here to decorate individual routes.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.REDIS_URL,
)
