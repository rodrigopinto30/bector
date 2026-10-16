from datetime import UTC, datetime
from pathlib import Path

import chromadb
import pytest

from healer.adapters.chroma_solutions import ChromaSolutionRepository
from healer.domain.errors import IndexingError
from healer.domain.patch import FileEdit, Patch
from healer.domain.solution import Solution
from tests.conftest import FakeEmbeddingFunction

pytestmark = pytest.mark.integration


def solution(new: str) -> Solution:
    return Solution(
        signature="a" * 64,
        error="KeyError: 'db'",
        patch=Patch(edits=(FileEdit(path="app/cfg.py", old='config["db"]', new=new),)),
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=2_900,
        command="python -m pytest",
        exit_code_before=1,
        verified_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


@pytest.fixture
def repo(tmp_path: Path, fake_embeddings: FakeEmbeddingFunction) -> ChromaSolutionRepository:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return ChromaSolutionRepository(client, "solutions-test", fake_embeddings)


def test_save_find_replace_and_remove(repo: ChromaSolutionRepository) -> None:
    assert repo.find("a" * 64) is None
    first = solution('config.get("db")')
    repo.save(first)
    assert repo.find("a" * 64) == first
    second = solution('config.get("db", "postgres")')
    repo.save(second)
    assert repo.find("a" * 64) == second and repo.count() == 1
    repo.remove("a" * 64)
    assert repo.find("a" * 64) is None


def test_corrupt_record_raises(repo: ChromaSolutionRepository) -> None:
    repo._collection.upsert(ids=["b" * 64], documents=["x"], metadatas=[{"solution_json": "{"}])
    with pytest.raises(IndexingError, match="corrupt"):
        repo.find("b" * 64)
