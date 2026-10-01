"""Interfaces the domain and services depend on. Adapters implement them."""

from collections.abc import Iterable, Iterator
from typing import Protocol

from healer.domain.models import FileMap, SearchHit, SourceFile, Symbol


class SourceScanner(Protocol):
    """Reads the files of a workspace that are eligible for indexing."""

    def scan(self) -> Iterator[SourceFile]: ...


class CodeParser(Protocol):
    """Turns source text into a structural map. One implementation per language."""

    def parse(self, file: SourceFile) -> FileMap: ...


class SymbolRepository(Protocol):
    """Persistent store of symbols, searchable by meaning."""

    def indexed_files(self) -> dict[str, str]:
        """Return ``{path: content_hash}`` for every file currently in the index."""
        ...

    def replace_file(self, file: str, content_hash: str, symbols: Iterable[Symbol]) -> None:
        """Atomically swap everything stored for ``file`` with ``symbols``."""
        ...

    def remove_file(self, file: str) -> None: ...

    def symbols_in_file(self, file: str) -> list[Symbol]:
        """Return the symbols of ``file`` ordered by start line."""
        ...

    def search(self, query: str, limit: int) -> list[SearchHit]: ...

    def count(self) -> int: ...
