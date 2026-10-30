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
    rules as any other. Valid messages are kept per error signature (7.6): the same patch
    again changes nothing, an older one cannot replace a newer one, and a node ignores its
    own messages. Nothing here applies or tests a patch.
    """

    def __init__(
        self,
        store: TeamSolutionRepository,
        policy: PatchPolicy,
        *,
        node: str,
        key: bytes,
        max_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        check_key(key)
        self._store = store
        self._policy = policy
        self._node = node
        self._key = key
        self._max_bytes = max_bytes
        self._clock = clock

    def ingest(self, data: bytes) -> Ingested:
        """Keep the solution in ``data``; raise ``RejectedMessageError`` if it is not valid."""
        solution = unseal(data, self._key, max_bytes=self._max_bytes)
        self._check(solution)
        if solution.node == self._node:
            return Ingested(outcome=IngestOutcome.OWN, solution=solution)
        outcome = self._outcome(solution)
        if outcome in (IngestOutcome.NEW, IngestOutcome.REPLACED):
            self._store.save(ReceivedSolution(solution=solution, received_at=self._clock()))
        return Ingested(outcome=outcome, solution=solution)

    def _check(self, solution: SharedSolution) -> None:
        if solution.evidence.exit_code_before == 0:
            raise RejectedMessageError("the evidence does not show the tests failing before")
        try:
            self._policy.validate(solution.patch)
        except InvalidPatchError as exc:
            raise RejectedMessageError(f"the patch is not safe: {exc}") from exc

    def _outcome(self, solution: SharedSolution) -> IngestOutcome:
        known = self._store.find(solution.signature)
        if known is None:
            return IngestOutcome.NEW
        if known.solution.patch.edits == solution.patch.edits:
            return IngestOutcome.DUPLICATE
        if solution.published_at <= known.solution.published_at:
            return IngestOutcome.OLDER
        return IngestOutcome.REPLACED
