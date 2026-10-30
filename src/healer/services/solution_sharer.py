"""Use case: publish a fix the developer accepted, so other nodes need not solve it again."""

from collections.abc import Callable
from datetime import UTC, datetime

from healer.domain.errors import SharingError
from healer.domain.patch import Patch
from healer.domain.ports import SolutionPublisher
from healer.domain.redaction import Redactor
from healer.domain.sharing import Evidence, SharedSolution, ShareReceipt, check_key, seal
from healer.domain.solution import Solution


class SolutionSharer:
    """Sends the signature, the patch's edits and its evidence (4.2, 6.2), never whole files.

    The error, the explanation and the command are redacted first (7.5); the patch's own
    description, a copy of the explanation, is left out. A patch that contains something
    that looks like a secret is not shared at all, because masking it would break the
    patch. Every message is signed with the team key (HMAC-SHA256), so receivers can
    reject forged or altered ones.
    """

    def __init__(
        self,
        publisher: SolutionPublisher,
        redactor: Redactor,
        *,
        node: str,
        key: bytes,
        max_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        check_key(key)
        self._publisher = publisher
        self._redactor = redactor
        self._node = node
        self._key = key
        self._max_bytes = max_bytes
        self._clock = clock

    def share(self, solution: Solution) -> ShareReceipt:
        """Publish ``solution``; raise ``SharingError`` if it must not or could not be sent."""
        for edit in solution.patch.edits:
            kinds = (
                self._redactor.redact(edit.old).counts.keys()
                | self._redactor.redact(edit.new).counts.keys()
            )
            if kinds:
                raise SharingError(
                    f"the patch for {edit.path} contains what looks like a secret "
                    f"({', '.join(sorted(kinds))}), so it was not shared"
                )
        hidden = 0

        def redact(text: str) -> str:
            nonlocal hidden
            result = self._redactor.redact(text)
            hidden += result.total
            return result.text

        shared = SharedSolution(
            node=self._node,
            published_at=self._clock(),
            signature=solution.signature,
            error=redact(solution.error),
            patch=Patch(edits=solution.patch.edits),
            explanation=redact(solution.explanation),
            model=solution.model,
            tokens_spent=solution.tokens_spent,
            evidence=Evidence(
                command=redact(solution.command),
                exit_code_before=solution.exit_code_before,
                verified_at=solution.verified_at,
            ),
        )
        message = seal(shared, self._key)
        if len(message) > self._max_bytes:
            raise SharingError(
                f"the message is {len(message)} bytes; the limit is {self._max_bytes}"
            )
        self._publisher.publish(message)
        return ShareReceipt(solution=shared, size=len(message), hidden=hidden)
