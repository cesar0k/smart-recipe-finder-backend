"""Cloudflare Turnstile server-side verification.

Turnstile is a privacy-friendly captcha that works reliably across browsers
(including Safari with Private Relay / Hide IP enabled), unlike Google
reCAPTCHA which depends on Google's PAT endpoint and silently hangs in
some Safari configurations.

The widget produces a short-lived token; we exchange it against
Cloudflare's siteverify endpoint with the server-side secret.

Docs: https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""

import logging

import httpx

from app.core.config import settings
from app.core.exceptions import CaptchaError

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify(token: str, action: str | None = None) -> None:
    """Verify a Turnstile token.

    Raises CaptchaError if CAPTCHA_ENABLED is True, the secret key is
    configured, and the token is missing or rejected by Cloudflare.

    When CAPTCHA_ENABLED is False or CAPTCHA_SECRET_KEY is empty, the call
    is a no-op (useful for tests and local dev without a real secret).

    The ``action`` parameter is passed through to siteverify as a hint —
    Cloudflare will optionally echo it back as ``cdata`` in the response.
    We don't enforce action matching the way reCAPTCHA v3 does because
    Turnstile's threat model relies on the token itself rather than scores.
    """
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
