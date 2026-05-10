import asyncio
import logging
import re
from typing import cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.services.user_service import (
    get_user_by_email,
    set_avatar_from_remote_url_background,
)

logger = logging.getLogger(__name__)

# Google avatar URLs typically end in `=s96-c` (96px crop).
# Bump that to a size that looks reasonable on profile pages without
# wasting bytes if we ever pull a 1200px master.
_GOOGLE_SIZE_SUFFIX = re.compile(r"=s\d+-c$")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


class GoogleAuthError(Exception):
    pass


async def exchange_code_for_user_info(code: str, redirect_uri: str) -> dict[str, str]:
    """Exchange authorization code for user info from Google."""
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

        if token_response.status_code != 200:
            raise GoogleAuthError("Failed to exchange code for token")

        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise GoogleAuthError("No access token in Google response")

        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if userinfo_response.status_code != 200:
            raise GoogleAuthError("Failed to fetch user info from Google")

        return cast(dict[str, str], userinfo_response.json())


def upgrade_google_picture_size(url: str) -> str:
    """Replace Google's default `=s96-c` suffix with a higher-resolution one.

    Falls through unchanged for URLs that don't carry the suffix.
    """
    return _GOOGLE_SIZE_SUFFIX.sub("=s400-c", url)


async def authenticate_or_create_google_user(
    db: AsyncSession,
    *,
    google_user_info: dict[str, str],
) -> User:
    """Find existing user by Google email or create a new one.

    For new users, the Google profile picture is fetched into our own S3
    bucket asynchronously (fire-and-forget) so registration latency is not
    bound to Google's CDN. Existing users are returned unchanged; the
    offline backfill script handles legacy CDN URLs.
    """
    email = google_user_info.get("email")
    if not email:
        raise GoogleAuthError("Google account has no email")

    user = await get_user_by_email(db, email=email)

    if user is not None:
        return user

    display_name = google_user_info.get("name", "") or None
    picture_url = google_user_info.get("picture") or None

    # Generate username from email prefix, ensure uniqueness
    base_username = email.split("@")[0]
    username = base_username

    from app.services.user_service import get_user_by_username

    # If username taken, append a suffix
    counter = 1
    while await get_user_by_username(db, username=username):
        username = f"{base_username}_{counter}"
        counter += 1

    user = User(
        email=email,
        username=username,
        display_name=display_name,
        avatar_url=None,
        hashed_password=None,
        auth_provider="google",
        role="user",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    if picture_url:
        # Fire-and-forget: the avatar download + S3 upload runs after the
        # response is sent so the user gets their JWT immediately.
        asyncio.create_task(
            set_avatar_from_remote_url_background(user.id, upgrade_google_picture_size(picture_url))
        )

    return user
