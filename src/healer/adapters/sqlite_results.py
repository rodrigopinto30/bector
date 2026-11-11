"""``ResultStore`` in a local SQLite file, shared by ``healer run`` and ``healer listen``."""

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from healer.domain.errors import ResultsError
from healer.domain.ranking import Tally
from healer.domain.sharing import FixResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    topic TEXT NOT NULL,
    fix TEXT NOT NULL,
    node TEXT NOT NULL,
    worked INTEGER NOT NULL,
    reported_at TEXT NOT NULL,
    PRIMARY KEY (topic, fix, node)
)
"""

_CHUNK = 500


class SqliteResults:
    """One row per topic, fix and node: the node's latest result. Times are stored in UTC
    with microseconds, so they compare correctly as text. Several processes may use the
    file at once: SQLite locks it for each change. Reading never creates the file, so a
    read-only reader such as the panel can use it."""

    def __init__(self, path: Path, topic: str) -> None:
        self._path = path
        self._topic = topic

    def record(self, result: FixResult) -> bool:
        key = (self._topic, result.fix_id, result.node)
        with self._connect() as db:
            known = db.execute(
                "SELECT worked, reported_at FROM results WHERE topic = ? AND fix = ? AND node = ?",
                key,
            ).fetchone()
            at = _text(result.reported_at)
            if known is not None and str(known[1]) >= at:
                return False
            db.execute(
                "INSERT INTO results (topic, fix, node, worked, reported_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (topic, fix, node) DO UPDATE SET "
                "worked = excluded.worked, reported_at = excluded.reported_at",
                (*key, int(result.worked), at),
            )
        return known is None or bool(known[0]) != result.worked

    def tally(self, fix_ids: Iterable[str]) -> dict[str, Tally]:
        wanted = list(dict.fromkeys(fix_ids))
        if not wanted or not self._path.exists():
            return {}
        tallies: dict[str, Tally] = {}
        with self._connect() as db:
            for start in range(0, len(wanted), _CHUNK):
                chunk = wanted[start : start + _CHUNK]
                marks = ", ".join("?" * len(chunk))
                rows = db.execute(
                    "SELECT fix, SUM(worked), COUNT(*) - SUM(worked) FROM results "
                    f"WHERE topic = ? AND fix IN ({marks}) GROUP BY fix",
                    (self._topic, *chunk),
                ).fetchall()
                for fix, worked, failed in rows:
                    tallies[str(fix)] = Tally(worked=int(worked), failed=int(failed))
        return tallies

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
