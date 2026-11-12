"""``RunHistory`` in a local SQLite file, written by ``healer run`` and read by the panel."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from healer.domain.errors import HistoryError
from healer.domain.history import RunRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    record TEXT NOT NULL
)
"""

_CHUNK = 500


class SqliteHistory:
    """One row per run, as JSON. Only the latest ``max_runs`` are kept. Several processes may
    use the file at once: SQLite locks it for each change. Reading never creates the file,
    so a read-only reader such as the panel can use it."""

    def __init__(self, path: Path, *, max_runs: int) -> None:
        if max_runs < 1:
            raise ValueError("the history must keep at least one run")
        self._path = path
        self._max_runs = max_runs

    def keep(self, record: RunRecord) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs (id, recorded_at, record) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET record = excluded.record",
                (record.run_id, _text(record.recorded_at), record.model_dump_json()),
            )
            db.execute(
                "DELETE FROM runs WHERE id NOT IN "
                "(SELECT id FROM runs ORDER BY recorded_at DESC, id DESC LIMIT ?)",
                (self._max_runs,),
            )

    def get(self, run_id: str) -> RunRecord | None:
        if not self._path.exists():
            return None
        with self._connect() as db:
            row = db.execute("SELECT record FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        try:
            return RunRecord.model_validate_json(str(row[0]))
        except ValidationError as exc:
            raise HistoryError(f"The run {run_id} in {self._path} is corrupt") from exc

    def known(self, run_ids: Iterable[str]) -> set[str]:
        wanted = list(dict.fromkeys(run_ids))
        if not wanted or not self._path.exists():
            return set()
        found: set[str] = set()
        with self._connect() as db:
            for start in range(0, len(wanted), _CHUNK):
                chunk = wanted[start : start + _CHUNK]
                marks = ", ".join("?" * len(chunk))
                rows = db.execute(f"SELECT id FROM runs WHERE id IN ({marks})", chunk)
                found.update(str(identifier) for (identifier,) in rows)
        return found

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise HistoryError(f"Cannot open the history at {self._path}: {exc}") from exc
        try:
            with db:
                db.execute(_SCHEMA)
                yield db
        except sqlite3.Error as exc:
            raise HistoryError(f"Cannot use the history at {self._path}: {exc}") from exc
        finally:
            db.close()


def _text(moment: datetime) -> str:
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.isoformat(timespec="microseconds")
