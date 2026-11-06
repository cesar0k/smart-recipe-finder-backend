"""Selects the configured LLM provider."""

from __future__ import annotations

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.providers.fal import FalProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider

_provider: LLMProvider | None = None
_is_resolved = False


def get_llm_provider() -> LLMProvider | None:
    global _provider, _is_resolved

    if not _is_resolved:
        _provider = _build_provider()
        _is_resolved = True
    return _provider


def _build_provider() -> LLMProvider | None:
    name = settings.LLM_PROVIDER.strip().lower()

    if name == "fal":
        if not settings.FAL_KEY:
            return None
        return FalProvider(api_key=settings.FAL_KEY, model=settings.LLM_MODEL)

    if name == "openai":
        if not settings.LLM_API_KEY:
            return None
        return OpenAICompatibleProvider(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER!r}")
