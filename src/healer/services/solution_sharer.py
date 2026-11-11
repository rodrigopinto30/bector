"""Use case: publish a fix the developer accepted, so other nodes need not solve it again."""

from collections.abc import Callable
from datetime import UTC, datetime

from healer.domain.errors import OutboxError, SharingError
from healer.domain.patch import Patch
from healer.domain.ports import ShareOutbox, SolutionPublisher
from healer.domain.redaction import Redactor
from healer.domain.sharing import (
    Evidence,
    FixResult,
    PendingResult,
    PendingShare,
    ResendReport,
    ResultReceipt,
    SharedSolution,
    ShareReceipt,
    check_key,
    seal,
    seal_result,
)
from healer.domain.solution import Solution


class SolutionSharer:
    """Sends the signature, the patch's edits and its evidence (4.2, 6.2), never whole files.

    The error, the explanation and the command are redacted first (7.5); the patch's own
    description, a copy of the explanation, is left out. A patch that contains something
    that looks like a secret is not shared at all, because masking it would break the
    patch. Every message is signed with the team key (HMAC-SHA256), so receivers can
    reject forged or altered ones.

    It also sends the results of the team's fixes tested here (ADR 0022), through
    ``results_publisher``, which publishes to the team's results topic.

    With an outbox, a solution or a result the broker does not take is kept there, and
    ``resend`` sends it later (7.6). Only the topic it was meant for ever receives it.
    """

    def __init__(
        self,
        publisher: SolutionPublisher,
        redactor: Redactor,
        *,
        node: str,
        key: bytes,
        max_bytes: int,
        outbox: ShareOutbox | None = None,
        results_publisher: SolutionPublisher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        check_key(key)
        self._publisher = publisher
        self._results_publisher = results_publisher
        self._redactor = redactor
        self._node = node
        self._key = key
        self._max_bytes = max_bytes
        self._outbox = outbox
        self._clock = clock

    def share(self, solution: Solution) -> ShareReceipt:
        """Publish ``solution``; raise ``SharingError`` if it must not or could not be sent.

        If the broker does not take it and there is an outbox, it is kept there instead and
        the receipt says why.
        """
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
        message = self._seal(shared)
        topic = self._publisher.topic
        try:
            self._publisher.publish(message)
        except SharingError as exc:
            if self._outbox is None:
                raise
            failure = str(exc)
            try:
                self._outbox.keep(
                    PendingShare(
                        solution=shared,
                        topic=topic,
                        queued_at=self._clock(),
                        attempts=1,
                        last_error=failure,
                    )
                )
            except OutboxError as kept:
                raise SharingError(
                    f"{failure}; it could not be kept to send later: {kept}"
                ) from exc
            return ShareReceipt(solution=shared, size=len(message), hidden=hidden, failure=failure)
        if self._outbox is not None:
            # The same fix may still be waiting from an earlier try; it was just delivered.
            self._outbox.remove(topic, shared.fix_id, up_to=shared.published_at)
        return ShareReceipt(solution=shared, size=len(message), hidden=hidden)

    def report(self, result: FixResult) -> ResultReceipt:
        """Tell the team ``result``; raise ``SharingError`` if it could not be sent and
        there is no outbox to keep it in. A result names a fix, never its code."""
        publisher = self._results_publisher
        if publisher is None:
            raise SharingError("results cannot be sent: there is no publisher for them")
        topic = self._publisher.topic
        try:
            publisher.publish(seal_result(result, self._key))
        except SharingError as exc:
            if self._outbox is None:
                raise
            failure = str(exc)
            try:
                self._outbox.keep_result(
                    PendingResult(
                        result=result,
                        topic=topic,
                        queued_at=self._clock(),
                        attempts=1,
                        last_error=failure,
                    )
                )
            except OutboxError as kept:
                raise SharingError(
                    f"{failure}; it could not be kept to send later: {kept}"
                ) from exc
            return ResultReceipt(result=result, failure=failure)
        if self._outbox is not None:
            self._outbox.remove_result(topic, result.fix_id, up_to=result.reported_at)
        return ResultReceipt(result=result)

    def resend(self) -> ResendReport:
        """Send what waits in the outbox for this topic: the fixes, then the results, each
        the earliest first.

        The first failure ends the pass, since the rest would fail the same way; the entry
        keeps the error and stays. A fix over the size limit can never be sent, so it is
        dropped. Results wait only while there is a publisher for them.
        """
        if self._outbox is None:
            return ResendReport()
        topic = self._publisher.topic
        waiting = [entry for entry in self._outbox.pending() if entry.topic == topic]
        results_publisher = self._results_publisher
        results = (
            [entry for entry in self._outbox.pending_results() if entry.topic == topic]
            if results_publisher is not None
            else []
        )
        sent = results_sent = dropped = 0

        def stopped(exc: SharingError) -> ResendReport:
            return ResendReport(
                sent=sent,
                results_sent=results_sent,
                dropped=dropped,
                left=len(waiting) - sent - dropped,
                results_left=len(results) - results_sent,
                error=str(exc),
            )

        for entry in waiting:
            solution = entry.solution
            try:
                message = self._seal(solution)
            except SharingError:
                self._outbox.remove(topic, solution.fix_id, up_to=solution.published_at)
                dropped += 1
                continue
            try:
                self._publisher.publish(message)
            except SharingError as exc:
                retried = {"attempts": entry.attempts + 1, "last_error": str(exc)}
                self._outbox.keep(entry.model_copy(update=retried))
                return stopped(exc)
            self._outbox.remove(topic, solution.fix_id, up_to=solution.published_at)
            sent += 1
        for pending in results:
            result = pending.result
            try:
                assert results_publisher is not None
                results_publisher.publish(seal_result(result, self._key))
            except SharingError as exc:
                retried = {"attempts": pending.attempts + 1, "last_error": str(exc)}
                self._outbox.keep_result(pending.model_copy(update=retried))
                return stopped(exc)
            self._outbox.remove_result(topic, result.fix_id, up_to=result.reported_at)
            results_sent += 1
        return ResendReport(sent=sent, results_sent=results_sent, dropped=dropped)

    def _seal(self, shared: SharedSolution) -> bytes:
        message = seal(shared, self._key)
        if len(message) > self._max_bytes:
            raise SharingError(
                f"the message is {len(message)} bytes; the limit is {self._max_bytes}"
            )
        return message
