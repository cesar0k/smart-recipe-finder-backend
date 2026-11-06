"""fal.ai adapter for the LLM provider port."""

from __future__ import annotations

import json
from typing import Any

import fal_client

from app.llm.parsing import parse_json_response

_FAL_APP = "openrouter/router"


class FalProvider:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = fal_client.AsyncClient(key=api_key)
        self._model = model

    async def complete_json(self, *, prompt: str, system_prompt: str) -> dict[str, Any]:
        result = await self._client.run(
            _FAL_APP,
            arguments={
                "model": self._model,
                "system_prompt": system_prompt,
                "prompt": prompt,
                "response_format": json.dumps({"type": "json_object"}),
            },
        )
        return parse_json_response(str(result["output"]))

    async def complete_text(self, *, prompt: str, system_prompt: str) -> str:
        result = await self._client.run(
            _FAL_APP,
            arguments={
                "model": self._model,
                "system_prompt": system_prompt,
                "prompt": prompt,
            },
        )
        return str(result["output"]).strip()
