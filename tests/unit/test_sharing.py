import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import Evidence, SharedSolution, canonical_json, seal

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
KEY = b"0123456789abcdef-team-key"


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
