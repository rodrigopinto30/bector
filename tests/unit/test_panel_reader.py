import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from healer import __version__
from healer.domain.actions import ActionKind, PanelAction
from healer.domain.errors import (
    HistoryError,
    LedgerError,
    OutboxError,
    SharingError,
    StatusError,
)
from healer.domain.history import AttemptRecord, RunOfTests, RunRecord, run_id
from healer.domain.ledger import LedgerEntry, Resolution
from healer.domain.listener import ListenerStatus
from healer.domain.panel import Check, CheckState
from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import TokenUsage
from healer.domain.repair import AttemptOutcome, AttemptSource, RepairStatus
from healer.domain.sharing import (
    Evidence,
    FixResult,
    PendingResult,
    PendingShare,
    ReceivedSolution,
    SharedSolution,
)
from healer.domain.solution import Solution
from healer.services.panel import PanelReader, Probe
from tests.conftest import FakeActionLog, FakeForgotten, FakeResults

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
TOPIC = "healer/solutions/v1"
PATCH = Patch(edits=(FileEdit(path="app.py", old="x", new="y"),))


def shared(signature: str = "b" * 64) -> SharedSolution:
    return SharedSolution(
        node="laptop-ana",
        published_at=NOW,
        signature=signature,
        error="KeyError: 'db'",
        patch=PATCH,
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=100,
        evidence=Evidence(command="pytest", exit_code_before=1, verified_at=NOW),
    )


def received_fix(node: str, new: str) -> ReceivedSolution:
    solution = shared().model_copy(
        update={"node": node, "patch": Patch(edits=(FileEdit(path="app.py", old="x", new=new),))}
    )
    return ReceivedSolution(solution=solution, received_at=NOW)


class FakeLedger:
    def __init__(self, entries: list[LedgerEntry] | None = None, error: Exception | None = None):
        self._entries = entries or []
        self._error = error

    def record(self, entry: LedgerEntry) -> None:
        raise AssertionError("the panel only reads")

    def entries(self, since: datetime | None = None) -> list[LedgerEntry]:
        if self._error is not None:
            raise self._error
        return self._entries


class FakeOutbox:
    def __init__(
        self,
        pending: list[PendingShare] | None = None,
        error: Exception | None = None,
        results: list[PendingResult] | None = None,
    ):
        self._pending = pending or []
        self._error = error
        self._results = results or []

    def pending_results(self) -> list[PendingResult]:
        if self._error is not None:
            raise self._error
        return self._results

    def keep(self, pending: PendingShare) -> None:
        raise AssertionError("the panel only reads")

    def pending(self) -> list[PendingShare]:
        if self._error is not None:
            raise self._error
        return self._pending

    def remove(self, topic: str, signature: str, *, up_to: datetime) -> None:
        raise AssertionError("the panel only reads")

    def clear(self) -> int:
        raise AssertionError("the panel only reads")


class FakeProjects:
    def __init__(
        self, team_error: Exception | None = None, team: list[ReceivedSolution] | None = None
    ) -> None:
        self.team_error = team_error
        self.received = team
        self.asked: list[str] = []

    def symbols(self, project: str) -> int | None:
        self.asked.append(project)
        return 42 if project == "/workspace/shop" else None

    def fixes(self, project: str) -> list[Solution]:
        if project != "/workspace/shop":
            return []
        return [
            Solution(
                signature="a" * 64,
                error="KeyError: 'db'",
                patch=PATCH,
                explanation="Use a default.",
                model="claude-sonnet-5-5",
                tokens_spent=100,
                command="pytest",
                exit_code_before=1,
                verified_at=NOW,
            )
        ]

    def team(self) -> list[ReceivedSolution]:
        if self.team_error is not None:
            raise self.team_error
        if self.received is not None:
            return self.received
        return [ReceivedSolution(solution=shared(), received_at=NOW)]


class FakeStatus:
    def __init__(self, status: ListenerStatus | None = None, error: Exception | None = None):
        self._status = status
        self._error = error

    def save(self, status: ListenerStatus) -> None:
        raise AssertionError("the panel only reads")

    def load(self) -> ListenerStatus | None:
        if self._error is not None:
            raise self._error
        return self._status


class Service:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def check(self) -> None:
        if self.error is not None:
            raise self.error


def an_entry(project: str = "/workspace/shop") -> LedgerEntry:
    return LedgerEntry(
        recorded_at=NOW - timedelta(hours=1),
        project=project,
        signature="a" * 64,
        error_type="KeyError",
        status=RepairStatus.FIXED,
        resolution=Resolution.MODEL,
        model_attempts=1,
        usage=TokenUsage(input_tokens=90, output_tokens=10),
        tokens_saved=0,
    )


class FakeHistory:
    """Runs kept by id; ``error`` makes every read fail."""

    def __init__(self, *runs: RunRecord, error: Exception | None = None) -> None:
        self.runs = {run.run_id: run for run in runs}
        self.error = error

    def keep(self, record: RunRecord) -> None:
        raise AssertionError("the panel only reads")

    def get(self, identifier: str) -> RunRecord | None:
        if self.error is not None:
            raise self.error
        return self.runs.get(identifier)

    def known(self, identifiers: Iterable[str]) -> set[str]:
        if self.error is not None:
            raise self.error
        return set(identifiers) & set(self.runs)


def kept_run(entry: LedgerEntry) -> RunRecord:
    return RunRecord(
        run_id=run_id(entry.project, entry.recorded_at),
        project=entry.project,
        recorded_at=entry.recorded_at,
        command="python -m pytest",
        status=entry.status,
        error="KeyError: 'db'",
        error_type=entry.error_type,
        signature=entry.signature,
        baseline=RunOfTests(exit_code=1, duration_seconds=1.0, passed=False),
        attempts=(
            AttemptRecord(
                number=1,
                source=AttemptSource.MODEL,
                model="claude-sonnet-5-5",
                input_tokens=90,
                output_tokens=10,
                saved_tokens=0,
                outcome=AttemptOutcome.PASSED,
                explanation="Use a default.",
                tests=RunOfTests(exit_code=0, duration_seconds=1.0, passed=True),
                edits=(FileEdit(path="app.py", old="x = 1\ny = 2\n", new="x = 1\ny = 3\n"),),
            ),
        ),
        applied=True,
        shared="off",
    )


def reader(**overrides: object) -> PanelReader:
    parts: dict[str, object] = {
        "node": "laptop-luis",
        "topic": TOPIC,
        "sharing": True,
        "ledger": FakeLedger([an_entry(), an_entry("/workspace/api")]),
        "outbox": FakeOutbox(),
        "projects": FakeProjects(),
        "results": FakeResults(),
        "history": FakeHistory(),
        "forgotten": FakeForgotten(),
        "actions": FakeActionLog(),
        "listener": FakeStatus(),
        "clock": lambda: NOW,
    }
    parts.update(overrides)
    return PanelReader(**parts)  # type: ignore[arg-type]


def states(snapshot_checks: list[Check]) -> dict[str, CheckState]:
    return {check.key: check.state for check in snapshot_checks}


def test_a_snapshot_gathers_every_section() -> None:
    snapshot = reader().snapshot()
    assert (snapshot.node, snapshot.topic, snapshot.sharing) == ("laptop-luis", TOPIC, True)
    assert (snapshot.taken_at, snapshot.version) == (NOW, __version__)
    assert snapshot.problems == {}
    assert snapshot.savings.total.incidents == 2
    assert snapshot.savings.days[-1].spent == 200
    assert [i.project for i in snapshot.incidents] == ["/workspace/shop", "/workspace/api"]
    assert {p.path: p.symbols for p in snapshot.projects} == {
        "/workspace/shop": 42,
        "/workspace/api": None,
    }
    assert snapshot.team[0].node == "laptop-ana"
    assert states(snapshot.checks) == {"listener": CheckState.OFF, "outbox": CheckState.OK}


def test_probes_ask_each_service_and_say_what_they_answered() -> None:
    snapshot = reader(
        checks=[
            Probe(key="broker", name="Broker", target=Service(), ok="accepts this node"),
            Probe(key="tls", name="TLS", target=Service(SharingError("handshake failed")), ok="-"),
            Probe(key="sandbox", name="Sandbox", target=None, ok="-", off="off: tests run here"),
            Check(key="api_key", name="Key", state=CheckState.OK, detail="set"),
        ]
    ).snapshot()
    checks = {check.key: check for check in snapshot.checks}
    assert (checks["broker"].state, checks["broker"].detail) == (
        CheckState.OK,
        "accepts this node",
    )
    assert (checks["tls"].state, checks["tls"].detail) == (CheckState.FAIL, "handshake failed")
    assert (checks["sandbox"].state, checks["sandbox"].detail) == (
        CheckState.OFF,
        "off: tests run here",
    )
    assert checks["api_key"].state is CheckState.OK


def test_fixes_waiting_in_the_outbox_need_attention() -> None:
    waiting = PendingShare(
        solution=shared(),
        topic=TOPIC,
        queued_at=NOW - timedelta(hours=3),
        attempts=2,
        last_error="down",
    )
    snapshot = reader(outbox=FakeOutbox([waiting, waiting])).snapshot()
    outbox = next(check for check in snapshot.checks if check.key == "outbox")
    assert (outbox.state, outbox.detail) == (CheckState.WARNING, "2 fixes are waiting to be sent")
    assert outbox.since == NOW - timedelta(hours=3)
    assert len(snapshot.outbox) == 2


def test_results_waiting_in_the_outbox_are_counted_too() -> None:
    result = PendingResult(
        result=FixResult(
            node="laptop-luis",
            reported_at=NOW,
            signature="a" * 64,
            fix_id="f" * 64,
            worked=True,
        ),
        topic=TOPIC,
        queued_at=NOW - timedelta(hours=1),
        attempts=1,
        last_error="down",
    )
    waiting = PendingShare(
        solution=shared(), topic=TOPIC, queued_at=NOW, attempts=1, last_error="down"
    )
    snapshot = reader(outbox=FakeOutbox([waiting], results=[result, result])).snapshot()
    outbox = next(check for check in snapshot.checks if check.key == "outbox")
    assert outbox.detail == "1 fix and 2 results are waiting to be sent"
    assert outbox.since == NOW - timedelta(hours=1)
    only = reader(outbox=FakeOutbox(results=[result])).snapshot()
    check = next(check for check in only.checks if check.key == "outbox")
    assert check.detail == "1 result is waiting to be sent"


def test_team_fixes_show_their_results_and_the_order_they_are_tried_in() -> None:
    first = received_fix("laptop-ana", 'c.get("db", "x")')
    second = received_fix("laptop-eva", 'c.get("db")')
    results = FakeResults(
        FixResult(
            node="laptop-sol",
            reported_at=NOW,
            signature=first.solution.signature,
            fix_id=first.solution.fix_id,
            worked=True,
        )
    )
    snapshot = reader(projects=FakeProjects(team=[second, first]), results=results).snapshot()
    assert [(t.node, t.rank, t.worked, t.failed, t.fixes_for_error) for t in snapshot.team] == [
        ("laptop-eva", 2, 0, 0, 2),
        ("laptop-ana", 1, 1, 0, 2),
    ]


def test_a_section_that_cannot_be_read_is_empty_and_says_why() -> None:
    snapshot = reader(
        ledger=FakeLedger(error=LedgerError("The ledger has a corrupt record")),
        outbox=FakeOutbox(error=OutboxError("Cannot open the outbox")),
        listener=FakeStatus(error=StatusError("The listener status is corrupt")),
    ).snapshot()
    assert snapshot.problems == {
        "savings": "The ledger has a corrupt record",
        "outbox": "Cannot open the outbox",
        "listener": "The listener status is corrupt",
    }
    assert snapshot.savings.total.incidents == 0
    assert snapshot.incidents == []
    assert snapshot.projects == []
    assert snapshot.team[0].node == "laptop-ana"
    assert states(snapshot.checks) == {"listener": CheckState.FAIL, "outbox": CheckState.FAIL}


def test_incidents_whose_run_is_kept_can_open_it_step_by_step() -> None:
    shop, api = an_entry(), an_entry("/workspace/api")
    panel = reader(ledger=FakeLedger([shop, api]), history=FakeHistory(kept_run(shop)))
    snapshot = panel.snapshot()
    by_project = {i.project: i.run_id for i in snapshot.incidents}
    assert by_project == {
        "/workspace/shop": run_id(shop.project, shop.recorded_at),
        "/workspace/api": None,
    }

    run = panel.run(run_id(shop.project, shop.recorded_at))
    assert run is not None
    assert [(a.number, a.outcome) for a in run.attempts] == [(1, AttemptOutcome.PASSED)]
    assert [line.kind for line in run.attempts[0].edits[0].lines] == ["context", "removed", "added"]
    assert panel.run("f" * 16) is None


def test_a_history_that_cannot_be_read_hides_only_the_runs() -> None:
    panel = reader(history=FakeHistory(error=HistoryError("Cannot use the history")))
    snapshot = panel.snapshot()
    assert snapshot.problems == {"history": "Cannot use the history"}
    assert len(snapshot.incidents) == 2
    assert all(incident.run_id is None for incident in snapshot.incidents)
    with pytest.raises(HistoryError):
        panel.run("f" * 16)


def test_forgotten_suggestions_are_left_out_and_actions_show_in_the_activity() -> None:
    first = received_fix("laptop-ana", 'c.get("db", "x")')
    second = received_fix("laptop-eva", 'c.get("db")')
    log = FakeActionLog()
    log.record(
        PanelAction(
            at=NOW - timedelta(minutes=1),
            kind=ActionKind.FORGET,
            title="Suggestion forgotten",
            detail="KeyError: 'db', from laptop-eva",
        )
    )
    snapshot = reader(
        projects=FakeProjects(team=[second, first]),
        forgotten=FakeForgotten(second.solution.fix_id),
        actions=log,
    ).snapshot()
    assert [(t.node, t.fixes_for_error) for t in snapshot.team] == [("laptop-ana", 1)]
    assert ("action", "Suggestion forgotten") in [(a.kind, a.title) for a in snapshot.activity]


def test_an_unexpected_error_is_shown_and_logged_not_hidden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    panel = reader(projects=FakeProjects(team_error=RuntimeError("database is locked")))
    with caplog.at_level(logging.ERROR):
        result = panel.snapshot()
    assert result.problems["team"] == "RuntimeError: database is locked"
    assert result.team == []
    assert "The panel could not read team" in caplog.text


def test_a_running_listener_is_reported_with_its_counts() -> None:
    status = ListenerStatus(
        node="laptop-luis",
        topic=TOPIC,
        connection="connected to mosquitto:1883",
        connected=True,
        started_at=NOW - timedelta(hours=1),
        changed_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(seconds=5),
        received=3,
    )
    snapshot = reader(listener=FakeStatus(status)).snapshot()
    assert snapshot.listener == status
    assert states(snapshot.checks)["listener"] is CheckState.OK
