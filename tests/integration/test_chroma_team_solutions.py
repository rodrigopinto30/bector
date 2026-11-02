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


def test_save_find_and_replace(repo: ChromaTeamSolutions) -> None:
    assert repo.find("a" * 64) is None
    first = received("a")
    repo.save(first)
    assert repo.find("a" * 64) == first
    second = received("a", new="None", at=NOW + timedelta(hours=1))
    repo.save(second)
    assert repo.find("a" * 64) == second
    assert repo.count() == 1


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


def test_a_corrupt_record_is_reported(repo: ChromaTeamSolutions) -> None:
    repo.save(received("a"))
    repo._collection.update(ids=["a" * 64], metadatas=[{"received_json": "{"}])
    with pytest.raises(IndexingError, match="corrupt"):
        repo.find("a" * 64)
    with pytest.raises(IndexingError, match="corrupt"):
        repo.all()
    with pytest.raises(IndexingError, match="corrupt"):
        repo.similar("KeyError: 'db'", limit=1)
