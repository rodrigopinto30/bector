from pathlib import Path

import chromadb
import pytest

from healer.adapters.chroma_repository import ChromaSymbolRepository
from healer.adapters.python_parser import PythonParser
from healer.adapters.workspace_scanner import WorkspaceScanner
from healer.domain.errors import IndexingError
from healer.domain.models import Symbol
from healer.services.code_index import CodeIndex
from tests.conftest import FakeEmbeddingFunction, make_source

pytestmark = pytest.mark.integration

CONFIG = '''"""Configuration loading."""


def read_database_settings(config):
    """Read the db section of the config dict."""
    return config["db"]
'''
MATH = '''def average(numbers):
    """Compute the arithmetic mean of a list of numbers."""
    return sum(numbers) / len(numbers)
'''


@pytest.fixture
def repo(tmp_path: Path, fake_embeddings: FakeEmbeddingFunction) -> ChromaSymbolRepository:
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    return ChromaSymbolRepository(client, "test", fake_embeddings)


def symbols_of(path: str, content: str) -> list[Symbol]:
    return list(PythonParser().parse(make_source(path, content)).symbols)


def test_replace_and_read_back(repo: ChromaSymbolRepository) -> None:
    symbols = symbols_of("app/config.py", CONFIG)
    repo.replace_file("app/config.py", "h1", symbols)
    assert repo.indexed_files() == {"app/config.py": "h1"}
    assert repo.symbols_in_file("app/config.py") == sorted(symbols, key=lambda s: s.start_line)


def test_replace_file_drops_previous_symbols(repo: ChromaSymbolRepository) -> None:
    repo.replace_file("a.py", "h1", symbols_of("a.py", CONFIG))
    repo.replace_file("a.py", "h2", symbols_of("a.py", "x = 1\n"))
    assert repo.count() == 1
    assert repo.indexed_files() == {"a.py": "h2"}


def test_remove_file_only_affects_that_file(repo: ChromaSymbolRepository) -> None:
    repo.replace_file("a.py", "h", symbols_of("a.py", CONFIG))
    repo.replace_file("b.py", "h", symbols_of("b.py", MATH))
    repo.remove_file("a.py")
    assert set(repo.indexed_files()) == {"b.py"}


def test_search_ranks_the_relevant_symbol_first(repo: ChromaSymbolRepository) -> None:
    repo.replace_file("app/config.py", "h", symbols_of("app/config.py", CONFIG))
    repo.replace_file("app/math.py", "h", symbols_of("app/math.py", MATH))
    hits = repo.search("compute the arithmetic mean of numbers", limit=3)
    assert hits[0].symbol.qualified_name == "average"
    assert hits == sorted(hits, key=lambda h: h.distance)


def test_search_on_empty_index_returns_nothing(repo: ChromaSymbolRepository) -> None:
    assert repo.search("anything", limit=5) == []


def test_search_limit_is_capped_to_index_size(repo: ChromaSymbolRepository) -> None:
    repo.replace_file("b.py", "h", symbols_of("b.py", MATH))
    assert len(repo.search("mean", limit=50)) == 2  # module + function


def test_corrupt_record_raises_indexing_error(repo: ChromaSymbolRepository) -> None:
    repo.replace_file("b.py", "h", symbols_of("b.py", MATH))
    repo._collection.update(
        ids=[repo._collection.get()["ids"][0]], metadatas=[{"symbol_json": "{not json"}]
    )
    with pytest.raises(IndexingError):
        repo.symbols_in_file("b.py")


def test_index_persists_across_clients(
    tmp_path: Path, fake_embeddings: FakeEmbeddingFunction
) -> None:
    path = str(tmp_path / "chroma")
    first = ChromaSymbolRepository(chromadb.PersistentClient(path=path), "persist", fake_embeddings)
    first.replace_file("b.py", "h", symbols_of("b.py", MATH))
    second = ChromaSymbolRepository(
        chromadb.PersistentClient(path=path), "persist", fake_embeddings
    )
    assert second.indexed_files() == {"b.py": "h"}


def test_end_to_end_index_then_search(
    tmp_path: Path, fake_embeddings: FakeEmbeddingFunction
) -> None:
    workspace = tmp_path / "ws"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "config.py").write_text(CONFIG)
    (workspace / "app" / "stats.py").write_text(MATH)
    repository = ChromaSymbolRepository(
        chromadb.PersistentClient(path=str(tmp_path / "chroma")), "e2e", fake_embeddings
    )
    code_index = CodeIndex(WorkspaceScanner(workspace), PythonParser(), repository)

    assert code_index.index().files_indexed == 2
    assert code_index.index().files_unchanged == 2

    (workspace / "app" / "stats.py").write_text(MATH + "\n\ndef median(values):\n    return 0\n")
    (workspace / "app" / "config.py").unlink()
    report = code_index.index()
    assert (report.files_indexed, report.files_removed) == (1, 1)
    assert [s.qualified_name for s in code_index.outline("app/stats.py")] == [
        "stats",
        "average",
        "median",
    ]
    assert code_index.search("read config", limit=10)[0].symbol.file == "app/stats.py"


def test_default_embedder_is_used_when_none_is_given(tmp_path: Path) -> None:
    # Regression: an explicit None disabled embeddings and every upsert failed.
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    repository = ChromaSymbolRepository(client, "default-ef")
    assert repository._collection._embedding_function is not None
