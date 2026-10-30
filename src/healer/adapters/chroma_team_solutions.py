"""Chroma implementation of ``TeamSolutionRepository``."""

from typing import Any

from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from pydantic import ValidationError

from healer.domain.errors import IndexingError
from healer.domain.sharing import ReceivedSolution


class ChromaTeamSolutions:
    """The error signature is the record id, so the same error is never stored twice (7.6).

    The error summary is embedded so that similar errors can be matched by meaning. The
    collection belongs to the node, not to a workspace, because a signature means the
    same in every project.
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

    def find(self, signature: str) -> ReceivedSolution | None:
        found = self._collection.get(ids=[signature], include=["metadatas"])
        metadatas = found["metadatas"] or []
        if not metadatas:
            return None
        return _parse(metadatas[0], signature)

    def save(self, received: ReceivedSolution) -> None:
        solution = received.solution
        self._collection.upsert(
            ids=[solution.signature],
            documents=[solution.error],
            metadatas=[
                {
                    "signature": solution.signature,
                    "node": solution.node,
                    "published_at": solution.published_at.isoformat(),
                    "received_at": received.received_at.isoformat(),
                    "received_json": received.model_dump_json(),
                }
            ],
        )

    def all(self) -> list[ReceivedSolution]:
        found = self._collection.get(include=["metadatas"])
        records = [
            _parse(metadata, identifier)
            for identifier, metadata in zip(found["ids"], found["metadatas"] or [], strict=True)
        ]
        return sorted(records, key=lambda r: r.received_at, reverse=True)

    def count(self) -> int:
        return self._collection.count()


def _parse(metadata: Any, signature: str) -> ReceivedSolution:
    try:
        return ReceivedSolution.model_validate_json(str(metadata["received_json"]))
    except (KeyError, ValidationError) as exc:
        raise IndexingError(f"Stored team solution {signature[:12]} is corrupt") from exc
