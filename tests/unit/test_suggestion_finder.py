from datetime import UTC, datetime

from healer.domain.diagnosis import Diagnosis, TracedError
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import (
    Evidence,
    FixResult,
    ReceivedSolution,
    SharedSolution,
    TeamMatch,
)
from healer.services.suggestion_finder import SuggestionFinder
from tests.conftest import FakeForgotten, FakeResults

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SIGNATURE = "a" * 64
TARGET = Diagnosis(
    error=TracedError(error_type="KeyError", message="'db'"),
    origin=None,
    signature=SIGNATURE,
    signature_basis="KeyError",
)


def received(
    signature: str, error: str = "KeyError: 'db'", *, new: str = "b", node: str = "laptop-ana"
) -> ReceivedSolution:
    return ReceivedSolution(
        solution=SharedSolution(
            node=node,
            published_at=NOW,
            signature=signature,
            error=error,
            patch=Patch(edits=(FileEdit(path="app/cfg.py", old="a", new=new),)),
            explanation="fix",
            model="claude-sonnet-5-5",
            tokens_spent=100,
            evidence=Evidence(command="pytest", exit_code_before=1, verified_at=NOW),
        ),
        received_at=NOW,
    )


class FakeStore:
    def __init__(
        self,
        same: ReceivedSolution | tuple[ReceivedSolution, ...] | None = None,
        nearby: tuple[TeamMatch, ...] = (),
    ) -> None:
        self.same = (same,) if isinstance(same, ReceivedSolution) else same or ()
        self.nearby = nearby
        self.limits: list[int] = []
        self.left_out: list[str] = []

    def for_signature(self, signature: str) -> list[ReceivedSolution]:
        return list(self.same)

    def similar(self, error: str, limit: int, *, other_than: str = "") -> list[TeamMatch]:
        self.limits.append(limit)
        self.left_out.append(other_than)
        return list(self.nearby)[:limit]


def finder(
    store: FakeStore,
    max_matches: int = 3,
    results: FakeResults | None = None,
    forgotten: FakeForgotten | None = None,
) -> SuggestionFinder:
    return SuggestionFinder(
        store,  # type: ignore[arg-type]
        results=results if results is not None else FakeResults(),
        forgotten=forgotten if forgotten is not None else FakeForgotten(),
        max_distance=0.1,
        max_matches=max_matches,
    )


def vote(received: ReceivedSolution, node: str, *, worked: bool) -> FixResult:
    solution = received.solution
    return FixResult(
        node=node,
        reported_at=NOW,
        signature=solution.signature,
        fix_id=solution.fix_id,
        worked=worked,
    )


def test_fixes_for_the_same_error_that_worked_elsewhere_come_first() -> None:
    newest = received(SIGNATURE, new="newest", node="laptop-eva")
    proven = received(SIGNATURE, new="proven")
    results = FakeResults(
        vote(proven, "laptop-sol", worked=True),
        vote(proven, "laptop-max", worked=True),
        vote(newest, "laptop-sol", worked=False),
    )
    matches = finder(FakeStore((newest, proven)), results=results).find(TARGET)
    assert [m.received for m in matches] == [proven, newest]
    assert [(m.worked, m.failed) for m in matches] == [(2, 0), (0, 1)]


def near(signature: str, distance: float = 0.05, error: str = "KeyError: 'db'") -> TeamMatch:
    return TeamMatch(received=received(signature, error), distance=distance)


def test_nothing_known() -> None:
    assert finder(FakeStore()).find(TARGET) == []


def test_the_same_error_comes_first() -> None:
    same = received(SIGNATURE)
    matches = finder(FakeStore(same, (near("b" * 64),))).find(TARGET)
    assert [m.received.solution.signature for m in matches] == [SIGNATURE, "b" * 64]
    assert [m.same_signature for m in matches] == [True, False]


def test_every_fix_for_the_same_error_comes_first_in_the_store_order() -> None:
    newest = received(SIGNATURE, new="newest", node="laptop-eva")
    oldest = received(SIGNATURE, new="oldest")
    store = FakeStore((newest, oldest), (near("b" * 64),))
    matches = finder(store).find(TARGET)
    assert [m.received for m in matches[:2]] == [newest, oldest]
    assert [m.same_signature for m in matches] == [True, True, False]
    assert store.left_out == [SIGNATURE]


def test_the_same_suggestion_is_not_offered_twice() -> None:
    same = received(SIGNATURE)
    matches = finder(FakeStore(same, (near(SIGNATURE, 0.0),))).find(TARGET)
    assert len(matches) == 1


def test_a_similar_error_must_be_close_enough() -> None:
    inside, outside = near("b" * 64, 0.1), near("c" * 64, 0.11)
    assert finder(FakeStore(None, (inside, outside))).find(TARGET) == [inside]


def test_a_similar_error_must_be_of_the_same_type() -> None:
    other = near("b" * 64, 0.0, error="ConnectionError: could not connect to db")
    prefixed = near("c" * 64, 0.0, error="KeyErrorSomething: 'db'")
    assert finder(FakeStore(None, (other, prefixed))).find(TARGET) == []


def test_an_error_without_a_message_can_match() -> None:
    bare = near("b" * 64, 0.0, error="KeyError")
    assert finder(FakeStore(None, (bare,))).find(TARGET) == [bare]


def test_at_most_the_limit_is_returned_and_the_search_looks_further() -> None:
    other_type = tuple(near(letter * 64, error="ValueError: bad") for letter in "bcd")
    valid = tuple(near(letter * 64) for letter in "efg")
    store = FakeStore(received(SIGNATURE), other_type + valid)
    matches = finder(store, max_matches=2).find(TARGET)
    assert [m.received.solution.signature for m in matches] == [SIGNATURE, "e" * 64]
    assert store.limits == [6]


def test_a_forgotten_fix_is_never_offered() -> None:
    kept, dropped = received(SIGNATURE, new="kept"), received(SIGNATURE, new="dropped")
    nearby = near("b" * 64)
    forgotten = FakeForgotten(dropped.solution.fix_id, nearby.received.solution.fix_id)
    matches = finder(FakeStore((kept, dropped), (nearby,)), forgotten=forgotten).find(TARGET)
    assert [m.received for m in matches] == [kept]
