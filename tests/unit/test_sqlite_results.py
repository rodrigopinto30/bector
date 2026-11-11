from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_results import SqliteResults
from healer.domain.errors import ResultsError
from healer.domain.ranking import Tally
from healer.domain.sharing import FixResult

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TOPIC = "healer/solutions/v1"
FIX = "f" * 64


def result(
    node: str = "laptop-eva", *, worked: bool = True, at: datetime = NOW, fix: str = FIX
) -> FixResult:
    return FixResult(node=node, reported_at=at, signature="a" * 64, fix_id=fix, worked=worked)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "results.sqlite3"


@pytest.fixture
def results(path: Path) -> SqliteResults:
    return SqliteResults(path, TOPIC)


def test_each_node_has_one_vote_per_fix(results: SqliteResults) -> None:
    assert results.record(result("laptop-eva"))
    assert results.record(result("laptop-sol", worked=False))
    assert not results.record(result("laptop-eva", at=NOW + timedelta(hours=1)))
    assert results.tally([FIX, "e" * 64]) == {FIX: Tally(worked=1, failed=1)}


def test_a_node_can_change_its_mind_but_not_go_back_in_time(results: SqliteResults) -> None:
    results.record(result(worked=True))
    assert results.record(result(worked=False, at=NOW + timedelta(hours=1)))
    assert results.tally([FIX]) == {FIX: Tally(worked=0, failed=1)}
    assert not results.record(result(worked=True, at=NOW))
    assert results.tally([FIX]) == {FIX: Tally(worked=0, failed=1)}


def test_each_topic_keeps_its_own_results(path: Path, results: SqliteResults) -> None:
    results.record(result())
    other = SqliteResults(path, "other-team/solutions/v1")
    assert other.tally([FIX]) == {}
    other.record(result(worked=False))
    assert results.tally([FIX]) == {FIX: Tally(worked=1, failed=0)}


def test_many_fixes_are_tallied_at_once(results: SqliteResults) -> None:
    fixes = [f"{n:064x}" for n in range(1_200)]
    for fix in fixes[::100]:
        results.record(result(fix=fix))
    tallies = results.tally(fixes)
    assert set(tallies) == set(fixes[::100])


def test_reading_a_missing_file_creates_nothing(results: SqliteResults, path: Path) -> None:
    assert results.tally([FIX]) == {}
    assert results.tally([]) == {}
    assert not path.parent.exists()


def test_a_file_that_cannot_be_opened_is_reported(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(ResultsError, match="Cannot open the results"):
        SqliteResults(tmp_path / "file" / "results.sqlite3", TOPIC).record(result())


def test_a_damaged_file_is_reported(results: SqliteResults, path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text("not a database")
    with pytest.raises(ResultsError, match="Cannot use the results"):
        results.tally([FIX])
