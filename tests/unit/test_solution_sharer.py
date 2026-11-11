import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_outbox import SqliteOutbox
from healer.domain.errors import SharingError
from healer.domain.patch import FileEdit, Patch
from healer.domain.redaction import Redactor
from healer.domain.sharing import FixResult, unseal
from healer.domain.solution import Solution
from healer.services.solution_sharer import SolutionSharer

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
KEY = b"0123456789abcdef-team-key"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
TOPIC = "healer/solutions/v1"
DOWN = "the broker at mosquitto:1883 could not be reached"


class FakePublisher:
    def __init__(self, fail: bool = False, topic: str = TOPIC) -> None:
        self.messages: list[bytes] = []
        self.fail = fail
        self.topic = topic

    def publish(self, message: bytes) -> None:
        if self.fail:
            raise SharingError(DOWN)
        self.messages.append(message)


def solution(**changes: object) -> Solution:
    values: dict[str, object] = {
        "signature": "abc123",
        "error": "KeyError: 'db'",
        "patch": Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new='c.get("db")'),)),
        "explanation": "Use a default.",
        "model": "claude-sonnet-5-5",
        "tokens_spent": 2_900,
        "command": "python -m pytest",
        "exit_code_before": 1,
        "verified_at": NOW,
    }
    return Solution.model_validate({**values, **changes})


def sharer(
    publisher: FakePublisher,
    *,
    key: bytes = KEY,
    max_bytes: int = 10_000,
    outbox: SqliteOutbox | None = None,
    now: datetime = NOW,
    results: FakePublisher | None = None,
) -> SolutionSharer:
    return SolutionSharer(
        publisher,
        Redactor(),
        node="laptop-ana",
        key=key,
        max_bytes=max_bytes,
        outbox=outbox,
        results_publisher=results,
        clock=lambda: now,
    )


def fix_result(fix: str = "f" * 64, *, worked: bool = True, at: datetime = NOW) -> FixResult:
    return FixResult(
        node="laptop-ana", reported_at=at, signature="abc123", fix_id=fix, worked=worked
    )


@pytest.fixture
def outbox(tmp_path: Path) -> SqliteOutbox:
    return SqliteOutbox(tmp_path / "outbox.sqlite3")


def test_publishes_the_signature_patch_and_evidence() -> None:
    publisher = FakePublisher()
    receipt = sharer(publisher).share(solution())
    [message] = publisher.messages
    body = json.loads(message)["solution"]
    assert body["node"] == "laptop-ana" and body["signature"] == "abc123"
    assert body["patch"]["edits"] == [
        {"path": "app/cfg.py", "old": 'c["db"]', "new": 'c.get("db")'}
    ]
    assert body["evidence"] == {
        "command": "python -m pytest",
        "exit_code_before": 1,
        "passed_after": True,
        "verified_at": "2026-10-29T12:00:00Z",
    }
    assert receipt.size == len(message) and receipt.hidden == 0


def test_secrets_in_the_text_are_masked_before_sending() -> None:
    publisher = FakePublisher()
    receipt = sharer(publisher).share(
        solution(explanation=f"The key {AWS} was fine.", error=f"KeyError: '{AWS}'")
    )
    assert AWS.encode() not in publisher.messages[0]
    assert receipt.hidden == 2


def test_the_patch_description_is_not_sent() -> None:
    publisher = FakePublisher()
    described = solution().patch.model_copy(update={"description": f"Uses {AWS}."})
    sharer(publisher).share(solution(patch=described))
    assert AWS.encode() not in publisher.messages[0]
    assert json.loads(publisher.messages[0])["solution"]["patch"]["description"] == ""


def test_a_patch_with_a_secret_is_not_shared() -> None:
    publisher = FakePublisher()
    leaky = Patch(edits=(FileEdit(path="app/cfg.py", old="KEY = None", new=f'KEY = "{AWS}"'),))
    with pytest.raises(SharingError, match=r"app/cfg.py contains what looks like a secret"):
        sharer(publisher).share(solution(patch=leaky))
    assert publisher.messages == []


def test_a_message_over_the_size_limit_is_not_sent() -> None:
    publisher = FakePublisher()
    with pytest.raises(SharingError, match="the limit is 100"):
        sharer(publisher, max_bytes=100).share(solution())
    assert publisher.messages == []


def test_a_short_team_key_is_refused() -> None:
    with pytest.raises(SharingError, match="at least 16 characters"):
        sharer(FakePublisher(), key=b"short")


def test_without_an_outbox_broker_failures_reach_the_caller() -> None:
    with pytest.raises(SharingError, match="could not be reached"):
        sharer(FakePublisher(fail=True)).share(solution())


def test_a_fix_the_broker_does_not_take_waits_in_the_outbox(outbox: SqliteOutbox) -> None:
    receipt = sharer(FakePublisher(fail=True), outbox=outbox).share(solution())
    assert not receipt.delivered and receipt.failure == DOWN
    [waiting] = outbox.pending()
    assert waiting.topic == TOPIC and waiting.attempts == 1 and waiting.last_error == DOWN
    assert waiting.queued_at == NOW
    assert waiting.solution == receipt.solution


def test_what_waits_is_already_redacted(outbox: SqliteOutbox, tmp_path: Path) -> None:
    sharer(FakePublisher(fail=True), outbox=outbox).share(solution(error=f"KeyError: '{AWS}'"))
    assert AWS not in outbox.pending()[0].solution.error
    assert AWS.encode() not in (tmp_path / "outbox.sqlite3").read_bytes()


def test_resend_sends_what_waits_and_empties_the_outbox(outbox: SqliteOutbox) -> None:
    down = FakePublisher(fail=True)
    sharer(down, outbox=outbox).share(solution(signature="first"))
    later = NOW + timedelta(minutes=5)
    sharer(down, outbox=outbox, now=later).share(solution(signature="second"))

    up = FakePublisher()
    report = sharer(up, outbox=outbox, now=NOW + timedelta(hours=1)).resend()
    assert report.sent == 2 and report.left == 0 and report.error is None
    first, second = (unseal(message, KEY, max_bytes=10_000) for message in up.messages)
    assert (first.signature, first.published_at) == ("first", NOW)
    assert (second.signature, second.published_at) == ("second", later)
    assert outbox.pending() == []


def test_resend_stops_at_the_first_failure_and_counts_the_attempt(
    outbox: SqliteOutbox,
) -> None:
    down = FakePublisher(fail=True)
    sharer(down, outbox=outbox).share(solution(signature="first"))
    sharer(down, outbox=outbox, now=NOW + timedelta(minutes=5)).share(solution(signature="second"))

    report = sharer(down, outbox=outbox).resend()
    assert report.sent == 0 and report.left == 2 and report.error == DOWN
    attempts = {entry.solution.signature: entry.attempts for entry in outbox.pending()}
    assert attempts == {"first": 2, "second": 1}


def test_resend_only_sends_to_the_topic_it_was_meant_for(outbox: SqliteOutbox) -> None:
    sharer(FakePublisher(fail=True), outbox=outbox).share(solution())
    other_team = FakePublisher(topic="other-team/solutions/v1")
    report = sharer(other_team, outbox=outbox).resend()
    assert report.sent == 0 and report.error is None
    assert other_team.messages == []
    assert len(outbox.pending()) == 1


def test_resend_signs_with_the_current_team_key(outbox: SqliteOutbox) -> None:
    sharer(FakePublisher(fail=True), outbox=outbox).share(solution())
    new_key = b"a-new-team-key-0123456789"
    up = FakePublisher()
    sharer(up, key=new_key, outbox=outbox).resend()
    assert unseal(up.messages[0], new_key, max_bytes=10_000).signature == "abc123"


def test_a_delivered_fix_drops_its_copy_waiting_but_not_other_fixes(
    outbox: SqliteOutbox,
) -> None:
    down = FakePublisher(fail=True)
    sharer(down, outbox=outbox).share(solution())
    other = Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new="None"),))
    sharer(down, outbox=outbox).share(solution(patch=other))
    later = sharer(FakePublisher(), outbox=outbox, now=NOW + timedelta(minutes=5))
    later.share(solution(explanation="A better default."))
    assert [entry.solution.patch for entry in outbox.pending()] == [other]


def test_a_result_is_sent_to_the_results_topic_signed() -> None:
    fixes, results = FakePublisher(), FakePublisher(topic=f"{TOPIC}/results")
    receipt = sharer(fixes, results=results).report(fix_result())
    assert receipt.delivered
    assert fixes.messages == []
    assert unseal(results.messages[0], KEY, max_bytes=10_000) == fix_result()


def test_a_result_the_broker_does_not_take_waits_and_is_resent(outbox: SqliteOutbox) -> None:
    down = FakePublisher(fail=True)
    receipt = sharer(down, outbox=outbox, results=down).report(fix_result())
    assert receipt.failure == DOWN
    [waiting] = outbox.pending_results()
    assert (waiting.topic, waiting.result) == (TOPIC, fix_result())

    report = sharer(down, outbox=outbox, results=down).resend()
    assert (report.results_sent, report.results_left, report.error) == (0, 1, DOWN)
    assert outbox.pending_results()[0].attempts == 2

    up = FakePublisher()
    report = sharer(up, outbox=outbox, results=up).resend()
    assert (report.results_sent, report.results_left) == (1, 0)
    assert outbox.pending_results() == []


def test_a_delivered_result_drops_an_older_one_waiting_for_the_same_fix(
    outbox: SqliteOutbox,
) -> None:
    down = FakePublisher(fail=True)
    sharer(down, outbox=outbox, results=down).report(fix_result(worked=False))
    up = FakePublisher()
    later = fix_result(at=NOW + timedelta(minutes=5))
    sharer(up, outbox=outbox, results=up).report(later)
    assert outbox.pending_results() == []


def test_results_cannot_be_sent_without_a_publisher_for_them() -> None:
    with pytest.raises(SharingError, match="no publisher for them"):
        sharer(FakePublisher()).report(fix_result())


def test_an_entry_over_the_size_limit_is_dropped(outbox: SqliteOutbox) -> None:
    sharer(FakePublisher(fail=True), outbox=outbox).share(solution())
    up = FakePublisher()
    report = sharer(up, outbox=outbox, max_bytes=100).resend()
    assert report.dropped == 1 and report.sent == 0 and report.error is None
    assert up.messages == [] and outbox.pending() == []


def test_a_fix_that_cannot_be_kept_either_is_reported(tmp_path: Path) -> None:
    (tmp_path / "not-a-folder").write_text("")
    broken = SqliteOutbox(tmp_path / "not-a-folder" / "outbox.sqlite3")
    with pytest.raises(SharingError, match=f"{DOWN}; it could not be kept to send later"):
        sharer(FakePublisher(fail=True), outbox=broken).share(solution())


def test_resend_without_an_outbox_does_nothing() -> None:
    assert sharer(FakePublisher()).resend().sent == 0


def test_without_an_outbox_a_result_the_broker_refuses_reaches_the_caller() -> None:
    down = FakePublisher(fail=True)
    with pytest.raises(SharingError, match="could not be reached"):
        sharer(down, results=down).report(fix_result())


def test_a_result_that_cannot_be_kept_either_is_reported(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    blocked = SqliteOutbox(tmp_path / "file" / "outbox.sqlite3")
    down = FakePublisher(fail=True)
    with pytest.raises(SharingError, match="could not be kept to send later"):
        sharer(down, outbox=blocked, results=down).report(fix_result())
