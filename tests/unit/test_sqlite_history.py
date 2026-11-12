import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_history import SqliteHistory
from healer.domain.errors import HistoryError
from healer.domain.history import RunOfTests, RunRecord, run_id
from healer.domain.repair import RepairStatus

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def run(minutes: int = 0, *, status: RepairStatus = RepairStatus.FIXED) -> RunRecord:
    at = NOW + timedelta(minutes=minutes)
    return RunRecord(
        run_id=run_id("/workspace", at),
        project="/workspace",
        recorded_at=at,
        command="python -m pytest",
        status=status,
        error="KeyError: 'db'",
        error_type="KeyError",
        signature="a" * 64,
        baseline=RunOfTests(exit_code=1, duration_seconds=1.0, passed=False),
        attempts=(),
    )


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "history.sqlite3"


@pytest.fixture
def history(path: Path) -> SqliteHistory:
    return SqliteHistory(path, max_runs=3)


def test_keeps_and_finds_runs(history: SqliteHistory) -> None:
    first, second = run(0), run(1)
    history.keep(first)
    history.keep(second)
    assert history.get(first.run_id) == first
    assert history.get("f" * 16) is None
    assert history.known([first.run_id, second.run_id, "f" * 16]) == {
        first.run_id,
        second.run_id,
    }


def test_the_same_run_kept_again_replaces_itself(history: SqliteHistory) -> None:
    history.keep(run(0))
    stopped = run(0, status=RepairStatus.STOPPED)
    history.keep(stopped)
    assert history.get(stopped.run_id) == stopped


def test_only_the_latest_runs_are_kept(history: SqliteHistory) -> None:
    runs = [run(minute) for minute in (3, 0, 1, 2)]
    for item in runs:
        history.keep(item)
    assert history.known(item.run_id for item in runs) == {r.run_id for r in runs if r != runs[1]}


def test_many_runs_are_looked_up_at_once(path: Path) -> None:
    history = SqliteHistory(path, max_runs=2_000)
    runs = [run(minute) for minute in range(1_200)]
    for item in runs[::100]:
        history.keep(item)
    assert history.known(item.run_id for item in runs) == {r.run_id for r in runs[::100]}


def test_reading_a_missing_history_creates_nothing(history: SqliteHistory, path: Path) -> None:
    assert history.get("f" * 16) is None
    assert history.known(["f" * 16]) == set()
    assert history.known([]) == set()
    assert not path.parent.exists()


def test_a_corrupt_run_is_reported(history: SqliteHistory, path: Path) -> None:
    kept = run()
    history.keep(kept)
    with sqlite3.connect(path) as db:
        db.execute("""UPDATE runs SET record = '{"not": "a run"}'""")
    with pytest.raises(HistoryError, match="is corrupt"):
        history.get(kept.run_id)


def test_a_history_that_cannot_be_opened_or_used_is_reported(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(HistoryError, match="Cannot open the history"):
        SqliteHistory(tmp_path / "file" / "history.sqlite3", max_runs=1).keep(run())
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_text("not a database")
    with pytest.raises(HistoryError, match="Cannot use the history"):
        SqliteHistory(damaged, max_runs=1).get("f" * 16)


def test_a_history_must_keep_at_least_one_run(path: Path) -> None:
    with pytest.raises(ValueError, match="at least one run"):
        SqliteHistory(path, max_runs=0)


def test_a_run_never_holds_the_raw_output_of_the_tests() -> None:
    assert "output" not in RunOfTests.model_fields
