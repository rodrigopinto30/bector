import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from healer.domain.errors import RejectedMessageError
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import (
    Evidence,
    FixResult,
    SharedSolution,
    canonical_json,
    results_topic,
    seal,
    seal_result,
    unseal,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
KEY = b"0123456789abcdef-team-key"
LIMIT = 10_000


def shared(**changes: object) -> SharedSolution:
    values: dict[str, object] = {
        "node": "laptop-ana",
        "published_at": NOW,
        "signature": "abc123",
        "error": "KeyError: 'db'",
        "patch": Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new='c.get("db")'),)),
        "explanation": "Use a default.",
        "model": "claude-sonnet-5-5",
        "tokens_spent": 2_900,
        "evidence": Evidence(command="python -m pytest", exit_code_before=1, verified_at=NOW),
    }
    return SharedSolution.model_validate({**values, **changes})


def test_the_message_is_the_solution_with_an_hmac_of_its_canonical_json() -> None:
    message = json.loads(seal(shared(), KEY))
    assert set(message) == {"mac", "solution"}
    expected = hmac.new(KEY, canonical_json(message["solution"]), hashlib.sha256).hexdigest()
    assert message["mac"] == expected
    assert message["solution"]["schema_version"] == 1
    assert message["solution"]["evidence"]["passed_after"] is True


def test_any_change_or_another_key_changes_the_mac() -> None:
    mac = json.loads(seal(shared(), KEY))["mac"]
    assert json.loads(seal(shared(tokens_spent=1), KEY))["mac"] != mac
    assert json.loads(seal(shared(), b"another-key-of-16+"))["mac"] != mac


def test_the_byte_form_is_stable() -> None:
    assert seal(shared(), KEY) == seal(shared(), KEY)
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


def test_unknown_fields_and_missing_evidence_are_rejected() -> None:
    with pytest.raises(ValidationError):
        shared(source_file="the whole file")
    with pytest.raises(ValidationError):
        Evidence(command="pytest", exit_code_before=1, passed_after=False, verified_at=NOW)  # type: ignore[arg-type]


def resigned(body: dict[str, Any], key: bytes = KEY) -> bytes:
    """A message whose signature is right, for testing what comes after the signature."""
    mac = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    return canonical_json({"mac": mac, "solution": body})


def test_unseal_returns_what_seal_sent() -> None:
    assert unseal(seal(shared(), KEY), KEY, max_bytes=LIMIT) == shared()


def test_text_outside_ascii_survives_the_signature() -> None:
    original = shared(error="KeyError: 'contraseña ñ 日本語'")
    assert unseal(seal(original, KEY), KEY, max_bytes=LIMIT) == original


def test_a_message_signed_with_another_key_is_rejected() -> None:
    with pytest.raises(RejectedMessageError, match="forged or altered"):
        unseal(seal(shared(), b"another-key-of-16+"), KEY, max_bytes=LIMIT)


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body["patch"]["edits"][0].update(new="import os; os.system('rm -rf /')"),
        lambda body: body["patch"]["edits"][0].update(path="app/other.py"),
        lambda body: body.update(node="laptop-mallory"),
        lambda body: body["evidence"].update(exit_code_before=2),
        lambda body: body.update(injected="field"),
    ],
)
def test_any_change_after_signing_is_rejected(change: Callable[[dict[str, Any]], None]) -> None:
    message = json.loads(seal(shared(), KEY))
    change(message["solution"])
    with pytest.raises(RejectedMessageError, match="forged or altered"):
        unseal(json.dumps(message).encode(), KEY, max_bytes=LIMIT)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"not json", "not valid JSON"),
        (b"\xff\xfe", "not valid JSON"),
        (b"[" * 5_000, "not valid JSON"),
        (b'{"mac": "\\ud800", "solution": {}}', "not valid JSON"),
        (b'{"mac": "ab", "solution": {"error": "\\ud800"}}', "not valid JSON"),
        (b"[]", "not a signed fix or result"),
        (b'{"solution": {}}', "not a signed fix or result"),
        (b'{"mac": 1, "solution": {}}', "not a signed fix or result"),
        (b'{"mac": "ab", "solution": {}, "extra": 1}', "not a signed fix or result"),
        (b'{"mac": "\xc3\xb1", "solution": {}}', "forged or altered"),
    ],
)
def test_malformed_messages_are_rejected(data: bytes, reason: str) -> None:
    with pytest.raises(RejectedMessageError, match=reason):
        unseal(data, KEY, max_bytes=LIMIT)


def test_a_message_over_the_limit_is_rejected_before_it_is_read() -> None:
    with pytest.raises(RejectedMessageError, match="the limit is 3"):
        unseal(seal(shared(), KEY), KEY, max_bytes=3)


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.update(schema_version=2),
        lambda body: body.update(injected="field"),
        lambda body: body.pop("evidence"),
        lambda body: body["evidence"].update(passed_after=False),
        lambda body: body["patch"].update(edits=[]),
        lambda body: body.update(tokens_spent=-1),
    ],
)
def test_a_signed_message_must_still_follow_the_schema(
    change: Callable[[dict[str, Any]], None],
) -> None:
    body = json.loads(seal(shared(), KEY))["solution"]
    change(body)
    with pytest.raises(RejectedMessageError, match="invalid format"):
        unseal(resigned(body), KEY, max_bytes=LIMIT)


def test_a_fix_is_identified_by_its_error_and_its_edits() -> None:
    fix = shared().fix_id
    assert len(fix) == 64
    same_fix_elsewhere = shared(
        node="laptop-eva",
        published_at=datetime(2026, 11, 1, tzinfo=UTC),
        explanation="Another text.",
        tokens_spent=1,
    )
    assert same_fix_elsewhere.fix_id == fix
    other_edits = Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new="None"),))
    assert shared(patch=other_edits).fix_id != fix
    assert shared(signature="def456").fix_id != fix


def fix_result(**changes: object) -> FixResult:
    values: dict[str, object] = {
        "node": "laptop-eva",
        "reported_at": NOW,
        "signature": "abc123",
        "fix_id": shared().fix_id,
        "worked": True,
    }
    return FixResult.model_validate({**values, **changes})


def test_a_result_is_signed_with_its_kind_and_read_back() -> None:
    message = seal_result(fix_result(), KEY)
    body = json.loads(message)
    assert set(body) == {"mac", "result"}
    signed = canonical_json({"result": body["result"]})
    assert body["mac"] == hmac.new(KEY, signed, hashlib.sha256).hexdigest()
    assert unseal(message, KEY, max_bytes=10_000) == fix_result()


def test_a_signed_result_cannot_pass_for_a_fix_or_carry_more() -> None:
    body = json.loads(seal_result(fix_result(), KEY))
    as_fix = canonical_json({"mac": body["mac"], "solution": body["result"]})
    with pytest.raises(RejectedMessageError, match="forged or altered"):
        unseal(as_fix, KEY, max_bytes=10_000)
    with pytest.raises(RejectedMessageError, match="result has an invalid format"):
        unseal(seal_result_body({**body["result"], "patch": "x"}), KEY, max_bytes=10_000)


def seal_result_body(body: dict[str, Any]) -> bytes:
    mac = hmac.new(KEY, canonical_json({"result": body}), hashlib.sha256).hexdigest()
    return canonical_json({"mac": mac, "result": body})


def test_a_result_names_a_fix_by_its_id() -> None:
    with pytest.raises(ValidationError):
        fix_result(fix_id="not-a-fix-id")


def test_results_travel_beside_the_team_topic() -> None:
    assert results_topic("healer/solutions/v1") == "healer/solutions/v1/results"
