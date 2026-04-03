from typing import ClassVar

from app.core.config import Settings


class TestingSettings(Settings):
    DB_ROOT_PASSWORD: str = "root_password"
    TEST_DB_NAME: str = "recipes_test_db"
    CHROMA_COLLECTION_NAME: str = "recipes_test"

    THRESHOLDS: ClassVar[dict[str, dict[str, float | int]]] = {
        "Vector Search": {
            "accuracy": 90.0,
            "mean_reciprocal_rank": 0.70,
            "zero_result_rate": 5.0,
            "avg_f1_score": 0.35,
            "avg_latency": 250,
        },
        "JSONB GIN Filter": {"accuracy": 90.0, "avg_latency": 5},
    }

    @property
    def SYNC_TEST_DATABASE_ADMIN_URL(self) -> str:
        return (
            f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_INTERNAL_PORT}/{self.TEST_DB_NAME}"
        )

    @property
    def ASYNC_TEST_DATABASE_ADMIN_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_INTERNAL_PORT}/{self.TEST_DB_NAME}"
        )


testing_settings = TestingSettings()
