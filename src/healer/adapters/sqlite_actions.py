"""``ActionLog`` in the SQLite file of the history of runs."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from healer.domain.actions import ACTIONS_KEPT, PanelAction
from healer.domain.errors import HistoryError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    action TEXT NOT NULL
)
"""


class SqliteActions:
    """One row per action, as JSON. Only the latest ``ACTIONS_KEPT`` stay. It shares the
    file of the history, since both are what happened on this node. Reading never creates
    the file."""

    def __init__(self, path: Path, *, kept: int = ACTIONS_KEPT) -> None:
        self._path = path
        self._kept = kept

    def record(self, action: PanelAction) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO actions (at, action) VALUES (?, ?)",
                (_text(action.at), action.model_dump_json()),
            )
            db.execute(
                "DELETE FROM actions WHERE id NOT IN "
                "(SELECT id FROM actions ORDER BY at DESC, id DESC LIMIT ?)",
                (self._kept,),
            )

    def recent(self, limit: int) -> list[PanelAction]:
        if limit < 1 or not self._path.exists():
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT action FROM actions ORDER BY at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        try:
            return [PanelAction.model_validate_json(str(row)) for (row,) in rows]
        except ValidationError as exc:
            raise HistoryError(f"An action in {self._path} is corrupt") from exc

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
