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
RESULTS_SUBTOPIC = "results"


def results_topic(topic: str) -> str:
    """Where the results of a team's fixes travel: beside its fixes, never mixed with them,
    so nodes that do not know results never receive one (ADR 0022)."""
    return f"{topic}/{RESULTS_SUBTOPIC}"


def fix_id(signature: str, patch: Patch) -> str:
    """What identifies a fix: the error it fixes and the edits it makes (ADR 0021).

    The same edits for the same error always give the same id, whoever found them and
    whenever, so a fix is never stored twice. Other edits for that error give another.
    """
    edits = [edit.model_dump(mode="json") for edit in patch.edits]
    return hashlib.sha256(canonical_json({"signature": signature, "edits": edits})).hexdigest()


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

    @property
    def fix_id(self) -> str:
        """See ``fix_id``."""
        return fix_id(self.signature, self.patch)

    def as_proposal(self) -> Proposal:
        """The solution as a free proposal, so it can be tried like any other."""
        return Proposal(
            patch=self.patch,
            explanation=self.explanation,
            model=self.model,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )


class FixResult(BaseModel):
    """What a node learned by testing a fix from the team: whether it made the tests pass
    there (ADR 0022). It names the fix and its error, never code, a project or a path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    node: str = Field(min_length=1, max_length=128, description="The node that tested it.")
    reported_at: datetime
    signature: str = Field(min_length=1, max_length=128, description="The error it fixes.")
    fix_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    worked: bool


class ResultReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: FixResult
    failure: str | None = Field(
        default=None,
        description="Why the broker did not take it, when it waits in the outbox instead.",
    )

    @property
    def delivered(self) -> bool:
        return self.failure is None


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


class PendingResult(BaseModel):
    """A result the broker did not take, kept to be sent again. One per fix: a newer
    result of this node for the same fix replaces it."""

    model_config = ConfigDict(frozen=True)

    result: FixResult
    topic: str = Field(description="The team topic; results go to its results subtopic.")
    queued_at: datetime
    attempts: int = Field(ge=1, description="Times the broker did not take it.")
    last_error: str


class ResendReport(BaseModel):
    """What one pass over the outbox did."""

    model_config = ConfigDict(frozen=True)

    sent: int = 0
    results_sent: int = 0
    dropped: int = Field(default=0, description="Over the size limit, so never sendable.")
    left: int = Field(default=0, description="Fixes still waiting for this topic.")
    results_left: int = Field(default=0, description="Results still waiting for this topic.")
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
    worked: int = Field(default=0, ge=0, description="Nodes where it made the tests pass.")
    failed: int = Field(default=0, ge=0, description="Nodes where it did not.")


class IngestOutcome(StrEnum):
    NEW = "new"
    """Kept, beside any other fix for the same error."""
    REPLACED = "replaced"
    """Kept; the error had as many fixes as allowed, so the one ranked last made room."""
    DUPLICATE = "duplicate"
    """The same edits for the same error were already kept."""
    OUTRANKED = "outranked"
    """Not kept: the error has as many fixes as allowed, all of them ranked higher."""
    OWN = "own"


class Ingested(BaseModel):
    """What happened to a valid message with a fix."""

    model_config = ConfigDict(frozen=True)

    outcome: IngestOutcome
    solution: SharedSolution


class ResultOutcome(StrEnum):
    COUNTED = "counted"
    """A new word from that node on that fix, or a change of mind."""
    KNOWN = "known"
    """That node had already said this, or something newer."""
    UNKNOWN = "unknown"
    """About a fix this node does not keep, so it is not stored."""
    OWN = "own"


class ResultIngested(BaseModel):
    """What happened to a valid message with a result."""

    model_config = ConfigDict(frozen=True)

    outcome: ResultOutcome
    result: FixResult
    fix: SharedSolution | None = Field(default=None, description="The fix, when kept here.")


def check_key(key: bytes) -> None:
    if len(key) < MIN_KEY_LENGTH:
        raise SharingError(f"the team key must have at least {MIN_KEY_LENGTH} characters")


def seal(solution: SharedSolution, key: bytes) -> bytes:
    """The message as sent: the solution plus an HMAC-SHA256 of its canonical JSON."""
    body = solution.model_dump(mode="json")
    mac = hmac.new(key, canonical_json(body), hashlib.sha256).hexdigest()
    return canonical_json({"mac": mac, "solution": body})


def seal_result(result: FixResult, key: bytes) -> bytes:
    """The message as sent: the result plus an HMAC-SHA256 of ``{"result": ...}``.

    The kind is inside what is signed, so a signed result can never be read as anything
    else.
    """
    body = result.model_dump(mode="json")
    mac = hmac.new(key, canonical_json({"result": body}), hashlib.sha256).hexdigest()
    return canonical_json({"mac": mac, "result": body})


def unseal(data: bytes, key: bytes, *, max_bytes: int) -> SharedSolution | FixResult:
    """Read a received message, a fix or a result, or raise ``RejectedMessageError``.

    Everything from the network is untrusted (7.5). The size comes first and the signature
    second, so a message without the team key never reaches the schema.
    """
    if len(data) > max_bytes:
        raise RejectedMessageError(f"the message is {len(data)} bytes; the limit is {max_bytes}")
    try:
        message = json.loads(data)
        if not isinstance(message, dict) or set(message) not in _ENVELOPES:
            raise RejectedMessageError("the message is not a signed fix or result")
        mac = message["mac"]
        if not isinstance(mac, str):
            raise RejectedMessageError("the message is not a signed fix or result")
        kind = "solution" if "solution" in message else "result"
        signed = message["solution"] if kind == "solution" else {"result": message["result"]}
        expected = hmac.new(key, canonical_json(signed), hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(mac.encode(), expected.encode())
    except (ValueError, RecursionError) as exc:
        raise RejectedMessageError("the message is not valid JSON") from exc
    if not valid:
        raise RejectedMessageError("the signature does not match: forged or altered message")
    model: type[SharedSolution] | type[FixResult] = (
        SharedSolution if kind == "solution" else FixResult
    )
    try:
        return model.model_validate(message[kind])
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        raise RejectedMessageError(f"the {kind} has an invalid format: {problems}") from exc


_ENVELOPES = ({"mac", "solution"}, {"mac", "result"})


def canonical_json(value: Any) -> bytes:
    """One byte form per value (sorted keys, no spaces), so a signature can be checked."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
