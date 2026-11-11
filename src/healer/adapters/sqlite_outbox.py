"""``ShareOutbox`` stored in a local SQLite file, shared by ``healer run`` and ``healer listen``."""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from healer.domain.errors import OutboxError
from healer.domain.sharing import FixResult, PendingResult, PendingShare, SharedSolution

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS fixes (
        topic TEXT NOT NULL,
        fix TEXT NOT NULL,
        signature TEXT NOT NULL,
        published_at TEXT NOT NULL,
        queued_at TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        last_error TEXT NOT NULL,
        solution TEXT NOT NULL,
        PRIMARY KEY (topic, fix)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS results (
        topic TEXT NOT NULL,
        fix TEXT NOT NULL,
        reported_at TEXT NOT NULL,
        queued_at TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        last_error TEXT NOT NULL,
        result TEXT NOT NULL,
        PRIMARY KEY (topic, fix)
    )
    """,
)

_OLD_TABLE = "pending"
"""Before ADR 0022 the outbox kept one fix per topic and error signature in this table."""


class SqliteOutbox:
    """One row per topic and fix, and one per topic and result of this node about a fix.

    Times are stored in UTC with microseconds, so they compare correctly as text. Several
    processes may use the file at once: SQLite locks it for each change. Reading never
    creates or changes the file, so a read-only reader such as the panel can use it; the
    first change moves the entries of an older file into the current tables.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def keep(self, pending: PendingShare) -> None:
        solution = pending.solution
        row = (
            pending.topic,
            solution.fix_id,
            solution.signature,
            _text(solution.published_at),
            _text(pending.queued_at),
            pending.attempts,
            pending.last_error,
            solution.model_dump_json(),
        )
        with self._write() as db:
            db.execute(
                "INSERT INTO fixes (topic, fix, signature, published_at, queued_at, attempts, "
                "last_error, solution) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (topic, fix) DO UPDATE SET "
                "published_at = excluded.published_at, queued_at = excluded.queued_at, "
                "attempts = excluded.attempts, last_error = excluded.last_error, "
                "solution = excluded.solution "
                "WHERE excluded.published_at >= fixes.published_at",
                row,
            )

    def pending(self) -> list[PendingShare]:
        with self._read() as db:
            if db is None:
                return []
            table = _first(db, "fixes", _OLD_TABLE)
            if table is None:
                return []
            rows = db.execute(
                f"SELECT topic, queued_at, attempts, last_error, solution FROM {table} "
                "ORDER BY published_at, topic"
            ).fetchall()
        try:
            return [
                PendingShare(
                    solution=SharedSolution.model_validate_json(str(solution)),
                    topic=str(topic),
                    queued_at=datetime.fromisoformat(str(queued_at)),
                    attempts=int(str(attempts)),
                    last_error=str(last_error),
                )
                for topic, queued_at, attempts, last_error, solution in rows
            ]
        except (ValidationError, ValueError) as exc:
            raise OutboxError(f"The outbox at {self._path} has a corrupt entry") from exc

    def remove(self, topic: str, fix: str, *, up_to: datetime) -> None:
        if not self._path.exists():
            return
        with self._write() as db:
            db.execute(
                "DELETE FROM fixes WHERE topic = ? AND fix = ? AND published_at <= ?",
                (topic, fix, _text(up_to)),
            )

    def keep_result(self, pending: PendingResult) -> None:
        result = pending.result
        row = (
            pending.topic,
            result.fix_id,
            _text(result.reported_at),
            _text(pending.queued_at),
            pending.attempts,
            pending.last_error,
            result.model_dump_json(),
        )
        with self._write() as db:
            db.execute(
                "INSERT INTO results (topic, fix, reported_at, queued_at, attempts, "
                "last_error, result) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (topic, fix) DO UPDATE SET "
                "reported_at = excluded.reported_at, queued_at = excluded.queued_at, "
                "attempts = excluded.attempts, last_error = excluded.last_error, "
                "result = excluded.result "
                "WHERE excluded.reported_at >= results.reported_at",
                row,
            )

    def pending_results(self) -> list[PendingResult]:
        with self._read() as db:
            if db is None or _first(db, "results") is None:
                return []
            rows = db.execute(
                "SELECT topic, queued_at, attempts, last_error, result FROM results "
                "ORDER BY reported_at, topic"
            ).fetchall()
        try:
            return [
                PendingResult(
                    result=FixResult.model_validate_json(str(result)),
                    topic=str(topic),
                    queued_at=datetime.fromisoformat(str(queued_at)),
                    attempts=int(str(attempts)),
                    last_error=str(last_error),
                )
                for topic, queued_at, attempts, last_error, result in rows
            ]
        except (ValidationError, ValueError) as exc:
            raise OutboxError(f"The outbox at {self._path} has a corrupt result") from exc

    def remove_result(self, topic: str, fix: str, *, up_to: datetime) -> None:
        if not self._path.exists():
            return
        with self._write() as db:
            db.execute(
                "DELETE FROM results WHERE topic = ? AND fix = ? AND reported_at <= ?",
                (topic, fix, _text(up_to)),
            )

    def clear(self) -> int:
        if not self._path.exists():
            return 0
        with self._write() as db:
            fixes = db.execute("DELETE FROM fixes").rowcount
            return fixes + db.execute("DELETE FROM results").rowcount

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise OutboxError(f"Cannot open the outbox at {self._path}: {exc}") from exc
        try:
            with db:
                for statement in _SCHEMA:
                    db.execute(statement)
                _move_old_entries(db)
                yield db
        except sqlite3.Error as exc:
            raise OutboxError(f"Cannot use the outbox at {self._path}: {exc}") from exc
        finally:
            db.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection | None]:
        if not self._path.exists():
            yield None
            return
        try:
            db = sqlite3.connect(self._path, timeout=10)
        except sqlite3.Error as exc:
            raise OutboxError(f"Cannot open the outbox at {self._path}: {exc}") from exc
        try:
            yield db
        except sqlite3.Error as exc:
            raise OutboxError(f"Cannot use the outbox at {self._path}: {exc}") from exc
        finally:
            db.close()


def _first(db: sqlite3.Connection, *tables: str) -> str | None:
    """The first of ``tables`` that exists in ``db``."""
    present = {
        str(name) for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    return next((table for table in tables if table in present), None)


def _move_old_entries(db: sqlite3.Connection) -> None:
    """Move the fixes of an outbox from before ADR 0022 into ``fixes`` and drop its table.

    An entry that cannot be read keeps its text and gets an id of its own, so it is still
    reported as corrupt instead of being lost.
    """
    if _first(db, _OLD_TABLE) is None:
        return
    rows = db.execute(
        f"SELECT topic, signature, published_at, queued_at, attempts, last_error, solution "
        f"FROM {_OLD_TABLE}"
    ).fetchall()
    for topic, signature, published_at, queued_at, attempts, last_error, solution in rows:
        try:
            fix = SharedSolution.model_validate_json(str(solution)).fix_id
        except ValidationError:
            fix = hashlib.sha256(str(solution).encode()).hexdigest()
        db.execute(
            "INSERT OR IGNORE INTO fixes (topic, fix, signature, published_at, queued_at, "
            "attempts, last_error, solution) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (topic, fix, signature, published_at, queued_at, attempts, last_error, solution),
        )
    db.execute(f"DROP TABLE {_OLD_TABLE}")


def _text(moment: datetime) -> str:
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.isoformat(timespec="microseconds")
