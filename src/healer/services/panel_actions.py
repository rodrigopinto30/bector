"""Use case: what the panel may change, on this node's own data only (ADR 0025)."""

from collections.abc import Callable
from datetime import UTC, datetime

from healer.domain.actions import ActionKind, ActionOutcome, PanelAction
from healer.domain.errors import HealerError
from healer.domain.ports import ActionLog, ForgottenFixes, ShareOutbox
from healer.domain.sharing import ReceivedSolution
from healer.services.solution_sharer import SolutionSharer


class PanelActions:
    """Send what waits in the outbox, discard one fix waiting, or forget a suggestion from
    the team. Nothing here can reach the workspace, and nothing writes the Chroma database:
    a forgotten suggestion is only listed, and ``healer listen``, which writes the team's
    collection, drops it from there.

    Each action that changes something is recorded for the panel's recent activity. A
    failure is told back as an ``ActionOutcome``, never raised, except one of the action log.
    """

    def __init__(
        self,
        *,
        outbox: ShareOutbox,
        team: Callable[[], list[ReceivedSolution]],
        forgotten: ForgottenFixes,
        log: ActionLog,
        sharer: Callable[[], SolutionSharer] | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._outbox = outbox
        self._team = team
        self._forgotten = forgotten
        self._log = log
        self._sharer = sharer
        self._clock = clock

    def send_outbox(self) -> ActionOutcome:
        """Send now what waits for this topic, as ``healer outbox --send`` does."""
        if self._sharer is None:
            return ActionOutcome(
                ok=False,
                message="Sharing is off: set SHARE_SOLUTIONS=true and SHARE_KEY in .env.",
            )
        try:
            report = self._sharer().resend()
        except HealerError as exc:
            return ActionOutcome(ok=False, message=f"Nothing was sent: {exc}")
        sent = _count(report.sent, report.results_sent) or "nothing"
        dropped = (
            f"; {_count(report.dropped, 0)} over the size limit dropped" if report.dropped else ""
        )
        if report.error is not None:
            left = _count(report.left, report.results_left)
            message = f"Sent {sent}{dropped}; {left} still waiting: {report.error}"
        else:
            message = f"Sent {sent}{dropped}."
        if report.sent or report.results_sent or report.dropped:
            self._record(ActionKind.SEND, "Outbox sent", message)
        return ActionOutcome(ok=report.error is None, message=message)

    def discard(self, topic: str, fix: str) -> ActionOutcome:
        """Forget one fix waiting for ``topic``: the team will not receive it."""
        try:
            entry = next(
                (
                    e
                    for e in self._outbox.pending()
                    if e.topic == topic and e.solution.fix_id == fix
                ),
                None,
            )
            if entry is None:
                return ActionOutcome(ok=False, message="That fix is no longer waiting.")
            self._outbox.remove(topic, fix, up_to=entry.solution.published_at)
        except HealerError as exc:
            return ActionOutcome(ok=False, message=f"Nothing was discarded: {exc}")
        error = entry.solution.error
        self._record(ActionKind.DISCARD, "Fix discarded from the outbox", error)
        return ActionOutcome(ok=True, message=f"Discarded: {error}. The team will not get it.")

    def forget(self, fix: str) -> ActionOutcome:
        """Never offer or keep again the suggestion ``fix``."""
        try:
            received = next((r for r in self._team() if r.solution.fix_id == fix), None)
            if received is None:
                return ActionOutcome(ok=False, message="That suggestion is no longer kept.")
            solution = received.solution
            self._forgotten.forget(fix, solution.signature, self._clock())
        except HealerError as exc:
            return ActionOutcome(ok=False, message=f"Nothing was forgotten: {exc}")
        detail = f"{solution.error}, from {solution.node}"
        self._record(ActionKind.FORGET, "Suggestion forgotten", detail)
        return ActionOutcome(
            ok=True, message=f"Forgotten: the fix from {solution.node} will not be offered again."
        )

    def _record(self, kind: ActionKind, title: str, detail: str) -> None:
        self._log.record(PanelAction(at=self._clock(), kind=kind, title=title, detail=detail[:500]))


def _count(fixes: int, results: int) -> str:
    parts = [f"{fixes} {'fix' if fixes == 1 else 'fixes'}"] if fixes else []
    if results:
        parts.append(f"{results} {'result' if results == 1 else 'results'}")
    return " and ".join(parts)
