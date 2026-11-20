"""Agent-level evals for the MCP server.

The package is intentionally separate from ``app/``: evals need an LLM, an MCP
client and a dataset, while the API process needs none of that. The agent loop
here is the seed of the future product harness; the eval runner only depends on
``MCPAgent.run`` and the ``AgentRun`` result, so the loop can be replaced
without touching the dataset or the graders.
"""
