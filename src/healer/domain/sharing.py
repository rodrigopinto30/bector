"""The message a node publishes when its developer accepts a verified fix (4.2, 6.2)."""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.patch import Patch

SCHEMA_VERSION: Literal[1] = 1


class Evidence(BaseModel):
    """The proof a shared solution must carry (6.2): the tests failed without the patch
    and passed with it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str
    exit_code_before: int | None = Field(description="None when the run timed out.")
    passed_after: Literal[True] = True
    verified_at: datetime


class SharedSolution(BaseModel):
    """What leaves the machine: the error, the patch and the evidence. Never whole files."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    node: str = Field(min_length=1, max_length=128, description="The node that found it.")
    published_at: datetime
    signature: str = Field(min_length=1, description="Signature of the error it fixes.")
    error: str = Field(description="Redacted summary of the error.")
    patch: Patch
    explanation: str
    model: str
    tokens_spent: int = Field(ge=0)
    evidence: Evidence


class ShareReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    solution: SharedSolution
    size: int = Field(description="Bytes sent.")
    hidden: int = Field(description="Secrets masked in the error, explanation and command.")


def seal(solution: SharedSolution, key: bytes) -> bytes:
    """The message as sent: the solution plus an HMAC-SHA256 of its canonical JSON."""
    body = solution.model_dump(mode="json")
    mac = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    return canonical_json({"mac": mac, "solution": body})


def canonical_json(value: Any) -> bytes:
    """One byte form per value (sorted keys, no spaces), so a signature can be checked."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
