"""The message nodes exchange when a developer accepts a verified fix (4.2, 6.2)."""

import hashlib
import hmac
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from healer.domain.errors import RejectedMessageError, SharingError
from healer.domain.patch import Patch
from healer.domain.proposal import Proposal, TokenUsage

SCHEMA_VERSION: Literal[1] = 1
MIN_KEY_LENGTH = 16


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

    def as_proposal(self) -> Proposal:
        """The solution as a free proposal, so it can be tried like any other."""
        return Proposal(
            patch=self.patch,
            explanation=self.explanation,
            model=self.model,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )


class ShareReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    solution: SharedSolution
    size: int = Field(description="Bytes sent.")
    hidden: int = Field(description="Secrets masked in the error, explanation and command.")
    failure: str | None = Field(
        default=None,
        description="Why the broker did not take it, when it waits in the outbox instead.",
    )

    @property
    def delivered(self) -> bool:
        return self.failure is None


class PendingShare(BaseModel):
    """A solution the broker did not take, kept to be sent again (7.6).

    It is stored as it would have left the machine, already redacted, and sealed only when
    sent, with the team key of that moment. It keeps its ``published_at``, so receivers
    still prefer whichever fix for the same error was found last.
    """

    model_config = ConfigDict(frozen=True)

    solution: SharedSolution
    topic: str = Field(description="Only this topic's team may receive it.")
    queued_at: datetime
    attempts: int = Field(ge=1, description="Times the broker did not take it.")
    last_error: str


class ResendReport(BaseModel):
    """What one pass over the outbox did."""

    model_config = ConfigDict(frozen=True)

    sent: int = 0
    dropped: int = Field(default=0, description="Over the size limit, so never sendable.")
    left: int = Field(default=0, description="Still waiting for this topic.")
    error: str | None = Field(default=None, description="Why the pass stopped early.")


class ReceivedSolution(BaseModel):
    """A solution from another node, kept as a suggestion. It is never applied on its own."""

    model_config = ConfigDict(frozen=True)

    solution: SharedSolution
    received_at: datetime


class TeamMatch(BaseModel):
    """A suggestion found for an error."""

    model_config = ConfigDict(frozen=True)

    received: ReceivedSolution
    distance: float = Field(description="Cosine distance between the two error summaries.")
    same_signature: bool = Field(
        default=False,
        description="Both errors have the same signature, which ignores concrete values.",
    )


class IngestOutcome(StrEnum):
    NEW = "new"
    REPLACED = "replaced"
    DUPLICATE = "duplicate"
    OLDER = "older"
    OWN = "own"


class Ingested(BaseModel):
    """What happened to a valid message."""

    model_config = ConfigDict(frozen=True)

    outcome: IngestOutcome
    solution: SharedSolution


def check_key(key: bytes) -> None:
    if len(key) < MIN_KEY_LENGTH:
        raise SharingError(f"the team key must have at least {MIN_KEY_LENGTH} characters")


def seal(solution: SharedSolution, key: bytes) -> bytes:
    """The message as sent: the solution plus an HMAC-SHA256 of its canonical JSON."""
    body = solution.model_dump(mode="json")
    mac = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    return canonical_json({"mac": mac, "solution": body})


def unseal(data: bytes, key: bytes, *, max_bytes: int) -> SharedSolution:
    """Read a received message, or raise ``RejectedMessageError``.

    Everything from the network is untrusted (7.5). The size comes first and the signature
    second, so a message without the team key never reaches the schema.
    """
    if len(data) > max_bytes:
        raise RejectedMessageError(f"the message is {len(data)} bytes; the limit is {max_bytes}")
    try:
        message = json.loads(data)
        if not isinstance(message, dict) or set(message) != {"mac", "solution"}:
            raise RejectedMessageError("the message is not a signed solution")
        mac = message["mac"]
        if not isinstance(mac, str):
            raise RejectedMessageError("the message is not a signed solution")
        expected = hmac.new(key, canonical_json(message["solution"]), hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(mac.encode(), expected.encode())
    except (ValueError, RecursionError) as exc:
        raise RejectedMessageError("the message is not valid JSON") from exc
    if not valid:
        raise RejectedMessageError("the signature does not match: forged or altered message")
    try:
        return SharedSolution.model_validate(message["solution"])
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise RejectedMessageError(f"the solution has an invalid format: {problems}") from exc


def canonical_json(value: Any) -> bytes:
    """One byte form per value (sorted keys, no spaces), so a signature can be checked."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
