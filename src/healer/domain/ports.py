"""Interfaces the domain and services depend on. Adapters implement them."""

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from healer.domain.diagnosis import TracedError
from healer.domain.execution import RunResult
from healer.domain.ledger import LedgerEntry
from healer.domain.listener import ListenerStatus
from healer.domain.models import FileMap, SearchHit, SourceFile, Symbol
from healer.domain.patch import AppliedPatch, Patch
from healer.domain.proposal import Proposal
from healer.domain.sharing import PendingShare, ReceivedSolution, TeamMatch
from healer.domain.solution import Solution


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
    def apply(
        self, patch: Patch, root: Path, *, expected: Mapping[str, str] | None = None
    ) -> AppliedPatch:
        """Apply every edit under ``root`` or none of them.

        With ``expected`` (path to SHA-256), nothing is written unless each of those files
        still has that content.
        """
        ...


class SymbolSearch(Protocol):
    def search(self, query: str, limit: int) -> list[SearchHit]: ...


class PatchProposer(Protocol):
    """A language model that answers a fix request with a patch."""

    def propose(self, system: str, prompt: str) -> Proposal:
        """Return the proposal; raise ``LLMError`` when the model gives no usable answer."""
        ...


class SolutionRepository(Protocol):
    """Verified solutions of this workspace, one per error signature."""

    def find(self, signature: str) -> Solution | None: ...

    def save(self, solution: Solution) -> None:
        """Store ``solution``, replacing any earlier one for the same signature."""
        ...

    def remove(self, signature: str) -> None: ...


class Ledger(Protocol):
    """Append-only record of what each incident cost."""

    def record(self, entry: LedgerEntry) -> None: ...

    def entries(self, since: datetime | None = None) -> list[LedgerEntry]:
        """Return the entries recorded at or after ``since``, oldest first."""
        ...


class SolutionPublisher(Protocol):
    """Sends a sealed solution to the other nodes."""

    @property
    def topic(self) -> str: ...

    def publish(self, message: bytes) -> None:
        """Raise ``SharingError`` when the broker did not accept the message."""
        ...


class ShareOutbox(Protocol):
    """Solutions the broker did not take, one per topic and error signature."""

    def keep(self, pending: PendingShare) -> None:
        """Store ``pending``, replacing an entry for the same topic and signature unless
        that one was published later. Raise ``OutboxError`` if it cannot be stored."""
        ...

    def pending(self) -> list[PendingShare]:
        """Return every entry, the earliest published first."""
        ...

    def remove(self, topic: str, signature: str, *, up_to: datetime) -> None:
        """Forget the entry for ``topic`` and ``signature`` if it was published no later
        than ``up_to``, so a newer one stored meanwhile is kept."""
        ...

    def clear(self) -> int:
        """Forget every entry and return how many there were."""
        ...


class SolutionSubscriber(Protocol):
    """Receives the messages other nodes publish."""

    def run(self, on_message: Callable[[bytes], None], on_status: Callable[[str], None]) -> None:
        """Deliver messages until ``stop`` is called.

        ``on_status`` gets a line each time the connection changes; it starts with
        ``connected`` once this node is connected. Raise ``SharingError`` when the broker
        refuses this node.
        """
        ...

    def stop(self) -> None: ...


class TeamSolutionRepository(Protocol):
    """Suggestions received from other nodes: one per fix, several per error signature."""

    def for_signature(self, signature: str) -> list[ReceivedSolution]:
        """Return the suggestions for that error signature, the most recently found first."""
        ...

    def save(self, received: ReceivedSolution) -> None:
        """Store ``received``, replacing only a stored copy of the same fix."""
        ...

    def remove(self, received: ReceivedSolution) -> None:
        """Forget the fix in ``received``; the other fixes for its error stay."""
        ...

    def all(self) -> list[ReceivedSolution]:
        """Return every suggestion, the most recently received first."""
        ...

    def similar(self, error: str, limit: int, *, other_than: str = "") -> list[TeamMatch]:
        """Return the suggestions whose error reads most like ``error``, closest first,
        leaving out those for the error signature ``other_than``."""
        ...


class ListenerStatusStore(Protocol):
    """Where the listener keeps the record of itself that the panel reads."""

    def save(self, status: ListenerStatus) -> None:
        """Replace the record. Raise ``StatusError`` if it cannot be written."""
        ...

    def load(self) -> ListenerStatus | None:
        """Return the record, or None if the listener never ran."""
        ...


class ProjectStore(Protocol):
    """What each project keeps, read without creating anything."""

    def symbols(self, project: str) -> int | None:
        """Return the symbols in the code index of ``project``, or None if never indexed."""
        ...

    def fixes(self, project: str) -> list[Solution]:
        """Return the verified fixes of ``project``, the most recent first."""
        ...

    def team(self) -> list[ReceivedSolution]:
        """Return the suggestions from the team, the most recently received first."""
        ...


class Checkable(Protocol):
    """Something whose health can be checked, like the broker or the test sandbox."""

    def check(self) -> None:
        """Raise ``HealerError`` saying what is wrong."""
        ...
