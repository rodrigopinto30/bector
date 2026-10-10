"""Interfaces the domain and services depend on. Adapters implement them."""

from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Protocol

from healer.domain.diagnosis import TracedError
from healer.domain.execution import RunResult
from healer.domain.models import FileMap, SearchHit, SourceFile, Symbol
from healer.domain.patch import AppliedPatch, Patch
from healer.domain.proposal import Proposal


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


class TraceParser(Protocol):
    """Recovers exceptions from raw output. One implementation per language."""

    def parse(self, text: str) -> list[TracedError]: ...


class PathResolver(Protocol):
    """Maps a path printed in a trace to a file of the workspace."""

    def resolve(self, raw_path: str) -> str | None:
        """Return the workspace-relative path, or None for files outside the workspace."""
        ...


class SymbolLocator(Protocol):
    def enclosing_symbol(self, file: str, line: int) -> Symbol | None:
        """Return the narrowest indexed symbol of ``file`` that contains ``line``."""
        ...


class Sandbox(Protocol):
    """A disposable copy of the workspace. Nothing done inside reaches the real files."""

    @property
    def root(self) -> Path: ...

    @property
    def files_copied(self) -> int: ...

    def close(self) -> None:
        """Delete the copy. Safe to call more than once."""
        ...


class SandboxProvider(Protocol):
    def create(self) -> Sandbox: ...


class CommandRunner(Protocol):
    """Runs an already validated argv, without a shell, under time and output limits."""

    def run(self, argv: Sequence[str], cwd: Path) -> RunResult: ...


class PatchApplier(Protocol):
    def apply(self, patch: Patch, root: Path) -> AppliedPatch:
        """Apply every edit under ``root`` or none of them."""
        ...


class SymbolSearch(Protocol):
    def search(self, query: str, limit: int) -> list[SearchHit]: ...


class PatchProposer(Protocol):
    """A language model that answers a fix request with a patch."""

    def propose(self, system: str, prompt: str) -> Proposal:
        """Return the proposal; raise ``LLMError`` when the model gives no usable answer."""
        ...
