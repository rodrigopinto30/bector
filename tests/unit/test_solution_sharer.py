import json
from datetime import UTC, datetime

import pytest

from healer.domain.errors import SharingError
from healer.domain.patch import FileEdit, Patch
from healer.domain.redaction import Redactor
from healer.domain.solution import Solution
from healer.services.solution_sharer import SolutionSharer

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
KEY = b"0123456789abcdef-team-key"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.messages: list[bytes] = []
        self.fail = fail

    def publish(self, message: bytes) -> None:
        if self.fail:
            raise SharingError("the broker at mosquitto:1883 could not be reached")
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
    publisher: FakePublisher, *, key: bytes = KEY, max_bytes: int = 10_000
) -> SolutionSharer:
    return SolutionSharer(
        publisher, Redactor(), node="laptop-ana", key=key, max_bytes=max_bytes, clock=lambda: NOW
    )


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


def test_broker_failures_reach_the_caller() -> None:
    with pytest.raises(SharingError, match="could not be reached"):
        sharer(FakePublisher(fail=True)).share(solution())
