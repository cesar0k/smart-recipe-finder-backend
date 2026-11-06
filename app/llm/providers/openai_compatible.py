"""OpenAI-compatible adapter for the LLM provider port."""

from __future__ import annotations

from typing import Any

import httpx

from app.llm.parsing import parse_json_response


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def complete_json(self, *, prompt: str, system_prompt: str) -> dict[str, Any]:
        content = await self._chat(
            prompt=prompt,
            system_prompt=system_prompt,
            json_mode=True,
        )
        return parse_json_response(content)

    async def complete_text(self, *, prompt: str, system_prompt: str) -> str:
        content = await self._chat(
            prompt=prompt,
            system_prompt=system_prompt,
            json_mode=False,
        )
        return content.strip()

    async def _chat(self, *, prompt: str, system_prompt: str, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        return str(data["choices"][0]["message"]["content"])
