from collections.abc import Sequence

import pytest

from healer.domain.diagnosis import Diagnosis, StackFrame, TracedError
from healer.domain.errors import (
    CommandNotAllowedError,
    ExecutionError,
    LLMError,
    PatchConflictError,
    RejectedProposalError,
)
from healer.domain.execution import RunReport, RunResult
from healer.domain.patch import AppliedPatch, FileEdit, Patch
from healer.domain.proposal import Proposal, TokenUsage
from healer.domain.repair import Attempt, AttemptOutcome, RepairStatus
from healer.pipeline.repair import RepairPipeline


def diagnosis(error_type: str, message: str, line: int = 2) -> Diagnosis:
    frame = StackFrame(
        file="app/cfg.py",
        line=line,
        function="read_db",
        code='return config["db"]',
        workspace_file="app/cfg.py",
    )
    return Diagnosis(
        error=TracedError(error_type=error_type, message=message, frames=(frame,)),
        origin=frame,
        signature=error_type.lower() * 8,
        signature_basis=error_type,
    )


KEY_ERROR = diagnosis("KeyError", "'db'")
ASSERTION = diagnosis("AssertionError", "assert 2 == 3", line=5)


def report(*diagnoses: Diagnosis, patch: Patch | None = None) -> RunReport:
    return RunReport(
        result=RunResult(
            command=("python", "-m", "pytest"),
            exit_code=1 if diagnoses else 0,
            output="",
            duration_seconds=0.5,
        ),
        files_copied=3,
        diagnoses=diagnoses,
        patch=None
        if patch is None
        else AppliedPatch(files=patch.files, diff="-a\n+b\n", lines_added=1, lines_removed=1),
    )


def patch(new: str) -> Patch:
    return Patch(edits=(FileEdit(path="app/cfg.py", old='config["db"]', new=new),))


PARTIAL = patch('config.get("db")')
FULL = patch('config.get("db", "postgres")')


class FakeRunner:
    """Fails without a patch; ``results`` says what each patch does."""

    def __init__(
        self,
        baseline: RunReport,
        results: dict[Patch, RunReport | Exception] | None = None,
    ) -> None:
        self.baseline = baseline
        self.results = results or {}
        self.runs: list[Patch | None] = []

    def run_tests(self, command: str, patch: Patch | None = None) -> RunReport:
        self.runs.append(patch)
        if patch is None:
            return self.baseline
        outcome = self.results.get(patch, report(ASSERTION, patch=patch))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeProposer:
    """Answers each call with the next patch, or raises the next exception."""

    def __init__(self, answers: list[Patch | Exception], tokens: int = 1_000) -> None:
        self.answers = answers
        self.tokens = tokens
        self.calls: list[tuple[Diagnosis, tuple[Attempt, ...]]] = []

    def propose(self, target: Diagnosis, history: Sequence[Attempt] = ()) -> Proposal:
        self.calls.append((target, tuple(history)))
        answer = self.answers[len(self.calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return proposal(answer, self.tokens)


def proposal(answer: Patch, tokens: int = 1_000) -> Proposal:
    return Proposal(
        patch=answer,
        explanation="fix",
        model="fake",
        usage=TokenUsage(input_tokens=tokens - 100, output_tokens=100),
    )


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def reproduced(self, baseline: RunReport, target: Diagnosis | None) -> None:
        self.events.append(f"reproduced {target.error.error_type if target else None}")

    def attempted(self, attempt: Attempt) -> None:
        self.events.append(f"attempt {attempt.number} {attempt.outcome}")


def build(
    runner: FakeRunner, proposer: FakeProposer, *, attempts: int = 3, tokens: int = 100_000
) -> RepairPipeline:
    return RepairPipeline(runner, proposer, max_attempts=attempts, max_tokens=tokens)  # type: ignore[arg-type]


def test_fixed_on_the_first_attempt() -> None:
    runner = FakeRunner(report(KEY_ERROR), {FULL: report(patch=FULL)})
    proposer = FakeProposer([FULL])
    result = build(runner, proposer).run("python -m pytest")
    assert result.status is RepairStatus.FIXED
    assert result.target == KEY_ERROR
    assert result.fix is not None and result.fix.proposal.patch == FULL
    assert proposer.calls == [(KEY_ERROR, ())]
    assert runner.runs == [None, FULL]


def test_a_failed_attempt_is_shown_to_the_model_on_the_next_one() -> None:
    runner = FakeRunner(report(KEY_ERROR), {FULL: report(patch=FULL)})
    proposer = FakeProposer([PARTIAL, FULL])
    result = build(runner, proposer).run("python -m pytest")
    assert result.status is RepairStatus.FIXED
    assert [a.outcome for a in result.attempts] == [AttemptOutcome.FAILED, AttemptOutcome.PASSED]
    [first] = proposer.calls[1][1]
    assert first.proposal.patch == PARTIAL
    assert "AssertionError: assert 2 == 3" in first.feedback()
    assert result.usage.total == 2_000


def test_stops_after_the_maximum_number_of_attempts() -> None:
    proposer = FakeProposer([PARTIAL, patch("1"), patch("2"), FULL])
    result = build(FakeRunner(report(KEY_ERROR)), proposer, attempts=3).run("python -m pytest")
    assert result.status is RepairStatus.OUT_OF_ATTEMPTS
    assert len(result.attempts) == 3 and len(proposer.calls) == 3
    assert result.fix is None
    assert "3 attempts" in result.detail


def test_stops_before_an_attempt_that_would_exceed_the_token_budget() -> None:
    proposer = FakeProposer([PARTIAL, patch("1"), FULL], tokens=4_000)
    result = build(FakeRunner(report(KEY_ERROR)), proposer, tokens=10_000).run("python -m pytest")
    assert result.status is RepairStatus.OUT_OF_BUDGET
    assert len(result.attempts) == 2
    assert "8000 of 10000 tokens" in result.detail


def test_a_patch_that_does_not_fit_counts_as_an_attempt() -> None:
    runner = FakeRunner(
        report(KEY_ERROR),
        {PARTIAL: PatchConflictError("app/cfg.py: text not found"), FULL: report(patch=FULL)},
    )
    result = build(runner, FakeProposer([PARTIAL, FULL])).run("python -m pytest")
    assert result.status is RepairStatus.FIXED
    first = result.attempts[0]
    assert first.outcome is AttemptOutcome.REJECTED
    assert first.feedback() == "The patch could not be applied: app/cfg.py: text not found"


def test_a_rejected_proposal_still_counts_its_tokens() -> None:
    rejected = RejectedProposalError("redaction marker", proposal(PARTIAL, tokens=3_000))
    runner = FakeRunner(report(KEY_ERROR), {FULL: report(patch=FULL)})
    result = build(runner, FakeProposer([rejected, FULL])).run("python -m pytest")
    assert result.status is RepairStatus.FIXED
    assert result.attempts[0].outcome is AttemptOutcome.REJECTED
    assert result.usage.total == 4_000
    assert runner.runs == [None, FULL]


def test_a_repeated_patch_is_not_tested_again() -> None:
    runner = FakeRunner(report(KEY_ERROR), {FULL: report(patch=FULL)})
    result = build(runner, FakeProposer([PARTIAL, PARTIAL, FULL])).run("python -m pytest")
    assert result.status is RepairStatus.FIXED
    assert result.attempts[1].outcome is AttemptOutcome.REPEATED
    assert "Same patch as attempt 1" in result.attempts[1].feedback()
    assert runner.runs == [None, PARTIAL, FULL]


def test_nothing_to_fix_when_the_tests_already_pass() -> None:
    proposer = FakeProposer([])
    result = build(FakeRunner(report()), proposer).run("python -m pytest")
    assert result.status is RepairStatus.PASSING
    assert result.attempts == () and proposer.calls == []


def test_a_failure_without_a_recognised_error_is_not_sent_to_the_model() -> None:
    failed = report().model_copy(
        update={"result": report().result.model_copy(update={"exit_code": 5})}
    )
    proposer = FakeProposer([])
    result = build(FakeRunner(failed), proposer).run("python -m pytest")
    assert result.status is RepairStatus.UNDIAGNOSED
    assert proposer.calls == []


def test_the_chosen_error_is_the_target() -> None:
    runner = FakeRunner(report(KEY_ERROR, ASSERTION), {FULL: report(patch=FULL)})
    proposer = FakeProposer([FULL])
    build(runner, proposer).run("python -m pytest", error_number=2)
    assert proposer.calls[0][0] == ASSERTION
    result = build(runner, FakeProposer([])).run("python -m pytest", error_number=3)
    assert result.status is RepairStatus.UNDIAGNOSED
    assert result.detail == "the run has only 2 errors"


def test_a_model_error_stops_the_loop_and_keeps_earlier_attempts() -> None:
    proposer = FakeProposer([PARTIAL, LLMError("rate limit")])
    result = build(FakeRunner(report(KEY_ERROR)), proposer).run("python -m pytest")
    assert result.status is RepairStatus.STOPPED
    assert result.detail == "rate limit"
    assert len(result.attempts) == 1


def test_a_sandbox_failure_stops_the_loop_and_keeps_the_attempt() -> None:
    runner = FakeRunner(report(KEY_ERROR), {PARTIAL: ExecutionError("cannot start")})
    result = build(runner, FakeProposer([PARTIAL])).run("python -m pytest")
    assert result.status is RepairStatus.STOPPED
    assert len(result.attempts) == 1 and result.usage.total == 1_000


def test_a_disallowed_command_fails_before_asking_the_model() -> None:
    class Refusing(FakeRunner):
        def run_tests(self, command: str, patch: Patch | None = None) -> RunReport:
            raise CommandNotAllowedError("Command not allowed")

    proposer = FakeProposer([])
    with pytest.raises(CommandNotAllowedError):
        build(Refusing(report()), proposer).run("rm -rf /")
    assert proposer.calls == []


def test_the_listener_hears_every_step_in_order() -> None:
    recorder = Recorder()
    runner = FakeRunner(report(KEY_ERROR), {FULL: report(patch=FULL)})
    build(runner, FakeProposer([PARTIAL, FULL])).run("python -m pytest", listener=recorder)
    assert recorder.events == [
        "reproduced KeyError",
        "attempt 1 failed",
        "attempt 2 passed",
    ]


def test_many_attempts_do_not_hit_the_graph_step_limit() -> None:
    proposer = FakeProposer([patch(str(i)) for i in range(15)])
    result = build(FakeRunner(report(KEY_ERROR)), proposer, attempts=15).run("python -m pytest")
    assert result.status is RepairStatus.OUT_OF_ATTEMPTS
    assert len(result.attempts) == 15


def test_feedback_without_a_diagnosis_shows_the_end_of_the_output() -> None:
    failed = report(KEY_ERROR, patch=PARTIAL)
    quiet = failed.model_copy(
        update={
            "diagnoses": (),
            "result": failed.result.model_copy(update={"output": "x" * 5_000 + "\nFAILED boom"}),
        }
    )
    attempt = Attempt(
        number=1, proposal=proposal(PARTIAL), outcome=AttemptOutcome.FAILED, report=quiet
    )
    feedback = attempt.feedback()
    assert feedback.endswith("FAILED boom")
    assert len(feedback) < 2_200


def test_feedback_lists_at_most_three_failures() -> None:
    many = report(*(diagnosis("ValueError", str(i)) for i in range(5)), patch=PARTIAL)
    attempt = Attempt(
        number=1, proposal=proposal(PARTIAL), outcome=AttemptOutcome.FAILED, report=many
    )
    feedback = attempt.feedback()
    assert feedback.count("- ValueError") == 3
    assert "and 2 more" in feedback
    assert "at app/cfg.py:2 in read_db" in feedback
