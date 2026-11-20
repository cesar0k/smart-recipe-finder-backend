"""Dataset loading and conversion to Pydantic Evals cases."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator

from evals.agent import AgentRun


class Requirement(BaseModel):
    """A condition on the tool trace of one run.

    Exactly one of the forms is used:

    - ``{"tool": ..., "args": {...}}`` — the tool was called with matching args;
    - ``{"tools": [...]}`` — every listed tool was called;
    - ``{"any": [...]}`` — at least one nested requirement holds.
    """

    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    any: list["Requirement"] = Field(default_factory=list)


class EvalCase(BaseModel):
    """One natural-language request and the tool behaviour it expects."""

    id: str
    prompt: str
    require: list[Requirement] = Field(default_factory=list)
    # Set to false for cases where only tool selection matters.
    judge: bool = True


class EvalDataset(BaseModel):
    name: str = "mcp-agent"
    cases: list[EvalCase]


Requirement.model_rebuild()


def load_cases(path: Path) -> list[EvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EvalDataset.model_validate(payload).cases


def build_dataset(
    cases: list[EvalCase],
    name: str,
    judge: Evaluator[Any, Any, Any] | None = None,
    evaluators: list[Evaluator[Any, Any, Any]] | None = None,
) -> Dataset[str, AgentRun, EvalCase]:
    return Dataset[str, AgentRun, EvalCase](
        name=name,
        cases=[
            Case[str, AgentRun, EvalCase](
                name=case.id,
                inputs=case.prompt,
                metadata=case,
                evaluators=[judge] if judge is not None and case.judge else [],
            )
            for case in cases
        ],
        evaluators=evaluators or [],
    )
