"""Use case: accept a message from another node as a suggestion, or refuse it."""

from collections.abc import Callable
from datetime import UTC, datetime

from healer.domain.errors import InvalidPatchError, RejectedMessageError
from healer.domain.patch import PatchPolicy
from healer.domain.ports import TeamSolutionRepository
from healer.domain.sharing import (
    Ingested,
    IngestOutcome,
    ReceivedSolution,
    SharedSolution,
    check_key,
    unseal,
)


class SolutionIngester:
    """Everything received is untrusted input (7.5), and it is only ever stored.

    A message must carry a valid signature, follow the schema, stay within the size limit,
    show that the tests failed before the patch, and hold a patch that follows the same
    rules as any other. Valid messages are kept per fix (ADR 0021): the same edits for the
    same error again change nothing, so ingestion stays idempotent (7.6), while other edits
    for that error are kept beside them. An error keeps at most ``fixes_per_error`` fixes,
    the most recently found: a newer one makes room by dropping the oldest, and an older
    one is ignored, so replaying old messages cannot push out newer fixes. A node ignores
    its own messages. Nothing here applies or tests a patch.
    """

    def __init__(
        self,
        store: TeamSolutionRepository,
        policy: PatchPolicy,
        *,
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
        self._node = node
        self._key = key
        self._max_bytes = max_bytes
        self._fixes_per_error = fixes_per_error
        self._clock = clock

    def ingest(self, data: bytes) -> Ingested:
        """Keep the solution in ``data``; raise ``RejectedMessageError`` if it is not valid."""
        solution = unseal(data, self._key, max_bytes=self._max_bytes)
        self._check(solution)
        if solution.node == self._node:
            return Ingested(outcome=IngestOutcome.OWN, solution=solution)
        known = self._store.for_signature(solution.signature)
        if any(k.solution.patch.edits == solution.patch.edits for k in known):
            return Ingested(outcome=IngestOutcome.DUPLICATE, solution=solution)
        arrived = ReceivedSolution(solution=solution, received_at=self._clock())
        # The newest first; on a tie the one that just arrived goes last, so it never
        # pushes out a fix found at the same moment.
        ranked = sorted([*known, arrived], key=lambda k: k.solution.published_at, reverse=True)
        dropped = ranked[self._fixes_per_error :]
        if any(stale is arrived for stale in dropped):
            return Ingested(outcome=IngestOutcome.OLDER, solution=solution)
        self._store.save(arrived)
        for stale in dropped:
            self._store.remove(stale)
        outcome = IngestOutcome.REPLACED if dropped else IngestOutcome.NEW
        return Ingested(outcome=outcome, solution=solution)

    def _check(self, solution: SharedSolution) -> None:
        if solution.evidence.exit_code_before == 0:
            raise RejectedMessageError("the evidence does not show the tests failing before")
        try:
            self._policy.validate(solution.patch)
        except InvalidPatchError as exc:
            raise RejectedMessageError(f"the patch is not safe: {exc}") from exc
