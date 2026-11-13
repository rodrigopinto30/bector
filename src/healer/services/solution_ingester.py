"""Use case: accept a message from another node, a fix or a result, or refuse it."""

from collections.abc import Callable
from datetime import UTC, datetime

from healer.domain.errors import InvalidPatchError, RejectedMessageError
from healer.domain.patch import PatchPolicy
from healer.domain.ports import ForgottenFixes, ResultStore, TeamSolutionRepository
from healer.domain.ranking import rank_key
from healer.domain.sharing import (
    FixResult,
    Ingested,
    IngestOutcome,
    ReceivedSolution,
    ResultIngested,
    ResultOutcome,
    SharedSolution,
    check_key,
    unseal,
)


class SolutionIngester:
    """Everything received is untrusted input (7.5), and it is only ever stored.

    A message must carry a valid signature, follow the schema and stay within the size
    limit. A fix must also show that the tests failed before the patch, and hold a patch
    that follows the same rules as any other. Fixes are kept per fix (ADR 0021): the same
    edits for the same error again change nothing, so ingestion stays idempotent (7.6),
    while other edits for that error are kept beside them.

    An error keeps at most ``fixes_per_error`` fixes, the best ranked (ADR 0022): the
    share of nodes where each worked, then how many, then the most recently found. A new
    fix has no results yet, so it ranks like any other fix nobody tried, and a full error
    drops whichever ranks last. On a tie the fix that just arrived loses, so replaying old
    messages cannot push out newer fixes.

    A result counts one vote per node and fix, its latest, and only for a fix kept here.
    A fix this node forgot is never kept again, and is dropped from the store the next time
    a fix for its error arrives (ADR 0025). A node ignores its own messages. Nothing here
    applies or tests a patch.
    """

    def __init__(
        self,
        store: TeamSolutionRepository,
        policy: PatchPolicy,
        *,
        results: ResultStore,
        forgotten: ForgottenFixes,
        node: str,
        key: bytes,
        max_bytes: int,
        fixes_per_error: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        check_key(key)
        if fixes_per_error < 1:
            raise ValueError("an error must keep at least one fix")
        self._store = store
        self._policy = policy
        self._results = results
        self._forgotten = forgotten
        self._node = node
        self._key = key
        self._max_bytes = max_bytes
        self._fixes_per_error = fixes_per_error
        self._clock = clock

    def ingest(self, data: bytes) -> Ingested | ResultIngested:
        """Keep the fix or the result in ``data``; raise ``RejectedMessageError`` if it is
        not valid."""
        message = unseal(data, self._key, max_bytes=self._max_bytes)
        if isinstance(message, FixResult):
            return self._count(message)
        return self._keep(message)

    def _keep(self, solution: SharedSolution) -> Ingested:
        self._check(solution)
        if solution.node == self._node:
            return Ingested(outcome=IngestOutcome.OWN, solution=solution)
        if self._forgotten.forgotten([solution.fix_id]):
            return Ingested(outcome=IngestOutcome.FORGOTTEN, solution=solution)
        known = self._without_forgotten(self._store.for_signature(solution.signature))
        if any(k.solution.patch.edits == solution.patch.edits for k in known):
            return Ingested(outcome=IngestOutcome.DUPLICATE, solution=solution)
        arrived = ReceivedSolution(solution=solution, received_at=self._clock())
        # The new fix may have results already: kept before, dropped, and sent again.
        tallies = self._results.tally(k.solution.fix_id for k in [*known, arrived])
        # Sorting is stable, so on a tie the fix that just arrived, listed last, stays last.
        ranked = sorted([*known, arrived], key=lambda fix: rank_key(fix, tallies), reverse=True)
        dropped = ranked[self._fixes_per_error :]
        if any(stale is arrived for stale in dropped):
            return Ingested(outcome=IngestOutcome.OUTRANKED, solution=solution)
        self._store.save(arrived)
        for stale in dropped:
            self._store.remove(stale)
        outcome = IngestOutcome.REPLACED if dropped else IngestOutcome.NEW
        return Ingested(outcome=outcome, solution=solution)

    def _without_forgotten(self, known: list[ReceivedSolution]) -> list[ReceivedSolution]:
        """``known`` without the fixes this node forgot, which are dropped from the store
        here: this process is the one that writes it."""
        forgotten = self._forgotten.forgotten(k.solution.fix_id for k in known)
        for stale in (k for k in known if k.solution.fix_id in forgotten):
            self._store.remove(stale)
        return [k for k in known if k.solution.fix_id not in forgotten]

    def _count(self, result: FixResult) -> ResultIngested:
        if result.node == self._node:
            return ResultIngested(outcome=ResultOutcome.OWN, result=result)
        fix = next(
            (
                kept.solution
                for kept in self._store.for_signature(result.signature)
                if kept.solution.fix_id == result.fix_id
            ),
            None,
        )
        if fix is None:
            return ResultIngested(outcome=ResultOutcome.UNKNOWN, result=result)
        counted = self._results.record(result)
        outcome = ResultOutcome.COUNTED if counted else ResultOutcome.KNOWN
        return ResultIngested(outcome=outcome, result=result, fix=fix)

    def _check(self, solution: SharedSolution) -> None:
        if solution.evidence.exit_code_before == 0:
            raise RejectedMessageError("the evidence does not show the tests failing before")
        try:
            self._policy.validate(solution.patch)
        except InvalidPatchError as exc:
            raise RejectedMessageError(f"the patch is not safe: {exc}") from exc
