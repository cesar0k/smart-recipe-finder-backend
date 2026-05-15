"""Google reCAPTCHA v3 server-side verification."""

import logging

import httpx

from app.core.config import settings
from app.core.exceptions import RecaptchaError

logger = logging.getLogger(__name__)

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


async def verify(token: str, action: str | None = None) -> None:
    """Verify a reCAPTCHA v3 token.

    Raises RecaptchaError if:
    - RECAPTCHA_ENABLED is True and secret key is configured, but token is missing/invalid
    - The score is below RECAPTCHA_MIN_SCORE
    - The expected action doesn't match (when provided)

    When RECAPTCHA_ENABLED is False or RECAPTCHA_SECRET_KEY is empty, the
    call is a no-op (useful for tests and local dev without a key).
    """
    if not settings.RECAPTCHA_ENABLED:
        return
    if not settings.RECAPTCHA_SECRET_KEY:
        logger.debug("RECAPTCHA_SECRET_KEY not set — skipping verification")
        return
    if not token:
        raise RecaptchaError("reCAPTCHA token is missing")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                VERIFY_URL,
                data={
                    "secret": settings.RECAPTCHA_SECRET_KEY,
                    "response": token,
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("reCAPTCHA verification request failed: %s", exc)
        raise RecaptchaError("reCAPTCHA verification service unavailable") from exc

    if not result.get("success"):
        error_codes = result.get("error-codes", [])
        logger.debug("reCAPTCHA failed: %s", error_codes)
        raise RecaptchaError("reCAPTCHA verification failed")

    score: float = result.get("score", 0.0)
    if score < settings.RECAPTCHA_MIN_SCORE:
        logger.warning("reCAPTCHA score too low: %.2f (min %.2f)", score, settings.RECAPTCHA_MIN_SCORE)
        raise RecaptchaError(f"reCAPTCHA score too low: {score:.2f}")

    if action and result.get("action") != action:
        logger.warning(
            "reCAPTCHA action mismatch: expected %r, got %r", action, result.get("action")
        )
        raise RecaptchaError("reCAPTCHA action mismatch")
