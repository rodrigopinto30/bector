from datetime import UTC, datetime, timedelta
from pathlib import Path

import chromadb
import pytest

from healer.adapters.chroma_panel import ChromaPanelStore
from healer.adapters.chroma_repository import ChromaSymbolRepository
from healer.adapters.chroma_solutions import ChromaSolutionRepository
from healer.adapters.chroma_team_solutions import ChromaTeamSolutions
from healer.domain.models import Symbol, SymbolKind
from healer.domain.patch import FileEdit, Patch
from healer.domain.sharing import Evidence, ReceivedSolution, SharedSolution
from healer.domain.solution import Solution
from tests.conftest import FakeEmbeddingFunction

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PATCH = Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new='c.get("db")'),))


def solution(letter: str, at: datetime) -> Solution:
    return Solution(
        signature=letter * 64,
        error=f"KeyError {letter}",
        patch=PATCH,
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=100,
        command="pytest",
        exit_code_before=1,
        verified_at=at,
    )


def store(client: chromadb.api.ClientAPI) -> ChromaPanelStore:
    return ChromaPanelStore(
        client,
        code_collection=lambda path: f"code-{Path(path).name}",
        fixes_collection=lambda path: f"solutions-{Path(path).name}",
        team_collection="team-test",
        embedding_function=FakeEmbeddingFunction(),
    )


def test_it_reads_what_the_other_commands_wrote(tmp_path: Path) -> None:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    fake = FakeEmbeddingFunction()
    code = ChromaSymbolRepository(client, "code-shop", fake)
    code.replace_file(
        "app.py",
        "hash",
        [
            Symbol(
                file="app.py",
                kind=SymbolKind.FUNCTION,
                name="f",
                qualified_name="f",
                start_line=1,
                end_line=2,
                source="def f():\n    pass\n",
            )
        ],
    )
    fixes = ChromaSolutionRepository(client, "solutions-shop", fake)
    fixes.save(solution("a", NOW - timedelta(days=1)))
    fixes.save(solution("b", NOW))
    team = ChromaTeamSolutions(client, "team-test", fake)
    shared = SharedSolution(
        node="laptop-ana",
        published_at=NOW,
        signature="c" * 64,
        error="ZeroDivisionError",
        patch=PATCH,
        explanation="Check first.",
        model="claude-sonnet-5-5",
        tokens_spent=50,
        evidence=Evidence(command="pytest", exit_code_before=1, verified_at=NOW),
    )
    team.save(ReceivedSolution(solution=shared, received_at=NOW))

    reader = store(client)
    assert reader.symbols("/workspace/shop") == 1
    assert [s.signature[0] for s in reader.fixes("/workspace/shop")] == ["b", "a"]
    assert [r.solution.node for r in reader.team()] == ["laptop-ana"]


def test_reading_creates_no_collection(tmp_path: Path) -> None:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    reader = store(client)
    assert reader.symbols("/workspace/never-indexed") is None
    assert reader.fixes("/workspace/never-indexed") == []
    assert reader.team() == []
    assert client.list_collections() == []
