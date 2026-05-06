from typing import ClassVar

from app.core.config import Settings


class TestingSettings(Settings):
    DB_ROOT_PASSWORD: str = "root_password"
    TEST_DB_NAME: str = "recipes_test_db"
    CHROMA_COLLECTION_NAME: str = "recipes_test"
    SECRET_KEY: str = "test-secret-key-not-for-prod"  # Only used in test DB

    # evaluate.py seeds without tags → embeddings have higher distances than prod.
    # Disable adaptive cutoff so evaluate always finds results (tests search quality,
    # not threshold tuning). Hard limits still apply.
    SEARCH_ABSOLUTE_MAX_DIST: float = 2.0
    SEARCH_RELATIVE_MARGIN: float = 2.0
    SIMILAR_RECIPES_ABSOLUTE_MAX_DIST: float = 2.0
    SIMILAR_RECIPES_RELATIVE_MARGIN: float = 2.0

    THRESHOLDS: ClassVar[dict[str, dict[str, float | int]]] = {
        "Vector Search": {
            "accuracy": 65.0,
            "mean_reciprocal_rank": 0.48,  # 0.497 actual; LLM tag-filter adds latency
            "zero_result_rate": 5.0,
            "avg_f1_score": 0.17,
            "avg_latency": 2000,           # parse_query_intent adds ~0.5-1s per query
        },
        "JSONB GIN Filter": {"accuracy": 90.0, "avg_latency": 15},
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
