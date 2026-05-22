import asyncio
import logging
import os
import sys
from typing import Any, Self, cast

import chromadb
import numpy as np
from chromadb.api.models.Collection import Collection
from chromadb.errors import NotFoundError as ChromaNotFoundError
from chromadb.types import VectorQueryResult
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.core.single_flight import SingleFlight

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])

logger = logging.getLogger(__name__)

# Coalesces concurrent identical search() calls into a single embed+query.
# Per-process; multiple uvicorn workers each get their own registry, which is
# fine because the Redis result cache short-circuits the next identical hit
# from any worker.
_search_single_flight = SingleFlight()


class VectorStore:
    _instance: Self | None = None
    model: SentenceTransformer | None = None
    search_calls_count: int = 0

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        if kwargs.get("force_new", False):
            return super(VectorStore, cls).__new__(cls)
        if cls._instance is None:
            cls._instance = super(VectorStore, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, collection_name: str | None = None, force_new: bool = False) -> None:
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
            self.client = chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)
        except Exception as ex:
            logger.error(f"Failed to connect to ChromaDB: {ex}")

        self.model = None
        self._collection: Collection | None = None

        self._initialized = True
        logger.info("Vector Store client initialized.")

    @property
    def collection(self) -> Collection:
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(name=self.collection_name)
        return self._collection

    def _refresh_collection(self) -> Collection:
        """Re-fetch collection from ChromaDB server, updating the cached UUID.

        Called automatically when a NotFoundError is raised — this happens when
        seed_db.py runs clear() in a separate process, creating a new collection
        with a new UUID while this process still holds the old cached object.
        """
        self._collection = self.client.get_or_create_collection(name=self.collection_name)
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

    _SEARCH_INSTRUCTION = (
        "Instruct: Given a user query about food or cooking, "
        "retrieve the most relevant recipes\nQuery: "
    )

    async def _embed(self, text: str) -> np.ndarray:
        model = self._get_model()

        def _encode(t: str) -> np.ndarray:
            return model.encode(t, convert_to_numpy=True)

        return await asyncio.to_thread(_encode, text)

    async def embed_document(self, text: str) -> np.ndarray:
        """Embed a recipe document (passage) with the 'passage:' prefix."""
        return await self._embed(f"passage: {text}")

    async def embed_query(self, text: str) -> np.ndarray:
        """Embed a search query with the instruct prefix."""
        return await self._embed(f"{self._SEARCH_INSTRUCTION}{text}")

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

        embedding_result = await self.embed_document(full_text)
        embedding_list = embedding_result.tolist()

        def _sync_upsert() -> None:
            self.collection.upsert(
                ids=[str(recipe_id)],
                embeddings=[embedding_list],
                metadatas=[safe_metadata],
                documents=[full_text],
            )

        await asyncio.to_thread(_sync_upsert)

    async def search(self, query: str, n_results: int = 5) -> list[tuple[int, float]]:
        """Return (recipe_id, distance) pairs ordered by ascending distance.

        Wrapped in a single-flight so N concurrent requests for the same query
        share one embedding + ChromaDB call. The redis-backed search_cache
        catches the *next* identical request after this one completes; the
        single-flight is what protects us during the cache-miss window when
        multiple requests arrive in the same few hundred milliseconds.
        """
        VectorStore.search_calls_count += 1
        flight_key = f"vs:search:{n_results}:{query}"
        return await _search_single_flight.do(
            flight_key, lambda: self._search_uncached(query, n_results)
        )

    async def _search_uncached(
        self, query: str, n_results: int
    ) -> list[tuple[int, float]]:
        query_vec_result = await self.embed_query(query)
        query_embedding_list = query_vec_result.tolist()

        def _sync_search() -> VectorQueryResult:
            try:
                query_result = self.collection.query(
                    query_embeddings=[query_embedding_list],
                    n_results=n_results,
                    include=["distances"],
                )
            except ChromaNotFoundError:
                # Collection UUID changed (seed_db.py ran clear() in another process)
                logger.warning("ChromaDB collection not found — refreshing and retrying")
                query_result = self._refresh_collection().query(
                    query_embeddings=[query_embedding_list],
                    n_results=n_results,
                    include=["distances"],
                )
            return cast(VectorQueryResult, query_result)

        results: Any = await asyncio.to_thread(_sync_search)

        ids = (results.get("ids") or [[]])[0] or []
        dists = (results.get("distances") or [[]])[0] or []

        if not ids:
            return []

        return [(int(id_str), float(dist)) for id_str, dist in zip(ids, dists, strict=True)]

    async def search_similar_by_id(
        self, recipe_id: int, n_results: int = 10
    ) -> list[tuple[int, float]]:
        VectorStore.search_calls_count += 1

        def _fetch_embedding() -> Any:
            try:
                col = self.collection
                got = col.get(ids=[str(recipe_id)], include=["embeddings"])
            except ChromaNotFoundError:
                logger.warning("ChromaDB collection not found — refreshing")
                got = self._refresh_collection().get(ids=[str(recipe_id)], include=["embeddings"])
            embs = got.get("embeddings")
            if embs is None or len(embs) == 0:
                return None
            first = embs[0]
            if hasattr(first, "tolist"):
                return first.tolist()  # type: ignore[union-attr]
            return list(first)

        emb = await asyncio.to_thread(_fetch_embedding)
        if emb is None:
            return []

        def _sync_query() -> VectorQueryResult:
            try:
                query_result = self.collection.query(
                    query_embeddings=[emb],
                    n_results=n_results + 1,
                    include=["distances"],
                )
            except ChromaNotFoundError:
                logger.warning("ChromaDB collection not found — refreshing and retrying")
                query_result = self._refresh_collection().query(
                    query_embeddings=[emb],
                    n_results=n_results + 1,
                    include=["distances"],
                )
            return cast(VectorQueryResult, query_result)

        results: Any = await asyncio.to_thread(_sync_query)
        ids = (results.get("ids") or [[]])[0] or []
        dists = (results.get("distances") or [[]])[0] or []

        pairs: list[tuple[int, float]] = []
        for id_str, dist in zip(ids, dists, strict=True):
            rid = int(id_str)
            if rid == recipe_id:
                continue
            pairs.append((rid, float(dist)))
        return pairs[:n_results]

    async def delete_recipe(self, recipe_id: int) -> None:
        await asyncio.to_thread(self.collection.delete, ids=[str(recipe_id)])

    def clear(self) -> None:
        try:
            self.client.delete_collection(self.collection_name)
        except Exception as ex:
            logger.error(f"Failed to delete collection: {ex}")
        self._collection = self.client.get_or_create_collection(name=self.collection_name)


vector_store = VectorStore()
