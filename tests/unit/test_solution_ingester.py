from datetime import UTC, datetime, timedelta

import pytest

from healer.domain.errors import RejectedMessageError, SharingError
from healer.domain.patch import FileEdit, Patch, PatchPolicy
from healer.domain.sharing import (
    Evidence,
    IngestOutcome,
    ReceivedSolution,
    SharedSolution,
    seal,
)
from healer.services.solution_ingester import SolutionIngester

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
KEY = b"0123456789abcdef-team-key"
SIGNATURE = "a" * 64


class FakeStore:
    def __init__(self) -> None:
        self.saved: dict[str, ReceivedSolution] = {}
        self.writes = 0

    def find(self, signature: str) -> ReceivedSolution | None:
        return self.saved.get(signature)

    def save(self, received: ReceivedSolution) -> None:
        self.saved[received.solution.signature] = received
        self.writes += 1

    def all(self) -> list[ReceivedSolution]:
        return list(self.saved.values())


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


def ingester(store: FakeStore, *, node: str = "laptop-luis") -> SolutionIngester:
    return SolutionIngester(
        store,
        PatchPolicy(max_files=2, max_bytes=1_000),
        node=node,
        key=KEY,
        max_bytes=10_000,
        clock=lambda: NOW,
    )


def test_a_valid_message_is_kept_with_its_origin_and_time() -> None:
    store = FakeStore()
    ingested = ingester(store).ingest(message())
    assert ingested.outcome is IngestOutcome.NEW
    received = store.saved[SIGNATURE]
    assert received.solution.node == "laptop-ana" and received.received_at == NOW


def test_the_same_patch_again_changes_nothing() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message())
    later = message(node="laptop-eva", at=NOW + timedelta(hours=1))
    assert sut.ingest(later).outcome is IngestOutcome.DUPLICATE
    assert store.writes == 1
    assert store.saved[SIGNATURE].solution.node == "laptop-ana"


def test_a_newer_patch_replaces_the_older_one() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message())
    newer = message(at=NOW + timedelta(hours=1), new='c.get("db", "x")')
    assert sut.ingest(newer).outcome is IngestOutcome.REPLACED
    assert store.saved[SIGNATURE].solution.patch == patch('c.get("db", "x")')


def test_an_older_message_cannot_replace_a_newer_one() -> None:
    store = FakeStore()
    sut = ingester(store)
    sut.ingest(message(at=NOW + timedelta(hours=1), new='c.get("db", "x")'))
    assert sut.ingest(message()).outcome is IngestOutcome.OLDER
    assert store.saved[SIGNATURE].solution.patch == patch('c.get("db", "x")')


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
            FakeStore(), PatchPolicy(max_files=2, max_bytes=1), node="n", key=b"short", max_bytes=1
        )
