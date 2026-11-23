"""Quality evaluations: retrieval benchmarks and agent-level MCP evals.

Both live here so that "eval" means one place. ``evals.retrieval`` is the
deterministic search benchmark that runs in CI; ``evals.mcp_agent`` drives a
real LLM through the MCP server and runs nightly.
"""
