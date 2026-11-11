"""The order in which a node tries the team's fixes for one error (plan 6.4, ADR 0022)."""

from collections.abc import Iterable, Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.repair import Attempt, AttemptOutcome, AttemptSource
from healer.domain.sharing import FixResult, ReceivedSolution


class Tally(BaseModel):
    """How a fix did across the team: one vote per node, its latest result."""

    model_config = ConfigDict(frozen=True)

    worked: int = Field(default=0, ge=0, description="Nodes where it made the tests pass.")
    failed: int = Field(default=0, ge=0, description="Nodes where it did not.")

    @property
    def score(self) -> float:
        """The share of nodes where it worked, counting one vote for and one against
        from the start, so a single result never decides alone. A fix nobody tried
        scores 0.5: above one that failed once, below one that worked once."""
        return (self.worked + 1) / (self.worked + self.failed + 2)


NO_RESULTS = Tally()


def rank_key(
    received: ReceivedSolution, tallies: Mapping[str, Tally]
) -> tuple[float, int, datetime]:
    """Higher first: the score, then how many nodes it worked on, then the newest."""
    tally = tallies.get(received.solution.fix_id, NO_RESULTS)
    return (tally.score, tally.worked, received.solution.published_at)


def ranked(
    fixes: Iterable[ReceivedSolution], tallies: Mapping[str, Tally]
) -> list[ReceivedSolution]:
    """``fixes`` in the order they should be tried. Equal fixes keep their given order."""
    return sorted(fixes, key=lambda received: rank_key(received, tallies), reverse=True)


def results_of(attempts: Iterable[Attempt], *, node: str, at: datetime) -> list[FixResult]:
    """What a run learned about the team's fixes it tested: a fix whose tests ran either
    worked or failed here. One that did not apply, or was not tested, says nothing."""
    tested = (AttemptOutcome.PASSED, AttemptOutcome.FAILED)
    return [
        FixResult(
            node=node,
            reported_at=at,
            signature=attempt.fix_signature,
            fix_id=attempt.fix,
            worked=attempt.outcome is AttemptOutcome.PASSED,
        )
        for attempt in attempts
        if attempt.source is AttemptSource.TEAM and attempt.fix and attempt.outcome in tested
    ]
