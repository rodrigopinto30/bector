from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_outbox import SqliteOutbox
from healer.domain.actions import ActionKind
from healer.domain.errors import ResultsError, SharingError
from healer.domain.patch import FileEdit, Patch
from healer.domain.redaction import Redactor
from healer.domain.sharing import Evidence, PendingShare, ReceivedSolution, SharedSolution
from healer.services.panel_actions import PanelActions
from healer.services.solution_sharer import SolutionSharer
from tests.conftest import FakeActionLog, FakeForgotten

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
TOPIC = "healer/solutions/v1"
KEY = b"0123456789abcdef-team-key"


def shared(new: str = 'c.get("db")', node: str = "laptop-ana") -> SharedSolution:
    return SharedSolution(
        node=node,
        published_at=NOW,
        signature="a" * 64,
        error="KeyError: 'db'",
        patch=Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new=new),)),
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=2_900,
        evidence=Evidence(command="python -m pytest", exit_code_before=1, verified_at=NOW),
    )


class Publisher:
    def __init__(self, *, down: bool = False, topic: str = TOPIC) -> None:
        self.down = down
        self.topic = topic
        self.messages: list[bytes] = []

    def publish(self, message: bytes) -> None:
        if self.down:
            raise SharingError("the broker at mosquitto:1883 could not be reached")
        self.messages.append(message)


@pytest.fixture
def outbox(tmp_path: Path) -> SqliteOutbox:
    return SqliteOutbox(tmp_path / "outbox.sqlite3")


def actions(
    outbox: SqliteOutbox,
    *,
    publisher: Publisher | None = None,
    team: list[ReceivedSolution] | None = None,
    forgotten: FakeForgotten | None = None,
    log: FakeActionLog | None = None,
) -> PanelActions:
    def sharer() -> SolutionSharer:
        assert publisher is not None
        return SolutionSharer(
            publisher,
            Redactor(),
            node="laptop-luis",
            key=KEY,
            max_bytes=10_000,
            outbox=outbox,
            results_publisher=publisher,
        )

    return PanelActions(
        outbox=outbox,
        team=lambda: team or [],
        forgotten=forgotten if forgotten is not None else FakeForgotten(),
        log=log if log is not None else FakeActionLog(),
        sharer=sharer if publisher is not None else None,
        clock=lambda: NOW,
    )


def waiting(outbox: SqliteOutbox, solution: SharedSolution, topic: str = TOPIC) -> None:
    outbox.keep(
        PendingShare(solution=solution, topic=topic, queued_at=NOW, attempts=1, last_error="down")
    )


def test_sending_needs_sharing_on(outbox: SqliteOutbox) -> None:
    outcome = actions(outbox).send_outbox()
    assert not outcome.ok and "SHARE_SOLUTIONS=true" in outcome.message


def test_what_waits_is_sent_and_recorded(outbox: SqliteOutbox) -> None:
    waiting(outbox, shared())
    publisher, log = Publisher(), FakeActionLog()
    outcome = actions(outbox, publisher=publisher, log=log).send_outbox()
    assert (outcome.ok, outcome.message) == (True, "Sent 1 fix.")
    assert len(publisher.messages) == 1 and outbox.pending() == []
    assert [(a.kind, a.title) for a in log.actions] == [(ActionKind.SEND, "Outbox sent")]


def test_a_broker_that_is_down_is_told_and_nothing_is_recorded(outbox: SqliteOutbox) -> None:
    waiting(outbox, shared())
    log = FakeActionLog()
    outcome = actions(outbox, publisher=Publisher(down=True), log=log).send_outbox()
    assert not outcome.ok
    assert outcome.message == (
        "Sent nothing; 1 fix still waiting: the broker at mosquitto:1883 could not be reached"
    )
    assert log.actions == [] and len(outbox.pending()) == 1


def test_one_fix_waiting_can_be_discarded(outbox: SqliteOutbox) -> None:
    kept, other = shared(), shared("None")
    waiting(outbox, kept)
    waiting(outbox, other)
    log = FakeActionLog()
    outcome = actions(outbox, log=log).discard(TOPIC, kept.fix_id)
    assert outcome.ok and outcome.message.startswith("Discarded: KeyError: 'db'.")
    assert [e.solution.fix_id for e in outbox.pending()] == [other.fix_id]
    assert [(a.kind, a.detail) for a in log.actions] == [(ActionKind.DISCARD, "KeyError: 'db'")]


def test_discarding_what_no_longer_waits_changes_nothing(outbox: SqliteOutbox) -> None:
    waiting(outbox, shared(), topic="other-team/solutions/v1")
    log = FakeActionLog()
    outcome = actions(outbox, log=log).discard(TOPIC, shared().fix_id)
    assert (outcome.ok, outcome.message) == (False, "That fix is no longer waiting.")
    assert len(outbox.pending()) == 1 and log.actions == []


def test_a_suggestion_can_be_forgotten(outbox: SqliteOutbox) -> None:
    suggestion = ReceivedSolution(solution=shared(node="laptop-eva"), received_at=NOW)
    forgotten, log = FakeForgotten(), FakeActionLog()
    panel = actions(outbox, team=[suggestion], forgotten=forgotten, log=log)
    outcome = panel.forget(suggestion.solution.fix_id)
    assert outcome.ok and "laptop-eva" in outcome.message
    assert forgotten.fixes == {suggestion.solution.fix_id}
    assert [(a.kind, a.detail) for a in log.actions] == [
        (ActionKind.FORGET, "KeyError: 'db', from laptop-eva")
    ]
    gone = panel.forget("f" * 64)
    assert (gone.ok, gone.message) == (False, "That suggestion is no longer kept.")


def test_a_store_that_fails_is_told_not_raised(outbox: SqliteOutbox) -> None:
    class Broken(FakeForgotten):
        def forget(self, fix: str, signature: str, at: datetime) -> None:
            raise ResultsError("Cannot use the results at /data/results.sqlite3: locked")

    suggestion = ReceivedSolution(solution=shared(), received_at=NOW - timedelta(hours=1))
    outcome = actions(outbox, team=[suggestion], forgotten=Broken()).forget(
        suggestion.solution.fix_id
    )
    assert not outcome.ok and outcome.message.endswith("locked")


def test_a_sharer_that_cannot_be_built_is_told(outbox: SqliteOutbox) -> None:
    def no_key() -> SolutionSharer:
        raise SharingError("the team key must have at least 16 characters")

    panel = PanelActions(
        outbox=outbox,
        team=list,
        forgotten=FakeForgotten(),
        log=FakeActionLog(),
        sharer=no_key,
    )
    outcome = panel.send_outbox()
    assert not outcome.ok
    assert outcome.message == "Nothing was sent: the team key must have at least 16 characters"


def test_results_waiting_are_sent_too(outbox: SqliteOutbox) -> None:
    from healer.domain.sharing import FixResult, PendingResult

    outbox.keep_result(
        PendingResult(
            result=FixResult(
                node="laptop-luis",
                reported_at=NOW,
                signature="a" * 64,
                fix_id="f" * 64,
                worked=True,
            ),
            topic=TOPIC,
            queued_at=NOW,
            attempts=1,
            last_error="down",
        )
    )
    outcome = actions(outbox, publisher=Publisher()).send_outbox()
    assert (outcome.ok, outcome.message) == (True, "Sent 1 result.")


def test_an_outbox_that_cannot_be_read_is_told(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("not a database")
    broken = SqliteOutbox(tmp_path / "file")
    outcome = actions(broken).discard(TOPIC, "f" * 64)
    assert not outcome.ok and outcome.message.startswith("Nothing was discarded: ")
