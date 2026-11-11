import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_outbox import SqliteOutbox
from healer.domain.errors import OutboxError
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import Evidence, FixResult, PendingResult, PendingShare, SharedSolution

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
TOPIC = "healer/solutions/v1"


def pending(
    signature: str = "abc123",
    *,
    published_at: datetime = NOW,
    topic: str = TOPIC,
    attempts: int = 1,
    explanation: str = "Use a default.",
    new: str = 'c.get("db")',
) -> PendingShare:
    solution = SharedSolution(
        node="laptop-ana",
        published_at=published_at,
        signature=signature,
        error="KeyError: 'db'",
        patch=Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new=new),)),
        explanation=explanation,
        model="claude-sonnet-5-5",
        tokens_spent=2_900,
        evidence=Evidence(command="python -m pytest", exit_code_before=1, verified_at=NOW),
    )
    return PendingShare(
        solution=solution,
        topic=topic,
        queued_at=published_at,
        attempts=attempts,
        last_error="the broker could not be reached",
    )


def waiting_result(
    fix: str = "f" * 64,
    *,
    at: datetime = NOW,
    worked: bool = True,
    topic: str = TOPIC,
    attempts: int = 1,
) -> PendingResult:
    return PendingResult(
        result=FixResult(
            node="laptop-luis", reported_at=at, signature="abc123", fix_id=fix, worked=worked
        ),
        topic=topic,
        queued_at=at,
        attempts=attempts,
        last_error="the broker could not be reached",
    )


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "outbox.sqlite3"


@pytest.fixture
def outbox(path: Path) -> SqliteOutbox:
    return SqliteOutbox(path)


def test_keeps_what_it_is_given(outbox: SqliteOutbox) -> None:
    entry = pending()
    outbox.keep(entry)
    assert outbox.pending() == [entry]


def test_different_fixes_for_the_same_error_wait_side_by_side(outbox: SqliteOutbox) -> None:
    first = pending()
    other = pending(published_at=NOW + timedelta(minutes=5), new="None")
    outbox.keep(first)
    outbox.keep(other)
    assert outbox.pending() == [first, other]


def test_the_same_fix_found_again_replaces_its_earlier_copy(outbox: SqliteOutbox) -> None:
    outbox.keep(pending())
    later = pending(published_at=NOW + timedelta(minutes=5), explanation="Found again.")
    outbox.keep(later)
    assert outbox.pending() == [later]


def test_an_earlier_copy_never_replaces_a_later_one(outbox: SqliteOutbox) -> None:
    later = pending(published_at=NOW + timedelta(microseconds=1))
    outbox.keep(later)
    outbox.keep(pending())
    assert outbox.pending() == [later]


def test_the_same_fix_again_records_another_attempt(outbox: SqliteOutbox) -> None:
    outbox.keep(pending())
    outbox.keep(pending(attempts=2))
    [entry] = outbox.pending()
    assert entry.attempts == 2


def test_each_topic_and_error_is_kept_apart_earliest_first(outbox: SqliteOutbox) -> None:
    outbox.keep(pending("b", published_at=NOW + timedelta(minutes=2)))
    outbox.keep(pending("a", published_at=NOW + timedelta(minutes=1)))
    outbox.keep(pending("a", topic="other-team/solutions/v1"))
    assert [(e.topic, e.solution.signature) for e in outbox.pending()] == [
        ("other-team/solutions/v1", "a"),
        (TOPIC, "a"),
        (TOPIC, "b"),
    ]


def test_remove_forgets_one_fix_unless_a_later_copy_waits(outbox: SqliteOutbox) -> None:
    later = pending(published_at=NOW + timedelta(minutes=5))
    other = pending(new="None")
    outbox.keep(later)
    outbox.keep(other)
    fix = later.solution.fix_id
    outbox.remove(TOPIC, fix, up_to=NOW)
    outbox.remove("other-team/solutions/v1", fix, up_to=later.solution.published_at)
    assert outbox.pending() == [other, later]
    outbox.remove(TOPIC, fix, up_to=later.solution.published_at)
    assert outbox.pending() == [other]


def test_results_wait_one_per_fix_the_newest(outbox: SqliteOutbox) -> None:
    outbox.keep_result(waiting_result(worked=True))
    newer = waiting_result(at=NOW + timedelta(minutes=1), worked=False)
    outbox.keep_result(newer)
    outbox.keep_result(waiting_result(at=NOW - timedelta(minutes=1)))
    other = waiting_result("e" * 64, at=NOW + timedelta(minutes=2))
    outbox.keep_result(other)
    assert outbox.pending_results() == [newer, other]
    assert outbox.pending() == []


def test_remove_result_keeps_a_newer_one(outbox: SqliteOutbox) -> None:
    newer = waiting_result(at=NOW + timedelta(minutes=1))
    outbox.keep_result(newer)
    outbox.remove_result(TOPIC, "f" * 64, up_to=NOW)
    assert outbox.pending_results() == [newer]
    outbox.remove_result(TOPIC, "f" * 64, up_to=newer.result.reported_at)
    assert outbox.pending_results() == []


def test_clear_says_how_many_it_forgot(outbox: SqliteOutbox) -> None:
    outbox.keep(pending("a"))
    outbox.keep(pending("b"))
    outbox.keep_result(waiting_result())
    assert outbox.clear() == 3
    assert outbox.pending() == []
    assert outbox.pending_results() == []


def test_reading_a_missing_outbox_creates_nothing(outbox: SqliteOutbox, tmp_path: Path) -> None:
    assert outbox.pending() == []
    assert outbox.pending_results() == []
    outbox.remove(TOPIC, "abc123", up_to=NOW)
    outbox.remove_result(TOPIC, "abc123", up_to=NOW)
    assert outbox.clear() == 0
    assert not (tmp_path / "data").exists()


def test_another_process_sees_the_same_entries(outbox: SqliteOutbox, path: Path) -> None:
    outbox.keep(pending())
    assert len(SqliteOutbox(path).pending()) == 1


def test_a_corrupt_entry_is_reported(outbox: SqliteOutbox, path: Path) -> None:
    outbox.keep(pending())
    outbox.keep_result(waiting_result())
    with sqlite3.connect(path) as db:
        db.execute('UPDATE fixes SET solution = \'{"not": "a solution"}\'')
        db.execute('UPDATE results SET result = \'{"not": "a result"}\'')
    with pytest.raises(OutboxError, match="has a corrupt entry"):
        outbox.pending()
    with pytest.raises(OutboxError, match="has a corrupt result"):
        outbox.pending_results()


def test_an_outbox_that_cannot_be_opened_is_reported(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(OutboxError, match="Cannot open the outbox"):
        SqliteOutbox(tmp_path / "file" / "outbox.sqlite3").keep(pending())


def old_outbox(path: Path, *entries: PendingShare, corrupt: str = "") -> None:
    """An outbox as written before ADR 0022: one fix per topic and error signature."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE pending (topic TEXT NOT NULL, signature TEXT NOT NULL, "
            "published_at TEXT NOT NULL, queued_at TEXT NOT NULL, attempts INTEGER NOT NULL, "
            "last_error TEXT NOT NULL, solution TEXT NOT NULL, PRIMARY KEY (topic, signature))"
        )
        rows = [
            (
                e.topic,
                e.solution.signature,
                e.solution.published_at.isoformat(timespec="microseconds"),
                e.queued_at.isoformat(timespec="microseconds"),
                e.attempts,
                e.last_error,
                e.solution.model_dump_json(),
            )
            for e in entries
        ]
        if corrupt:
            rows.append((TOPIC, "zzz", NOW.isoformat(), NOW.isoformat(), 1, "x", corrupt))
        db.executemany("INSERT INTO pending VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def test_an_older_outbox_is_read_as_it_is_and_moved_on_the_first_change(
    outbox: SqliteOutbox, path: Path
) -> None:
    first, second = pending("a"), pending("b", published_at=NOW + timedelta(minutes=1))
    old_outbox(path, first, second)
    assert outbox.pending() == [first, second]
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master")}
    assert "pending" in tables and "fixes" not in tables

    another = pending("a", published_at=NOW + timedelta(minutes=2), new="None")
    outbox.keep(another)
    assert outbox.pending() == [first, second, another]
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master")}
    assert "pending" not in tables


def test_an_unreadable_entry_of_an_older_outbox_is_moved_and_still_reported(
    outbox: SqliteOutbox, path: Path
) -> None:
    old_outbox(path, pending("a"), corrupt='{"not": "a solution"}')
    outbox.keep_result(waiting_result())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM fixes").fetchone() == (2,)
    with pytest.raises(OutboxError, match="has a corrupt entry"):
        outbox.pending()


def test_a_read_only_reader_never_changes_the_file(outbox: SqliteOutbox, path: Path) -> None:
    old_outbox(path, pending("a"))
    path.chmod(0o444)
    try:
        assert [e.solution.signature for e in outbox.pending()] == ["a"]
        assert outbox.pending_results() == []
    finally:
        path.chmod(0o644)


def test_an_empty_database_has_nothing_waiting(outbox: SqliteOutbox, path: Path) -> None:
    path.parent.mkdir(parents=True)
    sqlite3.connect(path).close()
    assert outbox.pending() == []
    assert outbox.pending_results() == []


def test_a_damaged_file_is_reported_when_read_or_written(outbox: SqliteOutbox, path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text("not a database")
    with pytest.raises(OutboxError, match="Cannot use the outbox"):
        outbox.pending()
    with pytest.raises(OutboxError, match="Cannot use the outbox"):
        outbox.keep(pending())


def test_a_folder_in_place_of_the_file_is_reported(outbox: SqliteOutbox, path: Path) -> None:
    path.mkdir(parents=True)
    with pytest.raises(OutboxError, match="Cannot open the outbox"):
        outbox.pending_results()
