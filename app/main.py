import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api.v1.api import api_router
from app.core.cache import close_redis, init_redis
from app.core.config import settings
from app.core.s3_client import s3_client
from app.core.vector_store import vector_store

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])


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


app = FastAPI(title="Smart Recipes Finder", version="2.0.0", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/", response_model=RootResponse, tags=["Root"])
def read_root() -> RootResponse:
    return RootResponse(
        status="ok",
        project_name=app.title,
        version=app.version,
        documentation_url="/docs",
    )
