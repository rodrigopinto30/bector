"""Chroma implementation of ``SolutionRepository``."""

from typing import Any

from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from pydantic import ValidationError

from healer.domain.errors import IndexingError
from healer.domain.solution import Solution


class ChromaSolutionRepository:
    """The error signature is the record id, so saving the same error twice replaces it.

    The error summary is embedded so that similar errors can be matched by meaning
    once solutions are shared between machines (Phase 5).
    """

    def __init__(
        self,
        client: ClientAPI,
        collection_name: str,
        embedding_function: EmbeddingFunction[Any] | None = None,
    ) -> None:
        embedder: Any = embedding_function or DefaultEmbeddingFunction()
        self._collection = client.get_or_create_collection(
            collection_name,
            embedding_function=embedder,
            configuration={"hnsw": {"space": "cosine"}},
        )

    def find(self, signature: str) -> Solution | None:
        found = self._collection.get(ids=[signature], include=["metadatas"])
        metadatas = found["metadatas"] or []
        if not metadatas:
            return None
        try:
            return Solution.model_validate_json(str(metadatas[0]["solution_json"]))
        except (KeyError, ValidationError) as exc:
            raise IndexingError(f"Stored solution {signature[:12]} is corrupt") from exc

    def save(self, solution: Solution) -> None:
        self._collection.upsert(
            ids=[solution.signature],
            documents=[solution.error],
            metadatas=[
                {
                    "signature": solution.signature,
                    "verified_at": solution.verified_at.isoformat(),
                    "tokens_spent": solution.tokens_spent,
                    "solution_json": solution.model_dump_json(),
                }
            ],
        )

    def remove(self, signature: str) -> None:
        self._collection.delete(ids=[signature])

    def count(self) -> int:
        return self._collection.count()
