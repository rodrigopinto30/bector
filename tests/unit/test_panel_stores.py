from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_actions import SqliteActions
from healer.adapters.sqlite_forgotten import SqliteForgotten
from healer.domain.actions import ActionKind, PanelAction
from healer.domain.errors import HistoryError, ResultsError

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def action(minutes: int, title: str = "Outbox sent") -> PanelAction:
    return PanelAction(
        at=NOW + timedelta(minutes=minutes), kind=ActionKind.SEND, title=title, detail="x"
    )


def test_forgotten_fixes_are_kept_per_topic(tmp_path: Path) -> None:
    path = tmp_path / "data" / "results.sqlite3"
    here = SqliteForgotten(path, "healer/solutions/v1")
    assert here.forgotten(["a" * 64]) == set()
    assert not path.parent.exists()
    here.forget("a" * 64, "s" * 64, NOW)
    here.forget("a" * 64, "s" * 64, NOW + timedelta(hours=1))
    assert here.forgotten(["a" * 64, "b" * 64]) == {"a" * 64}
    assert SqliteForgotten(path, "other/solutions/v1").forgotten(["a" * 64]) == set()
    many = [f"{n:064x}" for n in range(1_200)]
    assert here.forgotten(many + ["a" * 64]) == {"a" * 64}


def test_a_forgotten_list_that_cannot_be_opened_is_reported(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(ResultsError, match="Cannot open the results"):
        SqliteForgotten(tmp_path / "file" / "results.sqlite3", "t").forget("a", "s", NOW)
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_text("not a database")
    with pytest.raises(ResultsError, match="Cannot use the results"):
        SqliteForgotten(damaged, "t").forgotten(["a"])


def test_actions_are_listed_newest_first_and_only_the_latest_stay(tmp_path: Path) -> None:
    path = tmp_path / "data" / "history.sqlite3"
    log = SqliteActions(path, kept=3)
    assert log.recent(5) == []
    assert not path.parent.exists()
    for minutes in (2, 0, 3, 1):
        log.record(action(minutes, f"at {minutes}"))
    assert [a.title for a in log.recent(5)] == ["at 3", "at 2", "at 1"]
    assert [a.title for a in log.recent(1)] == ["at 3"]
    assert log.recent(0) == []


def test_a_corrupt_or_unusable_action_log_is_reported(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "history.sqlite3"
    log = SqliteActions(path)
    log.record(action(0))
    with sqlite3.connect(path) as db:
        db.execute("""UPDATE actions SET action = '{"not": "an action"}'""")
    with pytest.raises(HistoryError, match="is corrupt"):
        log.recent(5)
    (tmp_path / "file").write_text("")
    with pytest.raises(HistoryError, match="Cannot open the history"):
        SqliteActions(tmp_path / "file" / "history.sqlite3").record(action(0))
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_text("not a database")
    with pytest.raises(HistoryError, match="Cannot use the history"):
        SqliteActions(damaged).recent(1)
