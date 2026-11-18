"""Tests for MCP prompt templates (no network required)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.mcp.server import build_server


def _text(result: Any) -> str:
    """Concatenate text blocks from a rendered prompt result."""
    return "\n".join(
        message.content.text for message in result.messages if message.content.type == "text"
    )


def test_prompts_are_listed_with_arguments() -> None:
    prompts = {prompt.name: prompt for prompt in asyncio.run(build_server().list_prompts())}

    assert set(prompts) == {"plan_weekly_menu", "cook_from_fridge"}

    plan_args = {arg.name: arg for arg in prompts["plan_weekly_menu"].arguments or []}
    assert set(plan_args) == {"days", "dietary", "max_time", "cuisine"}
    assert all(not arg.required for arg in plan_args.values())

    fridge_args = {arg.name: arg for arg in prompts["cook_from_fridge"].arguments or []}
    assert set(fridge_args) == {"ingredients", "max_extra", "max_time"}
    assert fridge_args["ingredients"].required is True
    assert fridge_args["max_extra"].required is False


def test_plan_weekly_menu_renders_constraints() -> None:
    result = asyncio.run(
        build_server().get_prompt(
            "plan_weekly_menu",
            {"days": "3", "dietary": "vegetarian", "max_time": "30"},
        )
    )
    text = _text(result)

    assert "exactly 3 dinners" in text
    assert "Dietary constraint: vegetarian" in text
    assert "at most 30 minutes" in text
    assert "get_recipe" in text
    assert "untrusted data" in text


def test_blank_arguments_fall_back_to_defaults() -> None:
    # OpenCode sends an empty string for every argument when the user runs a
    # prompt without typing anything. That must not reach Pydantic as "".
    result = asyncio.run(
        build_server().get_prompt(
            "plan_weekly_menu",
            {"days": "", "dietary": "", "max_time": "", "cuisine": ""},
        )
    )
    text = _text(result)

    assert "exactly 5 dinners" in text
    assert "None" not in text


def test_cook_from_fridge_uses_defaults() -> None:
    result = asyncio.run(
        build_server().get_prompt("cook_from_fridge", {"ingredients": "eggs, cheese"})
    )
    text = _text(result)

    assert "eggs, cheese" in text
    assert "at most 2 extra ingredients" in text
    assert "include_ingredients" in text


def test_cook_from_fridge_requires_ingredients() -> None:
    with pytest.raises(ValueError):
        asyncio.run(build_server().get_prompt("cook_from_fridge", {}))


def test_cook_from_fridge_accepts_blank_optional_arguments() -> None:
    result = asyncio.run(
        build_server().get_prompt(
            "cook_from_fridge",
            {"ingredients": "eggs, cheese", "max_extra": "", "max_time": ""},
        )
    )
    text = _text(result)

    assert "eggs, cheese" in text
    assert "at most 2 extra ingredients" in text
    assert "include_ingredients" in text
