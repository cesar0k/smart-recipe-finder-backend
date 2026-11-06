"""Provider-agnostic interface for LLM calls."""

from __future__ import annotations

from typing import Any, Protocol


class LLMProvider(Protocol):
    async def complete_json(self, *, prompt: str, system_prompt: str) -> dict[str, Any]: ...

    async def complete_text(self, *, prompt: str, system_prompt: str) -> str: ...
