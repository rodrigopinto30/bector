"""Use case: find what the team already has for an error (6.1)."""

from healer.domain.diagnosis import Diagnosis
from healer.domain.ports import TeamSolutionRepository
from healer.domain.sharing import TeamMatch

_CANDIDATES_PER_MATCH = 3


class SuggestionFinder:
    """A fix for the same error signature first, then fixes for a nearly identical error.

    Matching by meaning alone is too loose: a ``KeyError`` reads close to a
    ``ConnectionError`` that mentions the same word. So a similar error must also be of the
    same type and within ``max_distance``, because a wrong suggestion costs more than none.
    """

    def __init__(
        self, store: TeamSolutionRepository, *, max_distance: float, max_matches: int
    ) -> None:
        self._store = store
        self._max_distance = max_distance
        self._max_matches = max_matches

    def find(self, target: Diagnosis) -> list[TeamMatch]:
        """Return at most ``max_matches`` suggestions, the most reliable first."""
        same = self._store.find(target.signature)
        matches = (
            [] if same is None else [TeamMatch(received=same, distance=0, same_signature=True)]
        )
        nearby = self._store.similar(
            target.error.summary, self._max_matches * _CANDIDATES_PER_MATCH
        )
        for match in nearby:
            solution = match.received.solution
            if (
                solution.signature != target.signature
                and match.distance <= self._max_distance
                and solution.error.split(":", 1)[0].strip() == target.error.error_type
            ):
                matches.append(match)
        return matches[: self._max_matches]
