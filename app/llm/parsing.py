"""Shared helpers for parsing LLM responses."""

from __future__ import annotations

import json
from typing import Any


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1 :] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("LLM returned JSON that is not an object")
    return parsed
