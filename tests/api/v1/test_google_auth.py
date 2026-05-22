"""Tests for Google avatar persistence in google_auth_service."""

from __future__ import annotations

from io import BytesIO
from typing import AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.core.config import settings
from app.services.auth import google_auth_service
from app.services.social import user_service
def _png_bytes(size: int = 64) -> bytes:
    """Produce a small valid PNG so libmagic recognises image/png."""
    buf = BytesIO()
    Image.new("RGB", (size, size), color=(123, 200, 75)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
async def db_session(
    db_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    async with async_sessionmaker(bind=db_engine, expire_on_commit=False)() as session:
        yield session


@pytest.mark.asyncio
async def test_google_registration_schedules_avatar_download(
    db_session: AsyncSession, monkeypatch: MonkeyPatch
) -> None:
    """A new Google user kicks off the background avatar fetch with the
    higher-resolution URL."""

    background_calls: list[tuple[int, str]] = []

    async def _capture(user_id: int, url: str) -> None:
        background_calls.append((user_id, url))

    monkeypatch.setattr(
        google_auth_service,
        "set_avatar_from_remote_url_background",
        _capture,
    )

    user = await google_auth_service.authenticate_or_create_google_user(
        db_session,
        google_user_info={
            "email": "newgoogleuser@example.com",
            "name": "New Google User",
            "picture": "https://lh3.googleusercontent.com/a/AAAA=s96-c",
        },
    )

    try:
        assert user.id is not None
        assert user.avatar_url is None  # populated asynchronously by the bg task
        # Yield to the event loop so the scheduled task actually runs.
        await asyncio_yield()
        assert background_calls == [
            (user.id, "https://lh3.googleusercontent.com/a/AAAA=s400-c"),
        ]
    finally:
        await db_session.delete(user)
        await db_session.commit()


@pytest.mark.asyncio
async def test_set_avatar_from_remote_url_persists_to_s3(
    db_session: AsyncSession, monkeypatch: MonkeyPatch
) -> None:
    """``set_avatar_from_remote_url`` uploads the fetched bytes to our S3
    and rewrites the user's avatar_url accordingly."""

    fake_png = _png_bytes()

    async def _fake_fetch(url: str) -> bytes:
        return fake_png

    captured: dict[str, str] = {}

    async def _fake_upload(file_obj: object, object_name: str, content_type: str) -> str:
        captured["object_name"] = object_name
        captured["content_type"] = content_type
        return f"{settings.S3_PUBLIC_ENDPOINT}/{settings.S3_BUCKET_NAME}/{object_name}"

    monkeypatch.setattr(
        user_service,
        "_fetch_remote_image_bytes_with_retry",
        AsyncMock(side_effect=_fake_fetch),
    )
    monkeypatch.setattr(user_service.s3_client, "upload_file", _fake_upload)

    from app.models.auth.user import User

    user = User(
        email="bg-target@example.com",
        username="bg_target",
        auth_provider="google",
        role="user",
        hashed_password=None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    try:
        result = await user_service.set_avatar_from_remote_url(
            db_session,
            user=user,
            url="https://lh3.googleusercontent.com/a/CCCC=s400-c",
        )
        assert result.avatar_url is not None
        assert result.avatar_url.startswith(
            f"{settings.S3_PUBLIC_ENDPOINT}/{settings.S3_BUCKET_NAME}/avatars/{user.id}/"
        )
        assert "googleusercontent.com" not in result.avatar_url
        assert captured["content_type"] == "image/png"
        assert captured["object_name"].startswith(f"avatars/{user.id}/")
        assert captured["object_name"].endswith(".png")
    finally:
        await db_session.delete(user)
        await db_session.commit()


@pytest.mark.asyncio
async def test_set_avatar_from_remote_url_swallows_download_failure(
    db_session: AsyncSession, monkeypatch: MonkeyPatch
) -> None:
    """If the remote download fails, the user is returned unchanged and no
    upload happens."""

    async def _failing_fetch(url: str) -> bytes:
        raise httpx.ConnectTimeout("simulated timeout")

    upload_called = False

    async def _fake_upload(*_args: object, **_kwargs: object) -> str:
        nonlocal upload_called
        upload_called = True
        return "should-not-be-used"

    monkeypatch.setattr(
        user_service,
        "_fetch_remote_image_bytes_with_retry",
        AsyncMock(side_effect=_failing_fetch),
    )
    monkeypatch.setattr(user_service.s3_client, "upload_file", _fake_upload)

    from app.models.auth.user import User

    user = User(
        email="bg-failure@example.com",
        username="bg_failure",
        auth_provider="google",
        role="user",
        hashed_password=None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    try:
        result = await user_service.set_avatar_from_remote_url(
            db_session,
            user=user,
            url="https://lh3.googleusercontent.com/a/DDDD=s400-c",
        )
        assert result.avatar_url is None
        assert upload_called is False
    finally:
        await db_session.delete(user)
        await db_session.commit()


async def asyncio_yield() -> None:
    """Yield control to the event loop so a freshly scheduled task can run."""
    import asyncio

    await asyncio.sleep(0)
