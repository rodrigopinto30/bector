from collections.abc import Iterable, Iterator

from healer.domain.models import FileMap, SearchHit, SourceFile, Symbol, SymbolKind
from healer.services.code_index import CodeIndex
from tests.conftest import make_source


class FakeScanner:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def scan(self) -> Iterator[SourceFile]:
        for path, content in self.files.items():
            yield make_source(path, content)


class FakeParser:
    def __init__(self) -> None:
        self.parsed: list[str] = []

    def parse(self, file: SourceFile) -> FileMap:
        self.parsed.append(file.path)
        symbol = Symbol(
            file=file.path,
            kind=SymbolKind.MODULE,
            name=file.path,
            qualified_name=file.path,
            start_line=1,
            end_line=1,
            source=file.content,
        )
        return FileMap(
            file=file.path, symbols=(symbol,), has_syntax_errors="BROKEN" in file.content
        )


class FakeRepository:
    def __init__(self) -> None:
        self.files: dict[str, tuple[str, list[Symbol]]] = {}

    def indexed_files(self) -> dict[str, str]:
        return {path: h for path, (h, _) in self.files.items()}

    def replace_file(self, file: str, content_hash: str, symbols: Iterable[Symbol]) -> None:
        self.files[file] = (content_hash, list(symbols))

    def remove_file(self, file: str) -> None:
        del self.files[file]

    def symbols_in_file(self, file: str) -> list[Symbol]:
        return self.files.get(file, ("", []))[1]

    def search(self, query: str, limit: int) -> list[SearchHit]:
        return []

    def count(self) -> int:
        return sum(len(s) for _, s in self.files.values())


def build(files: dict[str, str]) -> tuple[CodeIndex, FakeScanner, FakeParser, FakeRepository]:
    scanner, parser, repo = FakeScanner(files), FakeParser(), FakeRepository()
    return CodeIndex(scanner, parser, repo), scanner, parser, repo


def test_first_index_parses_every_file() -> None:
    code_index, _, parser, repo = build({"a.py": "a", "b.py": "b"})
    report = code_index.index()
    assert (report.files_indexed, report.files_unchanged, report.symbols_indexed) == (2, 0, 2)
    assert sorted(parser.parsed) == ["a.py", "b.py"]
    assert repo.count() == 2


def test_second_index_skips_unchanged_files() -> None:
    code_index, _, parser, _ = build({"a.py": "a", "b.py": "b"})
    code_index.index()
    parser.parsed.clear()
    report = code_index.index()
    assert (report.files_indexed, report.files_unchanged) == (0, 2)
    assert parser.parsed == []


def test_changed_file_is_reparsed_and_replaced() -> None:
    code_index, scanner, parser, repo = build({"a.py": "a", "b.py": "b"})
    code_index.index()
    parser.parsed.clear()
    scanner.files["a.py"] = "a2"
    report = code_index.index()
    assert parser.parsed == ["a.py"]
    assert (report.files_indexed, report.files_unchanged) == (1, 1)
    assert repo.files["a.py"][1][0].source == "a2"


def test_deleted_files_are_removed_from_the_index() -> None:
    code_index, scanner, _, repo = build({"a.py": "a", "b.py": "b"})
    code_index.index()
    del scanner.files["b.py"]
    report = code_index.index()
    assert report.files_removed == 1
    assert set(repo.files) == {"a.py"}


def test_syntax_errors_are_counted_but_still_indexed() -> None:
    code_index, _, _, repo = build({"a.py": "BROKEN"})
    report = code_index.index()
    assert report.files_with_syntax_errors == 1
    assert "a.py" in repo.files


def test_outline_returns_symbols_of_the_file() -> None:
    code_index, _, _, _ = build({"a.py": "a"})
    code_index.index()
    assert [s.file for s in code_index.outline("a.py")] == ["a.py"]
    assert code_index.outline("missing.py") == []


def test_enclosing_symbol_picks_the_narrowest_range() -> None:
    def symbol(name: str, kind: SymbolKind, start: int, end: int) -> Symbol:
        return Symbol(
            file="a.py",
            kind=kind,
            name=name,
            qualified_name=name,
            start_line=start,
            end_line=end,
            source="",
        )

    code_index, _, _, repo = build({})
    repo.files["a.py"] = (
        "h",
        [
            symbol("a", SymbolKind.MODULE, 1, 30),
            symbol("Repo", SymbolKind.CLASS, 5, 20),
            symbol("Repo.save", SymbolKind.METHOD, 10, 12),
        ],
    )
    assert code_index.enclosing_symbol("a.py", 11).name == "Repo.save"  # type: ignore[union-attr]
    assert code_index.enclosing_symbol("a.py", 6).name == "Repo"  # type: ignore[union-attr]
    assert code_index.enclosing_symbol("a.py", 25).name == "a"  # type: ignore[union-attr]
    assert code_index.enclosing_symbol("a.py", 99) is None
    assert code_index.enclosing_symbol("missing.py", 1) is None
