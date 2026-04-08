import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.services.user_service import get_user_by_email

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


class GoogleAuthError(Exception):
    pass


async def exchange_code_for_user_info(
    code: str, redirect_uri: str
) -> dict[str, str]:
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

        return userinfo_response.json()


async def authenticate_or_create_google_user(
    db: AsyncSession, *, google_user_info: dict[str, str]
) -> User:
    """Find existing user by Google email or create a new one."""
    email = google_user_info.get("email")
    if not email:
        raise GoogleAuthError("Google account has no email")

    user = await get_user_by_email(db, email=email)

    if user is not None:
        return user

    name = google_user_info.get("name", "")
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
        hashed_password=None,
        auth_provider="google",
        role="user",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
