import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_outbox import SqliteOutbox
from healer.domain.errors import OutboxError
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import Evidence, PendingShare, SharedSolution

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
TOPIC = "healer/solutions/v1"


def pending(
    signature: str = "abc123",
    *,
    published_at: datetime = NOW,
    topic: str = TOPIC,
    attempts: int = 1,
    explanation: str = "Use a default.",
) -> PendingShare:
    solution = SharedSolution(
        node="laptop-ana",
        published_at=published_at,
        signature=signature,
        error="KeyError: 'db'",
        patch=Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new='c.get("db")'),)),
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


@pytest.fixture
def outbox(tmp_path: Path) -> SqliteOutbox:
    return SqliteOutbox(tmp_path / "data" / "outbox.sqlite3")


def test_keeps_what_it_is_given(outbox: SqliteOutbox) -> None:
    entry = pending()
    outbox.keep(entry)
    assert outbox.pending() == [entry]


def test_a_later_fix_for_the_same_error_replaces_the_earlier_one(outbox: SqliteOutbox) -> None:
    outbox.keep(pending())
    later = pending(published_at=NOW + timedelta(minutes=5), explanation="A better default.")
    outbox.keep(later)
    assert outbox.pending() == [later]


def test_an_earlier_fix_never_replaces_a_later_one(outbox: SqliteOutbox) -> None:
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


def test_remove_keeps_a_fix_published_after_up_to(outbox: SqliteOutbox) -> None:
    later = pending(published_at=NOW + timedelta(minutes=5))
    outbox.keep(later)
    outbox.remove(TOPIC, "abc123", up_to=NOW)
    assert outbox.pending() == [later]
    outbox.remove("other-team/solutions/v1", "abc123", up_to=later.solution.published_at)
    assert outbox.pending() == [later]
    outbox.remove(TOPIC, "abc123", up_to=later.solution.published_at)
    assert outbox.pending() == []


def test_clear_says_how_many_it_forgot(outbox: SqliteOutbox) -> None:
    outbox.keep(pending("a"))
    outbox.keep(pending("b"))
    assert outbox.clear() == 2
    assert outbox.pending() == []


def test_reading_a_missing_outbox_creates_nothing(outbox: SqliteOutbox, tmp_path: Path) -> None:
    assert outbox.pending() == []
    outbox.remove(TOPIC, "abc123", up_to=NOW)
    assert outbox.clear() == 0
    assert not (tmp_path / "data").exists()


def test_another_process_sees_the_same_entries(outbox: SqliteOutbox, tmp_path: Path) -> None:
    outbox.keep(pending())
    assert len(SqliteOutbox(tmp_path / "data" / "outbox.sqlite3").pending()) == 1


def test_a_corrupt_entry_is_reported(outbox: SqliteOutbox, tmp_path: Path) -> None:
    outbox.keep(pending())
    with sqlite3.connect(tmp_path / "data" / "outbox.sqlite3") as db:
        db.execute('UPDATE pending SET solution = \'{"not": "a solution"}\'')
    with pytest.raises(OutboxError, match="has a corrupt entry"):
        outbox.pending()


def test_an_outbox_that_cannot_be_opened_is_reported(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(OutboxError, match="Cannot open the outbox"):
        SqliteOutbox(tmp_path / "file" / "outbox.sqlite3").keep(pending())
