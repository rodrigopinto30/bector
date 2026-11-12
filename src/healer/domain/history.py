"""What a run of ``healer run`` did, step by step, kept on this node so it can be looked at
again (ADR 0023). Everything in it is redacted before it is stored."""

import hashlib
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.diagnosis import Diagnosis
from healer.domain.execution import RunReport
from healer.domain.patch import FileEdit
from healer.domain.redaction import Redactor
from healer.domain.repair import Attempt, AttemptOutcome, AttemptSource, RepairResult, RepairStatus

TEXT_CHARS = 2_000
"""The most kept of an explanation, a problem or a detail."""

FAILURES_KEPT = 5
FAILURE_CHARS = 300

EDIT_CHARS = 20_000
"""The most kept of the edits of one attempt. A patch may be up to MAX_PATCH_BYTES, and a
node keeps many runs, so a longer change is cut and says so."""

Shared = Literal["sent", "waiting", "failed", "off"]


class _Clean(Protocol):
    """Redacts ``text`` and cuts it to ``limit`` characters."""

    def __call__(self, text: str, limit: int = TEXT_CHARS) -> str: ...


def run_id(project: str, at: datetime) -> str:
    """The id of the run recorded in the ledger for ``project`` at ``at``, so a run and its
    incident find each other."""
    utc = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    stamp = utc.isoformat(timespec="microseconds")
    return hashlib.sha256(f"{project}\n{stamp}".encode()).hexdigest()[:16]


class RunOfTests(BaseModel):
    """One run of the tests: how it ended and the errors it showed."""

    model_config = ConfigDict(frozen=True)

    exit_code: int | None = Field(description="None when the run timed out.")
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False
    passed: bool
    failures: tuple[str, ...] = Field(default=(), description="The first errors, redacted.")
    more_failures: int = Field(default=0, ge=0, description="Errors not kept.")


class AttemptRecord(BaseModel):
    """One patch tried during the run, and what the tests said."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    source: AttemptSource
    origin: str = Field(default="", description="The node a team fix came from.")
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    saved_tokens: int = Field(ge=0)
    outcome: AttemptOutcome
    explanation: str
    problem: str = ""
    tests: RunOfTests | None = Field(default=None, description="None when the patch did not run.")
    edits: tuple[FileEdit, ...] = ()
    edits_cut: bool = Field(default=False, description="The edits were too long to keep whole.")


class RunRecord(BaseModel):
    """A run from the first failing test to what happened to its fix."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    project: str
    recorded_at: datetime
    command: str
    status: RepairStatus
    detail: str = ""
    error: str
    error_type: str
    location: str = Field(default="", description="Where the error comes from in the project.")
    signature: str
    baseline: RunOfTests
    attempts: tuple[AttemptRecord, ...]
    applied: bool | None = Field(
        default=None, description="Whether the fix was written to the files; None without one."
    )
    shared: Shared | None = Field(
        default=None, description="What happened when it was sent; None if it was not applied."
    )
    results_told: int = Field(default=0, ge=0, description="Results of team fixes recorded.")


def run_record(
    result: RepairResult,
    *,
    project: str,
    recorded_at: datetime,
    command: str,
    redactor: Redactor,
    applied: bool | None = None,
    shared: Shared | None = None,
    results_told: int = 0,
) -> RunRecord | None:
    """The record of ``result``, or None when the run had no incident to keep."""
    target = result.target
    if target is None or not result.attempts:
        return None

    def clean(text: str, limit: int = TEXT_CHARS) -> str:
        return _cut(redactor.redact(text).text, limit)

    return RunRecord(
        run_id=run_id(project, recorded_at),
        project=project,
        recorded_at=recorded_at,
        command=clean(command),
        status=result.status,
        detail=clean(result.detail),
        error=clean(target.error.summary),
        error_type=target.error.error_type,
        location=_location(target),
        signature=target.signature,
        baseline=_tests(result.baseline, clean),
        attempts=tuple(_attempt(attempt, clean, redactor) for attempt in result.attempts),
        applied=applied,
        shared=shared,
        results_told=results_told,
    )


def _attempt(attempt: Attempt, clean: _Clean, redactor: Redactor) -> AttemptRecord:
    proposal = attempt.proposal
    edits, cut = _edits(proposal.patch.edits, redactor)
    return AttemptRecord(
        number=attempt.number,
        source=attempt.source,
        origin=attempt.origin,
        model=proposal.model,
        input_tokens=proposal.usage.input_tokens,
        output_tokens=proposal.usage.output_tokens,
        saved_tokens=attempt.saved_tokens,
        outcome=attempt.outcome,
        explanation=clean(proposal.explanation),
        problem=clean(attempt.problem),
        tests=_tests(attempt.report, clean) if attempt.report is not None else None,
        edits=edits,
        edits_cut=cut,
    )


def _tests(report: RunReport, clean: _Clean) -> RunOfTests:
    result = report.result
    failures = report.diagnoses
    return RunOfTests(
        exit_code=result.exit_code,
        duration_seconds=result.duration_seconds,
        timed_out=result.timed_out,
        passed=result.passed,
        failures=tuple(clean(d.error.summary, FAILURE_CHARS) for d in failures[:FAILURES_KEPT]),
        more_failures=max(len(failures) - FAILURES_KEPT, 0),
    )


def _edits(edits: tuple[FileEdit, ...], redactor: Redactor) -> tuple[tuple[FileEdit, ...], bool]:
    """The edits redacted, as many whole ones as fit in ``EDIT_CHARS``."""
    kept: list[FileEdit] = []
    room = EDIT_CHARS
    for edit in edits:
        old, new = redactor.redact(edit.old).text, redactor.redact(edit.new).text
        size = len(old) + len(new)
        if size > room:
            return tuple(kept), True
        room -= size
        kept.append(FileEdit(path=edit.path, old=old, new=new))
    return tuple(kept), False


def _location(target: Diagnosis) -> str:
    origin = target.origin
    if origin is None or origin.workspace_file is None:
        return ""
    where = f" in {origin.function}" if origin.function else ""
    return f"{origin.workspace_file}:{origin.line}{where}"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
