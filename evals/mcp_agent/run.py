"""Run agent-level evals against the MCP server.

Usage:
    uv run python -m evals.mcp_agent.run                    # all cases
    uv run python -m evals.mcp_agent.run --limit 3          # smoke test
    uv run python -m evals.mcp_agent.run --case search-salads
    uv run python -m evals.mcp_agent.run --no-judge         # tool checks only, no LLM judge

The REST API must be running (``docker compose up``); the runner starts its own
stdio MCP server and points it at ``SRF_API_URL``.
"""

import argparse
import asyncio
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic_evals.evaluators import Evaluator
from pydantic_evals.reporting import EvaluationReport

from evals.mcp_agent.agent import AgentRun, MCPAgent
from evals.mcp_agent.config import EvalSettings
from evals.mcp_agent.dataset import EvalCase, build_dataset, load_cases
from evals.mcp_agent.evaluators import ToolErrors, ToolRequirements
from evals.mcp_agent.judge import JudgeAnswer

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "mcp_agent.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MCP agent evals")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--case", action="append", default=[], help="case id; repeatable")
    parser.add_argument("--limit", type=int, default=None, help="run the first N cases")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="include the prompt and the full agent answer in the report",
    )
    return parser.parse_args()


def _judge(settings: EvalSettings) -> JudgeAnswer:
    client = AsyncOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY or "unused",
    )
    return JudgeAnswer(client=client, model=settings.LLM_MODEL)


def _select_cases(cases: list[EvalCase], args: argparse.Namespace) -> list[EvalCase]:
    if args.case:
        known = {case.id for case in cases}
        unknown = set(args.case) - known
        if unknown:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case.id in set(args.case)]
    if args.limit is not None:
        cases = cases[: args.limit]
    return cases


def _failed_cases(report: EvaluationReport[str, AgentRun, EvalCase]) -> int:
    failed = len(report.failures)
    for case in report.cases:
        if case.evaluator_failures:
            failed += 1
            continue
        if any(not result.value for result in case.assertions.values()):
            failed += 1
    return failed


async def _main(args: argparse.Namespace) -> int:
    settings = EvalSettings()
    if not settings.LLM_MODEL:
        raise SystemExit("LLM_MODEL is not set; evals need an OpenAI-compatible endpoint")

    cases = _select_cases(load_cases(args.dataset), args)
    if not cases:
        raise SystemExit("No cases selected")

    judge: Evaluator[Any, Any, Any] | None = None
    if not args.no_judge:
        judge = _judge(settings)

    dataset = build_dataset(
        cases,
        name=args.dataset.stem,
        judge=judge,
        evaluators=[ToolRequirements(), ToolErrors()],
    )

    try:
        async with MCPAgent(settings) as agent:
            report = await dataset.evaluate(
                agent.run,
                max_concurrency=args.concurrency or settings.EVAL_CONCURRENCY,
            )
    finally:
        if isinstance(judge, JudgeAnswer):
            await judge.client.close()

    report.print(
        width=160,
        include_input=args.verbose,
        include_output=args.verbose,
        include_reasons=True,
        include_error_stacktrace=True,
    )

    failed = _failed_cases(report)
    print(f"\n{failed} of {len(report.cases)} case(s) failed")
    return 1 if failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(_main(_parse_args())))


if __name__ == "__main__":
    main()
