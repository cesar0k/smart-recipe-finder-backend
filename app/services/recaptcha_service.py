"""Google reCAPTCHA v3 / v2 server-side verification.

We use v3 (invisible scoring) by default and fall back to v2 (checkbox)
when v3 fails on the client — typically Safari, where Google's Private Access
Token endpoint can return 401 and hang ``grecaptcha.execute()``. The two
token types are verified against different secret keys; the client tells us
which type it sent via the ``token_type`` argument.
"""

import logging
from typing import Literal

import httpx

from app.core.config import settings
from app.core.exceptions import RecaptchaError

logger = logging.getLogger(__name__)

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

TokenType = Literal["v3", "v2"]


async def verify(
    token: str,
    action: str | None = None,
    token_type: TokenType = "v3",
) -> None:
    """Verify a reCAPTCHA token (v3 by default, v2 when ``token_type="v2"``).

    Raises RecaptchaError if:
    - RECAPTCHA_ENABLED is True and the relevant secret key is configured,
      but the token is missing/invalid
    - v3 only: the score is below RECAPTCHA_MIN_SCORE
    - v3 only: the expected action doesn't match (when provided)

    When RECAPTCHA_ENABLED is False, or the secret key for the requested
    token type is empty, the call is a no-op (useful for tests and dev).
    """
    if not settings.RECAPTCHA_ENABLED:
        return

    if token_type == "v2":
        secret = settings.RECAPTCHA_V2_SECRET_KEY
        if not secret:
            logger.debug("RECAPTCHA_V2_SECRET_KEY not set — skipping verification")
            return
    else:
        secret = settings.RECAPTCHA_SECRET_KEY
        if not secret:
            logger.debug("RECAPTCHA_SECRET_KEY not set — skipping verification")
            return

    if not token:
        raise RecaptchaError("reCAPTCHA token is missing")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                VERIFY_URL,
                data={
                    "secret": secret,
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

    # v2 doesn't return a score or action — the user proving they aren't a bot
    # by passing the checkbox / challenge is sufficient.
    if token_type == "v2":
        return

    score: float = result.get("score", 0.0)
    if score < settings.RECAPTCHA_MIN_SCORE:
        logger.warning("reCAPTCHA score too low: %.2f (min %.2f)", score, settings.RECAPTCHA_MIN_SCORE)
        raise RecaptchaError(f"reCAPTCHA score too low: {score:.2f}")

    if action and result.get("action") != action:
        logger.warning(
            "reCAPTCHA action mismatch: expected %r, got %r", action, result.get("action")
        )
        raise RecaptchaError("reCAPTCHA action mismatch")
