from datetime import UTC, datetime, timedelta
from pathlib import Path

import chromadb
import pytest

from healer.adapters.chroma_team_solutions import ChromaTeamSolutions
from healer.domain.errors import IndexingError
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import Evidence, ReceivedSolution, SharedSolution
from tests.conftest import FakeEmbeddingFunction

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def received(
    letter: str,
    *,
    new: str = 'c.get("db")',
    at: datetime = NOW,
    error: str = "KeyError: 'db'",
) -> ReceivedSolution:
    return ReceivedSolution(
        solution=SharedSolution(
            node="laptop-ana",
            published_at=at,
            signature=letter * 64,
            error=error,
            patch=Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new=new),)),
            explanation="Use a default.",
            model="claude-sonnet-5-5",
            tokens_spent=2_900,
            evidence=Evidence(command="pytest", exit_code_before=1, verified_at=at),
        ),
        received_at=at,
    )


@pytest.fixture
def repo(tmp_path: Path, fake_embeddings: FakeEmbeddingFunction) -> ChromaTeamSolutions:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return ChromaTeamSolutions(client, "team-test", fake_embeddings)


SIGNATURE = "a" * 64


def test_several_fixes_for_one_error_are_kept_the_newest_first(
    repo: ChromaTeamSolutions,
) -> None:
    assert repo.for_signature(SIGNATURE) == []
    first = received("a")
    second = received("a", new="None", at=NOW + timedelta(hours=1))
    repo.save(first)
    repo.save(second)
    assert repo.for_signature(SIGNATURE) == [second, first]
    assert repo.for_signature("b" * 64) == []
    assert repo.count() == 2


def test_the_same_fix_is_stored_once(repo: ChromaTeamSolutions) -> None:
    repo.save(received("a"))
    again = received("a", at=NOW + timedelta(hours=1))
    repo.save(again)
    assert repo.for_signature(SIGNATURE) == [again]
    assert repo.count() == 1


def test_remove_forgets_one_fix_and_keeps_the_others(repo: ChromaTeamSolutions) -> None:
    first = received("a")
    second = received("a", new="None", at=NOW + timedelta(hours=1))
    repo.save(first)
    repo.save(second)
    repo.remove(first)
    assert repo.for_signature(SIGNATURE) == [second]
    repo.remove(first)
    assert repo.count() == 1


def test_a_record_saved_as_the_only_fix_of_its_error_is_still_used(
    repo: ChromaTeamSolutions,
) -> None:
    """Before ADR 0021 the signature was the record id."""
    old = received("a")
    repo._collection.upsert(
        ids=[SIGNATURE],
        documents=[old.solution.error],
        metadatas=[{"signature": SIGNATURE, "received_json": old.model_dump_json()}],
    )
    newer = received("a", new="None", at=NOW + timedelta(hours=1))
    repo.save(newer)
    assert repo.for_signature(SIGNATURE) == [newer, old]
    repo.remove(old)
    assert repo.for_signature(SIGNATURE) == [newer]


def test_all_lists_the_most_recently_received_first(repo: ChromaTeamSolutions) -> None:
    assert repo.all() == []
    older, newer = received("a"), received("b", at=NOW + timedelta(hours=1))
    repo.save(older)
    repo.save(newer)
    assert repo.all() == [newer, older]


def test_similar_returns_the_closest_errors_first(repo: ChromaTeamSolutions) -> None:
    assert repo.similar("KeyError: 'db'", limit=3) == []
    near = received("a", error="KeyError: 'db'")
    far = received("b", error="ZeroDivisionError: division by zero")
    repo.save(far)
    repo.save(near)

    matches = repo.similar("KeyError: 'db'", limit=5)

    assert [m.received for m in matches] == [near, far]
    assert matches[0].distance == pytest.approx(0, abs=1e-6)
    assert matches[0].distance >= 0
    assert matches[1].distance > matches[0].distance
    assert not matches[0].same_signature
    assert [m.received for m in repo.similar("KeyError: 'db'", limit=1)] == [near]


def test_similar_can_leave_out_the_fixes_of_one_error(repo: ChromaTeamSolutions) -> None:
    same = received("a")
    nearby = received("b", error="KeyError: 'database'")
    repo.save(same)
    repo.save(nearby)
    matches = repo.similar("KeyError: 'db'", limit=5, other_than=SIGNATURE)
    assert [m.received for m in matches] == [nearby]


def test_a_corrupt_record_is_reported(repo: ChromaTeamSolutions) -> None:
    stored = received("a")
    repo.save(stored)
    repo._collection.update(
        ids=[stored.solution.fix_id], metadatas=[{"signature": SIGNATURE, "received_json": "{"}]
    )
    with pytest.raises(IndexingError, match="corrupt"):
        repo.for_signature(SIGNATURE)
    with pytest.raises(IndexingError, match="corrupt"):
        repo.all()
    with pytest.raises(IndexingError, match="corrupt"):
        repo.similar("KeyError: 'db'", limit=1)
