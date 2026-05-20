from typing import Self

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8")

    APP_PORT: int = 8001

    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    BACKEND_CORS_ORIGINS: list[str] = []

    DB_ROOT_PASSWORD: str = ""
    DB_NAME: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_HOST: str = "postgres"
    DB_INTERNAL_PORT: int = 5432

    CHROMA_HOST: str = "chroma"
    CHROMA_PORT: int = 8000
    CHROMA_COLLECTION_NAME: str = ""

    HF_TOKEN: str = ""

    S3_ENDPOINT: str = "http://minio:9000"
    S3_PUBLIC_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET_NAME: str = "recipe-images"

    MAX_FILE_SIZE_MB: int = 10
    ALLOWED_IMAGE_TYPES: list[str] = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    ]
    MAX_IMAGE_WIDTH: int = 8192
    MAX_IMAGE_HEIGHT: int = 8192
    IMAGE_FULL_MAX_WIDTH: int = 1200  # px; full-size WebP output
    IMAGE_FULL_QUALITY: int = 85      # WebP quality for full-size variant
    IMAGE_THUMB_MAX_WIDTH: int = 400  # px; thumbnail WebP output
    IMAGE_THUMB_QUALITY: int = 60     # WebP quality for thumbnail variant

    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-large-instruct"
    LLM_MODEL: str = "google/gemini-3-flash-preview"  # openrouter/router model; override via .env

    SIMILAR_RECIPES_MAX: int = 6
    SIMILAR_RECIPES_THRESHOLD: float = 0.75

    # Adaptive result limits (calibrated on 171-recipe CIS dataset after dedup)
    # Similar recipes: p95=0.30, avg bucket ~7.5 at margin=0.08
    SIMILAR_RECIPES_ABSOLUTE_MAX_DIST: float = 0.30
    SIMILAR_RECIPES_RELATIVE_MARGIN: float = 0.08
    SIMILAR_RECIPES_HARD_LIMIT: int = 10
    # Vector search: p95=0.38
    SEARCH_ABSOLUTE_MAX_DIST: float = 0.38
    SEARCH_RELATIVE_MARGIN: float = 0.13
    SEARCH_HARD_LIMIT: int = 20

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # Caps for downloading a Google profile picture into our own S3 bucket
    # at registration time (see user_service.set_avatar_from_remote_url).
    GOOGLE_AVATAR_FETCH_TIMEOUT_SECONDS: float = 5.0
    GOOGLE_AVATAR_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MiB

    # Max recipe IDs accepted per GET /favorites/check request.
    FAVORITES_CHECK_MAX_IDS: int = 200

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_DEFAULT_TTL: int = 3600  # 1 hour

    # ── SMTP / Email ──────────────────────────────────────────────────────────
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_LOGIN: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@smartrecipefinder.com"
    SMTP_FROM_NAME: str = "Smart Recipe Finder"
    # Set to False to disable all outbound email (useful in dev/tests)
    EMAILS_ENABLED: bool = True

    # Token TTLs for email-based flows
    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 24
    PASSWORD_RESET_TOKEN_EXPIRE_HOURS: int = 1

    # Base URL of the frontend — used to build links inside emails
    FRONTEND_URL: str = "http://localhost:5173"

    # Google reCAPTCHA v3 (invisible scoring)
    RECAPTCHA_SECRET_KEY: str = ""
    # Minimum score to accept (0.0–1.0). Set to 0.0 to disable score check.
    RECAPTCHA_MIN_SCORE: float = 0.5
    # Set to False to skip verification entirely (e.g. in automated tests)
    RECAPTCHA_ENABLED: bool = True
    # Google reCAPTCHA v2 (visible checkbox) — used as a Safari fallback when v3
    # silently fails. Separate key pair, no score (verified by user interaction).
    RECAPTCHA_V2_SECRET_KEY: str = ""

    @model_validator(mode="after")
    def check_required_fields(self) -> Self:
        missing_fields = []
        required_fields = [
            "SECRET_KEY",
            "DB_ROOT_PASSWORD",
            "DB_NAME",
            "DB_USER",
            "DB_PASSWORD",
            "CHROMA_COLLECTION_NAME",
            "S3_ACCESS_KEY",
            "S3_SECRET_KEY",
        ]
        for field in required_fields:
            if not getattr(self, field):
                missing_fields.append(field)

        if missing_fields:
            raise ValueError(f"Missing required environment variables: {','.join(missing_fields)}")

        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_INTERNAL_PORT}/{self.DB_NAME}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SYNC_DATABASE_URL(self) -> str:
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_INTERNAL_PORT}/{self.DB_NAME}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


settings = Settings()
