"""``ShareOutbox`` stored in a local SQLite file, shared by ``healer run`` and ``healer listen``."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from healer.domain.errors import OutboxError
from healer.domain.sharing import PendingShare, SharedSolution

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    topic TEXT NOT NULL,
    signature TEXT NOT NULL,
    published_at TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    solution TEXT NOT NULL,
    PRIMARY KEY (topic, signature)
)
"""


class SqliteOutbox:
    """One row per topic and error signature. Times are stored in UTC with microseconds, so
    they compare correctly as text. Several processes may use the file at once: SQLite
    locks it for each change."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def keep(self, pending: PendingShare) -> None:
        solution = pending.solution
        row = (
            pending.topic,
            solution.signature,
            _text(solution.published_at),
            _text(pending.queued_at),
            pending.attempts,
            pending.last_error,
            solution.model_dump_json(),
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO pending (topic, signature, published_at, queued_at, attempts, "
                "last_error, solution) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (topic, signature) DO UPDATE SET "
                "published_at = excluded.published_at, queued_at = excluded.queued_at, "
                "attempts = excluded.attempts, last_error = excluded.last_error, "
                "solution = excluded.solution "
                "WHERE excluded.published_at >= pending.published_at",
                row,
            )

    def pending(self) -> list[PendingShare]:
        if not self._path.exists():
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT topic, queued_at, attempts, last_error, solution FROM pending "
                "ORDER BY published_at, topic, signature"
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

    def remove(self, topic: str, signature: str, *, up_to: datetime) -> None:
        if not self._path.exists():
            return
        with self._connect() as db:
            db.execute(
                "DELETE FROM pending WHERE topic = ? AND signature = ? AND published_at <= ?",
                (topic, signature, _text(up_to)),
            )

    def clear(self) -> int:
        if not self._path.exists():
            return 0
        with self._connect() as db:
            return db.execute("DELETE FROM pending").rowcount

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise OutboxError(f"Cannot open the outbox at {self._path}: {exc}") from exc
        try:
            with db:
                db.execute(_SCHEMA)
                yield db
        except sqlite3.Error as exc:
            raise OutboxError(f"Cannot use the outbox at {self._path}: {exc}") from exc
        finally:
            db.close()


def _text(moment: datetime) -> str:
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.isoformat(timespec="microseconds")
