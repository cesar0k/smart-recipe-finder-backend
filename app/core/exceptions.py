"""Domain-level exceptions raised by service layer."""

from __future__ import annotations


class DomainError(Exception):
    """Base for all domain-level errors."""


class NotFoundError(DomainError):
    """Resource does not exist or is not visible to the caller."""


class NotAuthorizedError(DomainError):
    """Caller is not authorized to perform this action."""


class InvalidStateError(DomainError):
    """Resource is in a state that doesn't allow the requested action."""


class ValidationError(DomainError):
    """Domain-level validation failure (e.g. duplicate username)."""


class InvalidCredentialsError(DomainError):
    """Login or refresh-token validation failure (HTTP 401)."""


class CaptchaError(DomainError):
    """Captcha verification failed (HTTP 400)."""
