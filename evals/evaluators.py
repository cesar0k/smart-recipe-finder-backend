"""Custom graders for MCP agent runs.

The graders read expectations from case metadata, so adding a case means editing
JSON, not Python. They check the tool trace; answer quality is left to
``JudgeAnswer``.
"""

import re
from typing import Any

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from evals.agent import AgentRun, ToolCallRecord
from evals.dataset import EvalCase, Requirement

# Only known client errors are recoverable: the agent can react to a missing
# recipe or a rejected filter. Everything else (5xx, timeouts, unexpected MCP
# exceptions) makes the run invalid. MCP wraps expected ToolErrors as
# "Error executing tool <name>: <message>", so the message is what we inspect.
_RECOVERABLE_TOOL_ERROR = re.compile(
    r"Not found:|Recipe API error \(4(?!01|03)\d\d\)",
    re.IGNORECASE,
)


def _calls(run: AgentRun, tool: str) -> list[ToolCallRecord]:
    return [call for call in run.tool_calls if call.name == tool]


def _text_matches(expected: str, actual: str) -> bool:
    expected_text = expected.casefold().strip()
    actual_text = actual.casefold().strip()
    return expected_text in actual_text or actual_text in expected_text


def _item_matches(expected: str, actual: Any) -> bool:
    return isinstance(actual, str) and _text_matches(expected, actual)


def _args_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if expected_value is None:
            if actual_value not in (None, "", []):
                return False
            continue
        if isinstance(expected_value, list):
            actual_list = actual_value if isinstance(actual_value, list) else [actual_value]
            if not all(
                any(_item_matches(item, candidate) for candidate in actual_list)
                for item in expected_value
            ):
                return False
            continue
        if isinstance(expected_value, str):
            if not isinstance(actual_value, str) or not _text_matches(expected_value, actual_value):
                return False
            continue
        if expected_value != actual_value:
            return False
    return True


def _satisfied(requirement: Requirement, run: AgentRun) -> tuple[bool, str]:
    if requirement.any:
        reasons = []
        for alternative in requirement.any:
            ok, reason = _satisfied(alternative, run)
            if ok:
                return True, ""
            reasons.append(reason)
        return False, "none of the alternatives matched: " + "; ".join(reasons)

    if requirement.tools:
        missing = [tool for tool in requirement.tools if not _calls(run, tool)]
        if missing:
            return False, f"missing tool calls: {', '.join(missing)}"
        return True, ""

    if requirement.tool:
        calls = _calls(run, requirement.tool)
        if not calls:
            return False, f"{requirement.tool} was not called"
        if not requirement.args:
            return True, ""
        if any(_args_match(requirement.args, call.arguments) for call in calls):
            return True, ""
        seen = [call.arguments for call in calls]
        return False, f"{requirement.tool} called with {seen}, expected {requirement.args}"

    return True, ""


class ToolRequirements(Evaluator[str, AgentRun, EvalCase]):
    """Check that the agent used the right tools with the right arguments."""

    def evaluate(self, ctx: EvaluatorContext[str, AgentRun, EvalCase]) -> EvaluationReason:
        case = ctx.metadata
        if case is None:
            return EvaluationReason(value=False, reason="case metadata is missing")

        problems = []
        for requirement in case.require:
            ok, reason = _satisfied(requirement, ctx.output)
            if not ok:
                problems.append(reason)

        return EvaluationReason(
            value=not problems,
            reason="; ".join(problems) or "all tool requirements met",
        )


class ToolErrors(Evaluator[str, AgentRun, EvalCase]):
    """Fail only when a tool failure makes the run invalid.

    A 4xx from the recipe API is part of normal agent behaviour: the agent asks
    for a recipe that does not exist or passes a filter the API rejects, and is
    expected to explain it. Those cases pass as long as the answer is not empty.
    Loop errors, unexpected MCP exceptions and 5xx responses mean the run tells
    us nothing about tool behaviour, so they fail the case.
    """

    def evaluate(self, ctx: EvaluatorContext[str, AgentRun, EvalCase]) -> EvaluationReason:
        if ctx.output.error:
            return EvaluationReason(value=False, reason=ctx.output.error)

        errors = [call.error for call in ctx.output.tool_calls if call.error]
        fatal = [error for error in errors if not _RECOVERABLE_TOOL_ERROR.search(error)]
        if fatal:
            return EvaluationReason(value=False, reason="; ".join(fatal))

        if errors and not ctx.output.answer.strip():
            return EvaluationReason(
                value=False,
                reason="a tool failed but the agent did not explain it",
            )

        return EvaluationReason(
            value=True,
            reason="recoverable tool errors were handled" if errors else "no tool errors",
        )
