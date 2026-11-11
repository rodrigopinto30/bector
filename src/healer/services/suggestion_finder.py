"""Use case: find what the team already has for an error (6.1)."""

from healer.domain.diagnosis import Diagnosis
from healer.domain.ports import ResultStore, TeamSolutionRepository
from healer.domain.ranking import NO_RESULTS, Tally, ranked
from healer.domain.sharing import TeamMatch

_CANDIDATES_PER_MATCH = 3


class SuggestionFinder:
    """The fixes for the same error signature first, best ranked first (ADR 0022): the
    share of nodes where each worked, then how many, then the most recently found. Then
    fixes for a nearly identical error, the closest first.

    Matching by meaning alone is too loose: a ``KeyError`` reads close to a
    ``ConnectionError`` that mentions the same word. So a similar error must also be of the
    same type and within ``max_distance``, because a wrong suggestion costs more than none.
    Each match carries how many nodes it worked on and failed on.
    """

    def __init__(
        self,
        store: TeamSolutionRepository,
        *,
        results: ResultStore,
        max_distance: float,
        max_matches: int,
    ) -> None:
        self._store = store
        self._results = results
        self._max_distance = max_distance
        self._max_matches = max_matches

    def find(self, target: Diagnosis) -> list[TeamMatch]:
        """Return at most ``max_matches`` suggestions, the most reliable first."""
        same = self._store.for_signature(target.signature)
        nearby = [
            match
            for match in self._store.similar(
                target.error.summary,
                self._max_matches * _CANDIDATES_PER_MATCH,
                other_than=target.signature,
            )
            if match.received.solution.signature != target.signature
            and match.distance <= self._max_distance
            and match.received.solution.error.split(":", 1)[0].strip() == target.error.error_type
        ]
        tallies = self._results.tally(
            [r.solution.fix_id for r in same] + [m.received.solution.fix_id for m in nearby]
        )
        matches = [
            TeamMatch(received=received, distance=0, same_signature=True)
            for received in ranked(same, tallies)
        ] + nearby
        return [
            match.model_copy(update=_counts(tallies.get(match.received.solution.fix_id)))
            for match in matches[: self._max_matches]
        ]


def _counts(tally: Tally | None) -> dict[str, int]:
    return (tally or NO_RESULTS).model_dump()
