import logging

import httpx

from app.core.config import settings
from app.core.exceptions import CaptchaError

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify(token: str, action: str | None = None) -> None:
    if not settings.CAPTCHA_ENABLED:
        return

    secret = settings.CAPTCHA_SECRET_KEY
    if not secret:
        logger.debug("CAPTCHA_SECRET_KEY not set — skipping verification")
        return

    if not token:
        raise CaptchaError("Captcha token is missing")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                VERIFY_URL,
                data={"secret": secret, "response": token},
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Turnstile verification request failed: %s", exc)
        raise CaptchaError("Captcha verification service unavailable") from exc

    if not result.get("success"):
        error_codes = result.get("error-codes", [])
        logger.debug("Turnstile failed: %s (action=%s)", error_codes, action)
        raise CaptchaError("Captcha verification failed")
