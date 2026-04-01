import asyncio
import logging
import os
import sys
from typing import Any, Self, cast

import chromadb
import numpy as np
from chromadb.api.models.Collection import Collection
from chromadb.types import VectorQueryResult
from sentence_transformers import SentenceTransformer

from app.core.config import settings

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])

logger = logging.getLogger(__name__)


class VectorStore:
    _instance: Self | None = None
    model: SentenceTransformer | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        if kwargs.get("force_new", False):
            return super(VectorStore, cls).__new__(cls)
        if cls._instance is None:
            cls._instance = super(VectorStore, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self, collection_name: str | None = None, force_new: bool = False
    ) -> None:
        if getattr(self, "_initialized", False) and not force_new:
            return

        env_collection = os.getenv("CHROMA_COLLECTION_NAME")
        if collection_name:
            self.collection_name = collection_name
        elif env_collection:
            self.collection_name = env_collection
        else:
            self.collection_name = "recipes"

        try:
            self.client = chromadb.HttpClient(
                host=settings.CHROMA_HOST, port=settings.CHROMA_PORT
            )
        except Exception as ex:
            logger.error(f"Failed to connect to ChromaDB: {ex}")

        self.model = None
        self._collection: Collection | None = None

        self._initialized = True
        logger.info("Vector Store client initialized.")

    @property
    def collection(self) -> Collection:
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name
            )
        return self._collection

    def preload_model(self) -> None:
        if self.model is None:
            logger.info(f"Pre-load embedding model: {settings.EMBEDDING_MODEL}...")
            self.model = SentenceTransformer(
                settings.EMBEDDING_MODEL,
                trust_remote_code=True,
                token=settings.HF_TOKEN,
            )
            logger.info("Embedding model pre-loaded successfully.")

    def _get_model(self) -> SentenceTransformer:
        if self.model is None:
            self.preload_model()

            if self.model is None:
                raise RuntimeError("Failed to load SentenceTransformer model.")
        return self.model

    async def embed_text(self, text: str) -> np.ndarray:
        model = self._get_model()

        def _encode(t: str) -> np.ndarray:
            # Helper to resolve overloaded model.encode for mypy
            return model.encode(t, convert_to_numpy=True)

        embedding = await asyncio.to_thread(_encode, text)
        return embedding

    async def upsert_recipe(
        self,
        recipe_id: int,
        title: str,
        full_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if metadata is None:
            metadata = {"title": title}
        safe_metadata = {k: ("" if v is None else v) for k, v in metadata.items()}

        embedding_result = await self.embed_text(full_text)
        embedding_list = embedding_result.tolist()

        def _sync_upsert() -> None:
            self.collection.upsert(
                ids=[str(recipe_id)],
                embeddings=[embedding_list],
                metadatas=[safe_metadata],
                documents=[full_text],
            )

        await asyncio.to_thread(_sync_upsert)

    async def search(self, query: str, n_results: int = 5) -> list[int]:
        query_vec_result = await self.embed_text(query)
        query_embedding_list = query_vec_result.tolist()

        def _sync_search() -> VectorQueryResult:
            query_result = self.collection.query(
                query_embeddings=[query_embedding_list], n_results=n_results
            )
            return cast(VectorQueryResult, query_result)

        results: Any = await asyncio.to_thread(_sync_search)

        if not results.get("ids") or not results["ids"][0]:
            return []

        return [int(id_str) for id_str in results["ids"][0]]

    async def delete_recipe(self, recipe_id: int) -> None:
        await asyncio.to_thread(self.collection.delete, ids=[str(recipe_id)])

    def clear(self) -> None:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception as ex:
            logger.error(f"Failed to delete collection: {ex}")
        self._collection = self.client.get_or_create_collection(
            name=self.collection_name
        )


vector_store = VectorStore()
