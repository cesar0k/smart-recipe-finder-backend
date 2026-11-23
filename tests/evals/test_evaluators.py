"""Unit tests for the eval graders (no network or database required)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("pydantic_evals", reason="eval dependencies are optional")

from pydantic_evals.evaluators import EvaluatorContext  # noqa: E402

from evals.mcp_agent.agent import AgentRun, ToolCallRecord  # noqa: E402
from evals.mcp_agent.dataset import EvalCase, Requirement  # noqa: E402
from evals.mcp_agent.evaluators import (  # noqa: E402
    ToolErrors,
    ToolRequirements,
    _args_match,
    _satisfied,
)
from evals.mcp_agent.judge import JudgeAnswer, _format_tool_calls  # noqa: E402


def _context(case: EvalCase, run: AgentRun) -> EvaluatorContext[str, AgentRun, EvalCase]:
    return EvaluatorContext(
        name=case.id,
        inputs=case.prompt,
        metadata=case,
        expected_output=None,
        output=run,
        duration=0.0,
        _span_tree=None,
        attributes={},
        metrics={},
    )


def _run(*calls: ToolCallRecord, answer: str = "Готово") -> AgentRun:
    return AgentRun(answer=answer, tool_calls=list(calls))


def test_args_match_lists_and_numbers() -> None:
    assert _args_match(
        {"include_ingredients": ["куриц", "рис"], "max_time": 15},
        {"include_ingredients": ["курица", "рис"], "max_time": 15},
    )
    assert not _args_match(
        {"include_ingredients": ["курица"]},
        {"include_ingredients": ["рыба"]},
    )
    assert not _args_match({"max_time": 15}, {"max_time": 30})


def test_args_match_strings_are_case_insensitive() -> None:
    assert _args_match({"cuisine": "русск"}, {"cuisine": "Русская"})
    assert _args_match({"query": "салат"}, {"query": "Салаты"})
    assert not _args_match({"query": "салат"}, {"query": "суп"})


def test_requirements_support_tools_and_alternatives() -> None:
    run = _run(
        ToolCallRecord(name="list_cuisines", arguments={}),
        ToolCallRecord(
            name="browse_recipes",
            arguments={"cuisine": "Русская", "difficulty": "easy"},
        ),
    )

    assert _satisfied(Requirement(tools=["list_cuisines"]), run)[0]
    assert _satisfied(
        Requirement(
            any=[
                Requirement(tool="search_recipes"),
                Requirement(tool="browse_recipes", args={"cuisine": "русск"}),
            ]
        ),
        run,
    )[0]
    assert not _satisfied(Requirement(tool="search_recipes"), run)[0]


def test_tool_requirements_reports_missing_call() -> None:
    case = EvalCase(
        id="search",
        prompt="Найди салат",
        require=[Requirement(tool="search_recipes")],
    )

    result = ToolRequirements().evaluate(_context(case, _run()))

    assert result.value is False
    assert "search_recipes was not called" in (result.reason or "")


def test_tool_errors_fail_on_loop_error() -> None:
    case = EvalCase(id="search", prompt="Найди салат")
    run = AgentRun(answer="Не нашёл", error="tool-call loop stopped after 10 steps")

    assert ToolErrors().evaluate(_context(case, run)).value is False


def test_tool_errors_allow_explained_client_error() -> None:
    case = EvalCase(id="missing", prompt="Дай рецепт 999999")
    run = _run(
        ToolCallRecord(
            name="get_recipe", arguments={"recipe_id": 999999}, error="Not found: recipe"
        ),
        answer="Такого рецепта нет.",
    )

    assert ToolErrors().evaluate(_context(case, run)).value is True


def test_tool_errors_fail_on_server_error() -> None:
    case = EvalCase(id="pasta", prompt="Покажи пасту")
    run = _run(
        ToolCallRecord(
            name="browse_recipes",
            arguments={"meal_type": "pasta"},
            error=(
                "Error executing tool browse_recipes: Recipe API error (500): Internal Server Error"
            ),
        ),
        answer="Сервис временно недоступен.",
    )

    assert ToolErrors().evaluate(_context(case, run)).value is False


def test_format_tool_calls_includes_results() -> None:
    run = _run(
        ToolCallRecord(
            name="get_recipe",
            arguments={"recipe_id": 260},
            result='{"title": "Курица"}',
        )
    )

    text = _format_tool_calls(run)

    assert "get_recipe" in text
    assert "result:" in text
    assert "Курица" in text


def test_tool_errors_fail_when_client_error_is_not_explained() -> None:
    case = EvalCase(id="missing", prompt="Дай рецепт 999999")
    run = _run(
        ToolCallRecord(name="get_recipe", arguments={"recipe_id": 999999}, error="Not found"),
        answer="",
    )

    assert ToolErrors().evaluate(_context(case, run)).value is False


class _StubCompletions:
    def __init__(self, text: str) -> None:
        self._text = text

    async def create(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._text))]
        )


class _StubClient:
    def __init__(self, text: str) -> None:
        self.chat = SimpleNamespace(completions=_StubCompletions(text))


def _judge(text: str) -> JudgeAnswer:
    return JudgeAnswer(client=cast(Any, _StubClient(text)), model="stub")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("PASS\nAnswer is grounded.", True),
        ("FAIL\nThe answer invents a recipe.", False),
        ("I cannot decide.", False),
    ],
)
def test_judge_parses_pass_fail_verdicts(reply: str, expected: bool) -> None:
    case = EvalCase(id="judge", prompt="Найди салат")
    result = asyncio.run(_judge(reply).evaluate(_context(case, _run())))

    assert result.value is expected
