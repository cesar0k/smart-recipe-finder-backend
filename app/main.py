import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.api import api_router
from app.core.cache import close_redis, init_redis
from app.core.config import settings
from app.core.exceptions import (
    CaptchaError,
    InvalidCredentialsError,
    InvalidStateError,
    NotAuthorizedError,
    NotFoundError,
    ValidationError,
)
from app.core.rate_limit import limiter
from app.core.s3_client import s3_client
from app.core.vector_store import vector_store

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])

_IS_PROD = settings.ENVIRONMENT == "production"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_redis()
    await s3_client.ensure_bucket_exists()
    asyncio.create_task(asyncio.to_thread(vector_store.preload_model))
    try:
        yield
    finally:
        await close_redis()


class RootResponse(BaseModel):
    status: str
    project_name: str
    version: str
    documentation_url: str


# In production, hide the interactive docs / OpenAPI schema so the API
# surface isn't trivially browsable from the public domain.
app = FastAPI(
    title="Smart Recipes Finder",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
)

# Wire up the SlowAPI limiter (defined in app.core.rate_limit). Counters live
# in Redis so multiple uvicorn workers share the same bucket; per-route stricter
# limits (auth endpoints) are layered on top via @limiter.limit decorators.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)

# Reject requests with a Host header outside the allow-list. Only enforced
# in production; dev/test stays permissive so `localhost`, `127.0.0.1`, the
# docker-compose service name etc. all work.
if _IS_PROD:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NotFoundError)
async def _not_found_handler(_request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})


@app.exception_handler(NotAuthorizedError)
async def _forbidden_handler(_request: Request, exc: NotAuthorizedError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc) or "Forbidden"})


@app.exception_handler(InvalidStateError)
async def _conflict_handler(_request: Request, exc: InvalidStateError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc) or "Bad request"})


@app.exception_handler(ValidationError)
async def _validation_handler(_request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc) or "Validation error"})


@app.exception_handler(InvalidCredentialsError)
async def _unauthorized_handler(_request: Request, exc: InvalidCredentialsError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc) or "Invalid credentials"},
    )


@app.exception_handler(CaptchaError)
async def _captcha_handler(_request: Request, exc: CaptchaError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc) or "Captcha failed"})


app.include_router(api_router, prefix="/api/v1")


@app.get("/", response_model=RootResponse, tags=["Root"])
def read_root() -> RootResponse:
    return RootResponse(
        status="ok",
        project_name=app.title,
        version=app.version,
        documentation_url="/docs",
    )
