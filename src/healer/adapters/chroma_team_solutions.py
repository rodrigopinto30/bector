"""Chroma implementation of ``TeamSolutionRepository``."""

from typing import Any, cast

from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction, Where
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from pydantic import ValidationError

from healer.domain.errors import IndexingError
from healer.domain.sharing import ReceivedSolution, TeamMatch


class ChromaTeamSolutions:
    """The fix id, from the error signature and the edits, is the record id, so the same
    fix is never stored twice and several fixes for one error live side by side (ADR 0021).

    Records saved before that, one per error, have the signature as their id. They are
    found and removed through the signature kept in their metadata, like the others.

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

    def for_signature(self, signature: str) -> list[ReceivedSolution]:
        records = [received for _, received in self._records(signature)]
        return sorted(records, key=lambda r: r.solution.published_at, reverse=True)

    def save(self, received: ReceivedSolution) -> None:
        solution = received.solution
        self._collection.upsert(
            ids=[solution.fix_id],
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

    def remove(self, received: ReceivedSolution) -> None:
        fix = received.solution.fix_id
        ids = [
            identifier
            for identifier, stored in self._records(received.solution.signature)
            if stored.solution.fix_id == fix
        ]
        if ids:
            self._collection.delete(ids=ids)

    def all(self) -> list[ReceivedSolution]:
        found = self._collection.get(include=["metadatas"])
        records = [
            _parse(metadata, identifier)
            for identifier, metadata in zip(found["ids"], found["metadatas"] or [], strict=True)
        ]
        return sorted(records, key=lambda r: r.received_at, reverse=True)

    def similar(self, error: str, limit: int, *, other_than: str = "") -> list[TeamMatch]:
        total = self._collection.count()
        if total == 0 or limit < 1:
            return []
        where = cast(Where, {"signature": {"$ne": other_than}}) if other_than else None
        found = self._collection.query(
            query_texts=[error],
            n_results=min(limit, total),
            where=where,
            include=["metadatas", "distances"],
        )
        identifiers = found["ids"][0]
        metadatas = (found["metadatas"] or [[]])[0]
        distances = (found["distances"] or [[]])[0]
        return [
            TeamMatch(received=_parse(metadata, identifier), distance=max(distance, 0.0))
            for identifier, metadata, distance in zip(
                identifiers, metadatas, distances, strict=True
            )
        ]

    def count(self) -> int:
        return self._collection.count()

    def _records(self, signature: str) -> list[tuple[str, ReceivedSolution]]:
        found = self._collection.get(where={"signature": signature}, include=["metadatas"])
        return [
            (identifier, _parse(metadata, identifier))
            for identifier, metadata in zip(found["ids"], found["metadatas"] or [], strict=True)
        ]


def _parse(metadata: Any, identifier: str) -> ReceivedSolution:
    try:
        return ReceivedSolution.model_validate_json(str(metadata["received_json"]))
    except (KeyError, ValidationError) as exc:
        raise IndexingError(f"Stored team solution {identifier[:12]} is corrupt") from exc
