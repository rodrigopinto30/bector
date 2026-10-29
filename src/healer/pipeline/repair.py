"""The self-correction loop as a LangGraph state graph: reproduce, recall, propose, test."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
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
from healer.domain.ledger import ledger_entry
from healer.domain.ports import Ledger, SolutionRepository
from healer.domain.proposal import Proposal
from healer.domain.repair import (
    Attempt,
    AttemptOutcome,
    AttemptSource,
    RepairResult,
    RepairStatus,
)
from healer.domain.solution import Solution
from healer.services.fix_proposer import FixProposer
from healer.services.sandbox_runner import SandboxRunner

logger = logging.getLogger(__name__)


class RepairState(TypedDict, total=False):
    """Shared by every node. Nodes return only the keys they change."""

    command: str
    error_number: int
    use_cache: bool
    baseline: RunReport
    target: Diagnosis
    attempts: tuple[Attempt, ...]
    proposal: Proposal | None
    status: RepairStatus
    detail: str
    stored: bool
    solution: Solution


class RepairListener(Protocol):
    """Hears about each step while the loop runs, for progress output."""

    def reproduced(self, baseline: RunReport, target: Diagnosis | None) -> None: ...

    def attempted(self, attempt: Attempt) -> None: ...


class RepairPipeline:
    """Runs the tests, then looks for a fix until one makes them pass or a limit is hit.

    The cheapest source goes first (6.1): a solution this workspace already verified for
    the same error signature, replayed for free. If there is none, or it no longer works,
    the model is asked for patches. Every patch is tried on a fresh copy of the original
    code, never on top of an earlier attempt, and the model sees what went wrong with the
    previous ones. The loop stops after ``max_attempts`` model attempts, or before one
    that would likely push the tokens spent past ``max_tokens`` (estimated from the last
    attempt). A patch from the model that works is kept for next time, and every
    incident is recorded in the ledger. The real workspace is never written.
    """

    def __init__(
        self,
        runner: SandboxRunner,
        proposer: FixProposer,
        solutions: SolutionRepository,
        ledger: Ledger,
        *,
        project: str,
        max_attempts: int,
        max_tokens: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runner = runner
        self._proposer = proposer
        self._solutions = solutions
        self._ledger = ledger
        self._project = project
        self._max_attempts = max_attempts
        self._max_tokens = max_tokens
        self._clock = clock
        self._listener: RepairListener | None = None
        self._graph = self._build()

    def run(
        self,
        command: str,
        *,
        error_number: int = 1,
        use_cache: bool = True,
        listener: RepairListener | None = None,
    ) -> RepairResult:
        """Fix error number ``error_number`` of the run; it counts from 1.

        With ``use_cache`` off, stored solutions are not tried, but a new one is still kept.
        """
        self._listener = listener
        try:
            state = cast(
                RepairState,
                self._graph.invoke(
                    {
                        "command": command,
                        "error_number": error_number,
                        "use_cache": use_cache,
                        "attempts": (),
                    },
                    {"recursion_limit": 2 * self._max_attempts + 6},
                ),
            )
        finally:
            self._listener = None
        result = RepairResult(
            status=state["status"],
            detail=state.get("detail", ""),
            baseline=state["baseline"],
            target=state.get("target"),
            attempts=state["attempts"],
            stored=state.get("stored", False),
            solution=state.get("solution"),
        )
        entry = ledger_entry(result, project=self._project, at=self._clock())
        if entry is not None:
            try:
                self._ledger.record(entry)
            except HealerError as exc:
                logger.warning("The incident was not recorded in the ledger: %s", exc)
        return result

    def _build(self) -> CompiledStateGraph[RepairState, None, RepairState, RepairState]:
        graph = StateGraph(RepairState)
        graph.add_node("reproduce", self._reproduce)
        graph.add_node("recall", self._recall)
        graph.add_node("propose", self._propose)
        graph.add_node("test", self._test)
        graph.add_node("remember", self._remember)
        graph.add_edge(START, "reproduce")
        graph.add_conditional_edges("reproduce", _next_after_reproduce, ["recall", END])
        graph.add_conditional_edges("recall", _next_after_recall, ["propose", END])
        graph.add_conditional_edges("propose", _next_after_propose, ["test", "propose", END])
        graph.add_conditional_edges("test", _next_after_test, ["propose", "remember", END])
        graph.add_edge("remember", END)
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

    def _recall(self, state: RepairState) -> RepairState:
        if not state["use_cache"]:
            return {}
        signature = state["target"].signature
        try:
            solution = self._solutions.find(signature)
        except HealerError as exc:
            logger.warning("Stored solutions could not be read: %s", exc)
            return {}
        if solution is None:
            return {}
        update = self._try(
            state,
            solution.as_proposal(),
            source=AttemptSource.CACHE,
            saved_tokens=solution.tokens_spent,
        )
        if update.get("status") is RepairStatus.FIXED:
            update["solution"] = solution.model_copy(
                update={
                    "command": state["command"],
                    "exit_code_before": state["baseline"].result.exit_code,
                    "verified_at": self._clock(),
                }
            )
        elif update.get("status") is not RepairStatus.STOPPED:
            try:
                self._solutions.remove(signature)
            except HealerError as exc:
                logger.warning("A stale solution could not be removed: %s", exc)
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
        return self._try(state, proposal)

    def _remember(self, state: RepairState) -> RepairState:
        attempts = state["attempts"]
        fix = attempts[-1]
        target = state["target"]
        solution = Solution(
            signature=target.signature,
            error=target.error.summary,
            patch=fix.proposal.patch,
            explanation=fix.proposal.explanation,
            model=fix.proposal.model,
            tokens_spent=sum(
                a.proposal.usage.total for a in attempts if a.source is AttemptSource.MODEL
            ),
            command=state["command"],
            exit_code_before=state["baseline"].result.exit_code,
            verified_at=self._clock(),
        )
        try:
            self._solutions.save(solution)
        except HealerError as exc:
            logger.warning("The solution could not be stored for reuse: %s", exc)
            return {"stored": False, "solution": solution}
        return {"stored": True, "solution": solution}

    def _try(
        self,
        state: RepairState,
        proposal: Proposal,
        *,
        source: AttemptSource = AttemptSource.MODEL,
        saved_tokens: int = 0,
    ) -> RepairState:
        """Test ``proposal`` on a fresh copy and record the attempt."""
        attempts = state["attempts"]

        def record(
            outcome: AttemptOutcome, report: RunReport | None = None, problem: str = ""
        ) -> RepairState:
            return self._record(
                attempts,
                proposal,
                outcome,
                report=report,
                problem=problem,
                source=source,
                saved_tokens=saved_tokens,
            )

        try:
            report = self._runner.run_tests(state["command"], patch=proposal.patch)
        except (InvalidPatchError, PatchConflictError) as exc:
            return record(AttemptOutcome.REJECTED, problem=str(exc))
        except HealerError as exc:
            update = record(AttemptOutcome.REJECTED, problem=str(exc))
            return {**update, "status": RepairStatus.STOPPED, "detail": str(exc)}
        outcome = AttemptOutcome.PASSED if report.result.passed else AttemptOutcome.FAILED
        return record(outcome, report=report)

    def _record(
        self,
        attempts: tuple[Attempt, ...],
        proposal: Proposal,
        outcome: AttemptOutcome,
        *,
        report: RunReport | None = None,
        problem: str = "",
        source: AttemptSource = AttemptSource.MODEL,
        saved_tokens: int = 0,
    ) -> RepairState:
        attempt = Attempt(
            number=len(attempts) + 1,
            proposal=proposal,
            outcome=outcome,
            source=source,
            saved_tokens=saved_tokens,
            report=report,
            problem=problem,
        )
        if self._listener is not None:
            self._listener.attempted(attempt)
        done = (*attempts, attempt)
        update: RepairState = {"attempts": done, "proposal": None}
        used = sum(a.proposal.usage.total for a in done)
        asked = sum(1 for a in done if a.source is AttemptSource.MODEL)
        if outcome is AttemptOutcome.PASSED:
            update["status"] = RepairStatus.FIXED
        elif asked >= self._max_attempts:
            update["status"] = RepairStatus.OUT_OF_ATTEMPTS
            update["detail"] = f"no patch made the tests pass in {_count(asked, 'attempt')}"
        elif used + proposal.usage.total > self._max_tokens:
            update["status"] = RepairStatus.OUT_OF_BUDGET
            update["detail"] = (
                f"{used} of {self._max_tokens} tokens spent; "
                "another attempt would likely go over the budget"
            )
        return update


def _next_after_reproduce(state: RepairState) -> str:
    return END if "status" in state else "recall"


def _next_after_recall(state: RepairState) -> str:
    return END if "status" in state else "propose"


def _next_after_propose(state: RepairState) -> str:
    if "status" in state:
        return END
    return "test" if state.get("proposal") is not None else "propose"


def _next_after_test(state: RepairState) -> str:
    if state.get("status") is RepairStatus.FIXED:
        return "remember"
    return END if "status" in state else "propose"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
