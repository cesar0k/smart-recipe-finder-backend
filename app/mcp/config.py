"""Configuration for the standalone MCP server.

This module deliberately does not import ``app.core.config``: the MCP process
only needs the API URL, so it should not fail to start when database/S3/Chroma
settings are absent.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Base URL of the REST API. Inside Docker Compose use http://app:8000.
    SRF_API_URL: str = "http://localhost:8001"
    # Public frontend URL used to build human-readable recipe links.
    SRF_WEB_URL: str = "http://localhost:5173"
    SRF_API_TIMEOUT: float = 30.0

    MCP_TRANSPORT: str = "stdio"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8002


settings = MCPSettings()
