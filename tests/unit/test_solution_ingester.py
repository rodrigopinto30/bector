import json
from datetime import UTC, datetime, timedelta

import pytest

from healer.domain.errors import RejectedMessageError, SharingError
from healer.domain.patch import FileEdit, Patch, PatchPolicy
from healer.domain.sharing import (
    Evidence,
    FixResult,
    IngestOutcome,
    ReceivedSolution,
    ResultIngested,
    ResultOutcome,
    SharedSolution,
    seal,
    seal_result,
)
from healer.services.solution_ingester import SolutionIngester
from tests.conftest import FakeResults

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
KEY = b"0123456789abcdef-team-key"
SIGNATURE = "a" * 64


class FakeStore:
    """Kept by fix id, like the real store."""

    def __init__(self) -> None:
        self.saved: dict[str, ReceivedSolution] = {}
        self.writes = 0

    def for_signature(self, signature: str) -> list[ReceivedSolution]:
        same = [r for r in self.saved.values() if r.solution.signature == signature]
        return sorted(same, key=lambda r: r.solution.published_at, reverse=True)

    def save(self, received: ReceivedSolution) -> None:
        self.saved[received.solution.fix_id] = received
        self.writes += 1

    def remove(self, received: ReceivedSolution) -> None:
        del self.saved[received.solution.fix_id]
        self.writes += 1

    def patches(self) -> list[Patch]:
        """The kept patches, the most recently found first."""
        return [r.solution.patch for r in self.for_signature(SIGNATURE)]


def patch(new: str = 'c.get("db")', path: str = "app/cfg.py") -> Patch:
    return Patch(edits=(FileEdit(path=path, old='c["db"]', new=new),))


def message(
    *,
    node: str = "laptop-ana",
    at: datetime = NOW,
    new: str = 'c.get("db")',
    edits: Patch | None = None,
    exit_code_before: int | None = 1,
) -> bytes:
    solution = SharedSolution(
        node=node,
        published_at=at,
        signature=SIGNATURE,
        error="KeyError: 'db'",
        patch=edits or patch(new),
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=2_900,
        evidence=Evidence(
            command="python -m pytest", exit_code_before=exit_code_before, verified_at=at
        ),
    )
    return seal(solution, KEY)


def ingester(
    store: FakeStore,
    *,
    node: str = "laptop-luis",
    fixes_per_error: int = 2,
    results: FakeResults | None = None,
) -> SolutionIngester:
    return SolutionIngester(
        store,
        PatchPolicy(max_files=2, max_bytes=1_000),
        results=results if results is not None else FakeResults(),
        node=node,
        key=KEY,
        max_bytes=10_000,
        fixes_per_error=fixes_per_error,
        clock=lambda: NOW,
    )


def hours(n: int) -> datetime:
    return NOW + timedelta(hours=n)


def test_a_valid_message_is_kept_with_its_origin_and_time() -> None:
    store = FakeStore()
    ingested = ingester(store).ingest(message())
    assert ingested.outcome is IngestOutcome.NEW
    [received] = store.saved.values()
    assert received.solution.node == "laptop-ana" and received.received_at == NOW


def test_the_same_patch_again_changes_nothing() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message())
    later = message(node="laptop-eva", at=hours(1))
    assert sut.ingest(later).outcome is IngestOutcome.DUPLICATE
    assert store.writes == 1
    assert [r.solution.node for r in store.saved.values()] == ["laptop-ana"]


def test_another_patch_for_the_same_error_is_kept_beside_the_first() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message())
    other = message(node="laptop-eva", at=hours(1), new='c.get("db", "x")')
    assert sut.ingest(other).outcome is IngestOutcome.NEW
    assert store.patches() == [patch('c.get("db", "x")'), patch()]


def test_an_older_patch_is_kept_too_while_there_is_room() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message(at=hours(1), new='c.get("db", "x")'))
    assert sut.ingest(message()).outcome is IngestOutcome.NEW
    assert store.patches() == [patch('c.get("db", "x")'), patch()]


def test_when_the_error_is_full_a_newer_patch_drops_the_oldest() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message(at=hours(1), new="first"))
    sut.ingest(message(at=hours(2), new="second"))
    assert sut.ingest(message(at=hours(3), new="third")).outcome is IngestOutcome.REPLACED
    assert store.patches() == [patch("third"), patch("second")]


@pytest.mark.parametrize("found", [hours(0), hours(1)], ids=["older", "same time"])
def test_when_the_error_is_full_an_older_patch_is_ignored(found: datetime) -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message(at=hours(1), new="first"))
    sut.ingest(message(at=hours(2), new="second"))
    writes = store.writes
    assert sut.ingest(message(at=found, new="replayed")).outcome is IngestOutcome.OUTRANKED
    assert store.writes == writes
    assert store.patches() == [patch("second"), patch("first")]


def test_a_lower_limit_keeps_only_the_newest_fixes() -> None:
    store = FakeStore()
    roomy = ingester(store, fixes_per_error=3)
    for n, name in enumerate(["first", "second", "third"], start=1):
        roomy.ingest(message(at=hours(n), new=name))

    tight = ingester(store, fixes_per_error=2)
    assert tight.ingest(message(at=hours(4), new="fourth")).outcome is IngestOutcome.REPLACED
    assert store.patches() == [patch("fourth"), patch("third")]
    assert tight.ingest(message(at=hours(0), new="zero")).outcome is IngestOutcome.OUTRANKED


def test_an_error_must_keep_at_least_one_fix() -> None:
    with pytest.raises(ValueError, match="at least one fix"):
        ingester(FakeStore(), fixes_per_error=0)


def test_a_node_ignores_its_own_messages() -> None:
    store = FakeStore()
    assert ingester(store, node="laptop-ana").ingest(message()).outcome is IngestOutcome.OWN
    assert store.saved == {}


def test_a_forged_message_is_not_stored() -> None:
    store = FakeStore()
    forged = message().replace(b"laptop-ana", b"laptop-eve")
    with pytest.raises(RejectedMessageError, match="forged or altered"):
        ingester(store).ingest(forged)
    assert store.saved == {}


def test_evidence_of_passing_tests_before_the_patch_is_rejected() -> None:
    store = FakeStore()
    with pytest.raises(RejectedMessageError, match="failing before"):
        ingester(store).ingest(message(exit_code_before=0))
    assert store.saved == {}


def test_a_run_that_timed_out_before_the_patch_counts_as_failing() -> None:
    assert ingester(FakeStore()).ingest(message(exit_code_before=None)).outcome is IngestOutcome.NEW


@pytest.mark.parametrize(
    ("unsafe", "reason"),
    [
        (patch(path="../outside.py"), "outside the project"),
        (patch(path="/etc/cron.d/job"), "outside the project"),
        (patch(path=".git/hooks/pre-commit"), "protected folder"),
        (patch(new="x" * 2_000), "the limit is 1000"),
        (
            Patch(edits=tuple(FileEdit(path=f"app/{n}.py", old="a", new="b") for n in "xyz")),
            "the limit is 2",
        ),
    ],
)
def test_a_signed_message_with_an_unsafe_patch_is_rejected(unsafe: Patch, reason: str) -> None:
    store = FakeStore()
    with pytest.raises(RejectedMessageError, match=f"the patch is not safe: .*{reason}"):
        ingester(store).ingest(message(edits=unsafe))
    assert store.saved == {}


def test_a_short_team_key_is_refused() -> None:
    with pytest.raises(SharingError, match="at least 16 characters"):
        SolutionIngester(
            FakeStore(),
            PatchPolicy(max_files=2, max_bytes=1),
            results=FakeResults(),
            node="n",
            key=b"short",
            max_bytes=1,
            fixes_per_error=1,
        )


def solution_of(data: bytes) -> SharedSolution:
    return SharedSolution.model_validate(json.loads(data)["solution"])


def result_message(
    fix: SharedSolution, *, node: str = "laptop-sol", worked: bool = True, at: datetime = NOW
) -> bytes:
    result = FixResult(
        node=node, reported_at=at, signature=fix.signature, fix_id=fix.fix_id, worked=worked
    )
    return seal_result(result, KEY)


def test_when_the_error_is_full_the_fix_that_works_least_makes_room() -> None:
    store, results = FakeStore(), FakeResults()
    sut = ingester(store, results=results)
    failing = solution_of(message(at=hours(2), new="failing"))
    sut.ingest(message(at=hours(1), new="working"))
    sut.ingest(message(at=hours(2), new="failing"))
    sut.ingest(result_message(failing, worked=False))
    assert sut.ingest(message(at=hours(3), new="new")).outcome is IngestOutcome.REPLACED
    assert store.patches() == [patch("new"), patch("working")]


def test_a_fix_that_failed_and_comes_back_keeps_its_record() -> None:
    store, results = FakeStore(), FakeResults()
    sut = ingester(store, results=results, fixes_per_error=1)
    sut.ingest(message(at=hours(1), new="failing"))
    sut.ingest(result_message(solution_of(message(at=hours(1), new="failing")), worked=False))
    assert sut.ingest(message(at=hours(2), new="new")).outcome is IngestOutcome.REPLACED
    replayed = message(at=hours(1), new="failing")
    assert sut.ingest(replayed).outcome is IngestOutcome.OUTRANKED
    assert store.patches() == [patch("new")]


def test_a_result_counts_once_per_node_for_a_fix_kept_here() -> None:
    store, results = FakeStore(), FakeResults()
    sut = ingester(store, results=results)
    sut.ingest(message())
    fix = solution_of(message())

    counted = sut.ingest(result_message(fix))
    assert isinstance(counted, ResultIngested)
    assert (counted.outcome, counted.fix) == (ResultOutcome.COUNTED, fix)
    again = sut.ingest(result_message(fix, at=hours(1)))
    assert isinstance(again, ResultIngested) and again.outcome is ResultOutcome.KNOWN
    changed = sut.ingest(result_message(fix, worked=False, at=hours(2)))
    assert isinstance(changed, ResultIngested) and changed.outcome is ResultOutcome.COUNTED
    assert results.tally([fix.fix_id])[fix.fix_id].failed == 1


def test_a_result_about_an_unknown_fix_or_from_this_node_is_not_stored() -> None:
    store, results = FakeStore(), FakeResults()
    sut = ingester(store, results=results)
    unknown = sut.ingest(result_message(solution_of(message())))
    assert isinstance(unknown, ResultIngested) and unknown.outcome is ResultOutcome.UNKNOWN
    sut.ingest(message())
    own = sut.ingest(result_message(solution_of(message()), node="laptop-luis"))
    assert isinstance(own, ResultIngested) and own.outcome is ResultOutcome.OWN
    assert results.votes == {}


def test_a_forged_result_is_rejected() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message())
    fix = solution_of(message())
    forged = FixResult(
        node="laptop-sol", reported_at=NOW, signature=fix.signature, fix_id=fix.fix_id, worked=True
    )
    with pytest.raises(RejectedMessageError, match="forged or altered"):
        sut.ingest(seal_result(forged, b"not-the-team-key-0123456789"))
