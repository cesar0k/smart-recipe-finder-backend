"""MCP (Model Context Protocol) adapter for Smart Recipe Finder.

The package is intentionally separate from the FastAPI HTTP layer. In v1 it is
a thin client of the public REST API; later the same tool functions can be
switched to call the service layer directly.
"""
