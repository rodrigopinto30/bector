"""``ForgottenFixes`` in the SQLite file of the results of the team's fixes."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from healer.domain.errors import ResultsError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forgotten (
    topic TEXT NOT NULL,
    fix TEXT NOT NULL,
    signature TEXT NOT NULL,
    forgotten_at TEXT NOT NULL,
    PRIMARY KEY (topic, fix)
)
"""

_CHUNK = 500


class SqliteForgotten:
    """One row per topic and forgotten fix. It shares the file of the results, since both
    are what this node knows about the team's fixes. Reading never creates the file."""

    def __init__(self, path: Path, topic: str) -> None:
        self._path = path
        self._topic = topic

    def forget(self, fix: str, signature: str, at: datetime) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO forgotten (topic, fix, signature, forgotten_at) "
                "VALUES (?, ?, ?, ?)",
                (self._topic, fix, signature, _text(at)),
            )

    def forgotten(self, fix_ids: Iterable[str]) -> set[str]:
        wanted = list(dict.fromkeys(fix_ids))
        if not wanted or not self._path.exists():
            return set()
        found: set[str] = set()
        with self._connect() as db:
            for start in range(0, len(wanted), _CHUNK):
                chunk = wanted[start : start + _CHUNK]
                marks = ", ".join("?" * len(chunk))
                rows = db.execute(
                    f"SELECT fix FROM forgotten WHERE topic = ? AND fix IN ({marks})",
                    (self._topic, *chunk),
                )
                found.update(str(fix) for (fix,) in rows)
        return found

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self._path, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise ResultsError(f"Cannot open the results at {self._path}: {exc}") from exc
        try:
            with db:
                db.execute(_SCHEMA)
                yield db
        except sqlite3.Error as exc:
            raise ResultsError(f"Cannot use the results at {self._path}: {exc}") from exc
        finally:
            db.close()


def _text(moment: datetime) -> str:
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.isoformat(timespec="microseconds")
