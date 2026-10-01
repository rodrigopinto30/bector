"""Chroma implementation of ``SymbolRepository``."""

from collections.abc import Iterable, Iterator
from typing import Any

from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from pydantic import ValidationError

from healer.domain.errors import IndexingError
from healer.domain.models import SearchHit, Symbol

BATCH_SIZE = 256
EMBEDDING_TEXT_CHARS = 1_000


class ChromaSymbolRepository:
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

    def indexed_files(self) -> dict[str, str]:
        found = self._collection.get(include=["metadatas"])
        return {str(meta["file"]): str(meta["file_hash"]) for meta in found["metadatas"] or []}

    def replace_file(self, file: str, content_hash: str, symbols: Iterable[Symbol]) -> None:
        self.remove_file(file)
        for batch in _batched(symbols, BATCH_SIZE):
            self._collection.upsert(
                ids=[s.id for s in batch],
                documents=[_embedding_text(s) for s in batch],
                metadatas=[_metadata(s, content_hash) for s in batch],
            )

    def remove_file(self, file: str) -> None:
        self._collection.delete(where={"file": file})

    def symbols_in_file(self, file: str) -> list[Symbol]:
        found = self._collection.get(where={"file": file}, include=["metadatas"])
        symbols = [_to_symbol(meta) for meta in found["metadatas"] or []]
        return sorted(symbols, key=lambda s: (s.start_line, -s.end_line, s.qualified_name))

    def search(self, query: str, limit: int) -> list[SearchHit]:
        total = self._collection.count()
        if total == 0 or limit < 1:
            return []
        result = self._collection.query(
            query_texts=[query],
            n_results=min(limit, total),
            include=["metadatas", "distances"],
        )
        metadatas = (result["metadatas"] or [[]])[0]
        distances = (result["distances"] or [[]])[0]
        return [
            SearchHit(symbol=_to_symbol(meta), distance=distance)
            for meta, distance in zip(metadatas, distances, strict=True)
        ]

    def count(self) -> int:
        return self._collection.count()


def _embedding_text(symbol: Symbol) -> str:
    """Text that gets embedded: names and docs first, because the model truncates long inputs."""
    parts = [symbol.qualified_name, symbol.signature, symbol.docstring, symbol.source]
    return "\n".join(p for p in parts if p)[:EMBEDDING_TEXT_CHARS]


def _metadata(symbol: Symbol, content_hash: str) -> dict[str, str | int]:
    return {
        "file": symbol.file,
        "file_hash": content_hash,
        "kind": symbol.kind.value,
        "name": symbol.name,
        "qualified_name": symbol.qualified_name,
        "start_line": symbol.start_line,
        "end_line": symbol.end_line,
        "symbol_json": symbol.model_dump_json(),
    }


def _to_symbol(meta: Any) -> Symbol:
    try:
        return Symbol.model_validate_json(str(meta["symbol_json"]))
    except (KeyError, ValidationError) as exc:
        raise IndexingError(
            "Index contains a corrupt symbol record; re-run 'healer index'."
        ) from exc


def _batched(items: Iterable[Symbol], size: int) -> Iterator[list[Symbol]]:
    batch: list[Symbol] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
