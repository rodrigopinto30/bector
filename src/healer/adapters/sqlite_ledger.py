"""``Ledger`` stored in a local SQLite file."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from healer.domain.errors import LedgerError
from healer.domain.ledger import LedgerEntry, Resolution
from healer.domain.proposal import TokenUsage
from healer.domain.repair import RepairStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    project TEXT NOT NULL,
    signature TEXT NOT NULL,
    error_type TEXT NOT NULL,
    status TEXT NOT NULL,
    resolution TEXT NOT NULL,
    model_attempts INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    tokens_saved INTEGER NOT NULL
)
"""
_COLUMNS = (
    "recorded_at, project, signature, error_type, status, resolution, model_attempts, "
    "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, tokens_saved"
)


class SqliteLedger:
    """One row per incident. Times are stored in UTC, so they sort as text."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, entry: LedgerEntry) -> None:
        usage = entry.usage
        row = (
            _utc(entry.recorded_at).isoformat(),
            entry.project,
            entry.signature,
            entry.error_type,
            entry.status.value,
            entry.resolution.value,
            entry.model_attempts,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            entry.tokens_saved,
        )
        with self._connect() as db:
            db.execute(f"INSERT INTO incidents ({_COLUMNS}) VALUES ({', '.join('?' * 12)})", row)

    def entries(self, since: datetime | None = None) -> list[LedgerEntry]:
        if not self._path.exists():
            return []
        query = f"SELECT {_COLUMNS} FROM incidents"
        params: tuple[str, ...] = ()
        if since is not None:
            query += " WHERE recorded_at >= ?"
            params = (_utc(since).isoformat(),)
        with self._connect() as db:
            rows = db.execute(query + " ORDER BY recorded_at, id", params).fetchall()
        try:
            return [_to_entry(row) for row in rows]
        except ValueError as exc:
            raise LedgerError(f"The ledger at {self._path} has a corrupt record") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path)
        except (OSError, sqlite3.Error) as exc:
            raise LedgerError(f"Cannot open the ledger at {self._path}: {exc}") from exc
        try:
            with db:
                db.execute(_SCHEMA)
                yield db
        except sqlite3.Error as exc:
            raise LedgerError(f"Cannot use the ledger at {self._path}: {exc}") from exc
        finally:
            db.close()


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _to_entry(row: tuple[object, ...]) -> LedgerEntry:
    (at, project, signature, error_type, status, resolution, attempts, *tokens) = row
    input_tokens, output_tokens, cache_read, cache_write, saved = (int(str(t)) for t in tokens)
    return LedgerEntry(
        recorded_at=datetime.fromisoformat(str(at)),
        project=str(project),
        signature=str(signature),
        error_type=str(error_type),
        status=RepairStatus(str(status)),
        resolution=Resolution(str(resolution)),
        model_attempts=int(str(attempts)),
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
        tokens_saved=saved,
    )
