"""Settings for the MCP agent evals."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class EvalSettings(BaseSettings):
    """Read eval settings from the same ``.env`` as the API.

    Only the LLM endpoint and the API URL are needed, so this deliberately does
    not import ``app.core.config`` (which pulls in database and storage
    settings that evals never use).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = ""

    # REST API the MCP server talks to. Inside Docker Compose use http://app:8000.
    SRF_API_URL: str = "http://localhost:8001"
    # The MCP server's HTTP timeout. Search can be slow while the embedding
    # model warms up, so evals are more patient than the default.
    SRF_API_TIMEOUT: float = 60.0

    # How many cases run at once. Each case is one conversation plus one judge call.
    EVAL_CONCURRENCY: int = 3
    # Guard against a model that keeps calling tools forever.
    EVAL_MAX_STEPS: int = 10
    # MCP server command; empty means "the interpreter running the evals".
    EVAL_MCP_COMMAND: str = ""
