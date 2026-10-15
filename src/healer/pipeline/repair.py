"""The self-correction loop as a LangGraph state graph: reproduce, propose, test, repeat."""

from typing import Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import (
    HealerError,
    InvalidPatchError,
    PatchConflictError,
    RejectedProposalError,
)
from healer.domain.execution import RunReport
from healer.domain.proposal import Proposal
from healer.domain.repair import Attempt, AttemptOutcome, RepairResult, RepairStatus
from healer.services.fix_proposer import FixProposer
from healer.services.sandbox_runner import SandboxRunner


class RepairState(TypedDict, total=False):
    """Shared by every node. Nodes return only the keys they change."""

    command: str
    error_number: int
    baseline: RunReport
    target: Diagnosis
    attempts: tuple[Attempt, ...]
    proposal: Proposal | None
    status: RepairStatus
    detail: str


class RepairListener(Protocol):
    """Hears about each step while the loop runs, for progress output."""

    def reproduced(self, baseline: RunReport, target: Diagnosis | None) -> None: ...

    def attempted(self, attempt: Attempt) -> None: ...


class RepairPipeline:
    """Runs the tests, then asks for patches until one makes them pass or a limit is hit.

    Every patch is tried on a fresh copy of the original code, never on top of an earlier
    attempt, and the model sees what went wrong with the previous ones. The loop stops
    after ``max_attempts``, or before an attempt that would likely push the tokens spent
    past ``max_tokens`` (estimated from the last attempt). The real workspace is never
    written.
    """

    def __init__(
        self,
        runner: SandboxRunner,
        proposer: FixProposer,
        *,
        max_attempts: int,
        max_tokens: int,
    ) -> None:
        self._runner = runner
        self._proposer = proposer
        self._max_attempts = max_attempts
        self._max_tokens = max_tokens
        self._listener: RepairListener | None = None
        self._graph = self._build()

    def run(
        self, command: str, *, error_number: int = 1, listener: RepairListener | None = None
    ) -> RepairResult:
        """Fix error number ``error_number`` of the run; it counts from 1."""
        self._listener = listener
        try:
            state = cast(
                RepairState,
                self._graph.invoke(
                    {"command": command, "error_number": error_number, "attempts": ()},
                    {"recursion_limit": 2 * self._max_attempts + 3},
                ),
            )
        finally:
            self._listener = None
        return RepairResult(
            status=state["status"],
            detail=state.get("detail", ""),
            baseline=state["baseline"],
            target=state.get("target"),
            attempts=state["attempts"],
        )

    def _build(self) -> CompiledStateGraph[RepairState, None, RepairState, RepairState]:
        graph = StateGraph(RepairState)
        graph.add_node("reproduce", self._reproduce)
        graph.add_node("propose", self._propose)
        graph.add_node("test", self._test)
        graph.add_edge(START, "reproduce")
        graph.add_conditional_edges("reproduce", _next_after_reproduce, ["propose", END])
        graph.add_conditional_edges("propose", _next_after_propose, ["test", "propose", END])
        graph.add_conditional_edges("test", _next_after_test, ["propose", END])
        return graph.compile()

    def _reproduce(self, state: RepairState) -> RepairState:
        baseline = self._runner.run_tests(state["command"])
        update: RepairState = {"baseline": baseline}
        diagnoses = baseline.diagnoses
        number = state["error_number"]
        if baseline.result.passed:
            update["status"] = RepairStatus.PASSING
        elif not diagnoses:
            update["status"] = RepairStatus.UNDIAGNOSED
            update["detail"] = "the tests fail, but no error was recognised in their output"
        elif number > len(diagnoses):
            update["status"] = RepairStatus.UNDIAGNOSED
            update["detail"] = f"the run has only {_count(len(diagnoses), 'error')}"
        else:
            update["target"] = diagnoses[number - 1]
        if self._listener is not None:
            self._listener.reproduced(baseline, update.get("target"))
        return update

    def _propose(self, state: RepairState) -> RepairState:
        attempts = state["attempts"]
        try:
            proposal = self._proposer.propose(state["target"], attempts)
        except RejectedProposalError as exc:
            return self._record(attempts, exc.proposal, AttemptOutcome.REJECTED, problem=str(exc))
        except HealerError as exc:
            return {"status": RepairStatus.STOPPED, "detail": str(exc)}
        return {"proposal": proposal}

    def _test(self, state: RepairState) -> RepairState:
        attempts = state["attempts"]
        proposal = state["proposal"]
        assert proposal is not None
        same = next((a for a in attempts if a.proposal.patch.edits == proposal.patch.edits), None)
        if same is not None:
            return self._record(
                attempts,
                proposal,
                AttemptOutcome.REPEATED,
                problem=f"Same patch as attempt {same.number}, which did not work",
            )
        try:
            report = self._runner.run_tests(state["command"], patch=proposal.patch)
        except (InvalidPatchError, PatchConflictError) as exc:
            return self._record(attempts, proposal, AttemptOutcome.REJECTED, problem=str(exc))
        except HealerError as exc:
            update = self._record(attempts, proposal, AttemptOutcome.REJECTED, problem=str(exc))
            return {**update, "status": RepairStatus.STOPPED, "detail": str(exc)}
        outcome = AttemptOutcome.PASSED if report.result.passed else AttemptOutcome.FAILED
        return self._record(attempts, proposal, outcome, report=report)

    def _record(
        self,
        attempts: tuple[Attempt, ...],
        proposal: Proposal,
        outcome: AttemptOutcome,
        *,
        report: RunReport | None = None,
        problem: str = "",
    ) -> RepairState:
        attempt = Attempt(
            number=len(attempts) + 1,
            proposal=proposal,
            outcome=outcome,
            report=report,
            problem=problem,
        )
        if self._listener is not None:
            self._listener.attempted(attempt)
        done = (*attempts, attempt)
        update: RepairState = {"attempts": done, "proposal": None}
        used = sum(a.proposal.usage.total for a in done)
        if outcome is AttemptOutcome.PASSED:
            update["status"] = RepairStatus.FIXED
        elif len(done) >= self._max_attempts:
            update["status"] = RepairStatus.OUT_OF_ATTEMPTS
            update["detail"] = f"no patch made the tests pass in {_count(len(done), 'attempt')}"
        elif used + proposal.usage.total > self._max_tokens:
            update["status"] = RepairStatus.OUT_OF_BUDGET
            update["detail"] = (
                f"{used} of {self._max_tokens} tokens spent; "
                "another attempt would likely go over the budget"
            )
        return update


def _next_after_reproduce(state: RepairState) -> str:
    return END if "status" in state else "propose"


def _next_after_propose(state: RepairState) -> str:
    if "status" in state:
        return END
    return "test" if state.get("proposal") is not None else "propose"


def _next_after_test(state: RepairState) -> str:
    return END if "status" in state else "propose"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
