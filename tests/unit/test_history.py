from datetime import UTC, datetime, timedelta, timezone

from healer.domain.diagnosis import Diagnosis, StackFrame, TracedError
from healer.domain.execution import RunReport, RunResult
from healer.domain.history import EDIT_CHARS, FAILURES_KEPT, TEXT_CHARS, run_id, run_record
from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import Proposal, TokenUsage
from healer.domain.redaction import Redactor
from healer.domain.repair import (
    Attempt,
    AttemptOutcome,
    AttemptSource,
    RepairResult,
    RepairStatus,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
AWS = "AKIA" + "IOSFODNN7EXAMPLE"


def diagnosis(message: str = "'db'", *, origin: StackFrame | None = None) -> Diagnosis:
    return Diagnosis(
        error=TracedError(error_type="KeyError", message=message),
        origin=origin,
        signature="a" * 64,
        signature_basis="KeyError",
    )


def report(*diagnoses: Diagnosis, timed_out: bool = False) -> RunReport:
    return RunReport(
        result=RunResult(
            command=("python", "-m", "pytest"),
            exit_code=None if timed_out else (1 if diagnoses else 0),
            output="",
            duration_seconds=1.5,
            timed_out=timed_out,
        ),
        files_copied=3,
        diagnoses=diagnoses,
    )


def attempt(
    number: int,
    outcome: AttemptOutcome,
    *,
    edits: tuple[FileEdit, ...] = (FileEdit(path="app/cfg.py", old="a", new="b"),),
    explanation: str = "Use a default.",
    run: RunReport | None = None,
    problem: str = "",
) -> Attempt:
    return Attempt(
        number=number,
        proposal=Proposal(
            patch=Patch(edits=edits),
            explanation=explanation,
            model="claude-sonnet-5-5",
            usage=TokenUsage(input_tokens=1_000, output_tokens=200),
        ),
        outcome=outcome,
        source=AttemptSource.MODEL,
        report=run,
        problem=problem,
    )


def result(*attempts: Attempt, target: Diagnosis | None = None) -> RepairResult:
    return RepairResult(
        status=RepairStatus.FIXED,
        detail="",
        baseline=report(diagnosis()),
        target=target if target is not None else diagnosis(),
        attempts=attempts,
        project="/workspace",
    )


def record(repair: RepairResult, **changes: object):  # type: ignore[no-untyped-def]
    return run_record(
        repair,
        project="/workspace",
        recorded_at=NOW,
        command="python -m pytest",
        redactor=Redactor(),
        **changes,  # type: ignore[arg-type]
    )


def test_a_run_and_its_incident_share_an_id_whatever_the_time_zone() -> None:
    later = NOW.astimezone(timezone(timedelta(hours=-3)))
    assert run_id("/workspace", NOW) == run_id("/workspace", later)
    assert run_id("/workspace", NOW) == run_id("/workspace", NOW.replace(tzinfo=None))
    assert run_id("/workspace", NOW) != run_id("/workspace/api", NOW)
    assert len(run_id("/workspace", NOW)) == 16


def test_the_record_follows_the_run_from_the_failure_to_the_fix() -> None:
    origin = StackFrame(
        file="/w/app/cfg.py", line=3, function="read_db", workspace_file="app/cfg.py"
    )
    repair = result(
        attempt(1, AttemptOutcome.FAILED, run=report(diagnosis("'other'"))),
        attempt(2, AttemptOutcome.REJECTED, problem="text not found"),
        attempt(3, AttemptOutcome.PASSED, run=report()),
        target=diagnosis(origin=origin),
    )
    kept = record(repair, applied=True, shared="sent", results_told=0)
    assert kept is not None
    assert (kept.run_id, kept.error, kept.location) == (
        run_id("/workspace", NOW),
        "KeyError: 'db'",
        "app/cfg.py:3 in read_db",
    )
    assert kept.baseline.failures == ("KeyError: 'db'",)
    assert [(a.number, a.outcome, a.input_tokens) for a in kept.attempts] == [
        (1, AttemptOutcome.FAILED, 1_000),
        (2, AttemptOutcome.REJECTED, 1_000),
        (3, AttemptOutcome.PASSED, 1_000),
    ]
    first, rejected, fixed = kept.attempts
    assert first.tests is not None and first.tests.failures == ("KeyError: 'other'",)
    assert rejected.tests is None and rejected.problem == "text not found"
    assert fixed.tests is not None and fixed.tests.passed
    assert (kept.applied, kept.shared) == (True, "sent")


def test_nothing_is_kept_without_an_incident() -> None:
    assert record(result()) is None
    no_target = RepairResult(status=RepairStatus.PASSING, baseline=report())
    assert record(no_target) is None


def test_secrets_never_reach_the_history() -> None:
    leaky = FileEdit(path="app/settings.py", old="KEY = None", new=f'KEY = "{AWS}"')
    repair = result(
        attempt(
            1,
            AttemptOutcome.FAILED,
            edits=(leaky,),
            explanation=f"Set {AWS}",
            run=report(diagnosis(f"'{AWS}'")),
        ),
        target=diagnosis(f"'{AWS}'"),
    )
    kept = record(repair)
    assert kept is not None
    assert AWS not in kept.model_dump_json()
    assert "[REDACTED:aws_access_key]" in kept.attempts[0].edits[0].new


def test_long_texts_many_failures_and_big_changes_are_cut() -> None:
    many = tuple(diagnosis(f"'{n}'") for n in range(FAILURES_KEPT + 2))
    big = tuple(FileEdit(path=f"app/{n}.py", old="", new="x" * (EDIT_CHARS // 2)) for n in range(3))
    repair = result(
        attempt(
            1,
            AttemptOutcome.FAILED,
            edits=big,
            explanation="y" * (TEXT_CHARS + 50),
            run=report(*many),
        )
    )
    kept = record(repair)
    assert kept is not None
    tried = kept.attempts[0]
    assert len(tried.explanation) == TEXT_CHARS and tried.explanation.endswith("…")
    assert tried.tests is not None
    assert (len(tried.tests.failures), tried.tests.more_failures) == (FAILURES_KEPT, 2)
    assert (len(tried.edits), tried.edits_cut) == (2, True)


def test_a_run_that_timed_out_is_kept_as_such() -> None:
    kept = record(result(attempt(1, AttemptOutcome.FAILED, run=report(timed_out=True))))
    assert kept is not None
    tests = kept.attempts[0].tests
    assert tests is not None and (tests.timed_out, tests.exit_code, tests.passed) == (
        True,
        None,
        False,
    )
