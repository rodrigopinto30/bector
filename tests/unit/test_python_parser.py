from healer.adapters.python_parser import MAX_SOURCE_CHARS, PythonParser
from healer.domain.models import Symbol, SymbolKind
from tests.conftest import make_source

SAMPLE = '''"""Config helpers."""
import os
import os.path as osp
from collections import OrderedDict as OD
from . import sibling
from .pkg.mod import thing


@decorator
async def load(path: str, *, strict: bool = False) -> dict:
    """Load the file.

    Raises on error.
    """
    def inner():
        pass
    return {}


class Repo(Base):
    """Stores things."""

    @property
    def size(self) -> int:
        return 1

    def save(self, item,
             force=False):
        return item

    class Meta:
        def helper(self): ...


if True:
    def conditional():
        pass

try:
    def guarded():
        pass
except ImportError:
    pass
'''


def parse(content: str = SAMPLE, path: str = "app/config.py") -> dict[str, Symbol]:
    file_map = PythonParser().parse(make_source(path, content))
    return {s.qualified_name: s for s in file_map.symbols}


def test_extracts_expected_symbols_and_kinds() -> None:
    symbols = parse()
    kinds = {name: s.kind for name, s in symbols.items()}
    assert kinds == {
        "config": SymbolKind.MODULE,
        "load": SymbolKind.FUNCTION,
        "Repo": SymbolKind.CLASS,
        "Repo.size": SymbolKind.METHOD,
        "Repo.save": SymbolKind.METHOD,
        "Repo.Meta": SymbolKind.CLASS,
        "Repo.Meta.helper": SymbolKind.METHOD,
        "conditional": SymbolKind.FUNCTION,
        "guarded": SymbolKind.FUNCTION,
    }


def test_nested_functions_are_not_indexed_separately() -> None:
    assert "load.inner" not in parse() and "inner" not in parse()


def test_signature_is_normalised_and_excludes_body() -> None:
    symbols = parse()
    assert (
        symbols["load"].signature == "async def load(path: str, *, strict: bool = False) -> dict:"
    )
    assert symbols["Repo.save"].signature == "def save(self, item, force=False):"
    assert symbols["Repo"].signature == "class Repo(Base):"


def test_docstrings_are_cleaned() -> None:
    symbols = parse()
    assert symbols["load"].docstring == "Load the file.\n\nRaises on error."
    assert symbols["Repo"].docstring == "Stores things."
    assert symbols["config"].docstring == "Config helpers."
    assert symbols["Repo.save"].docstring == ""


def test_line_ranges_are_one_based_and_include_decorators() -> None:
    symbols = parse()
    assert symbols["load"].start_line == 9  # the '@decorator' line
    assert symbols["Repo.size"].decorators == ("@property",)
    assert symbols["Repo.size"].source.startswith("@property")
    assert symbols["config"].start_line == 1


def test_module_symbol_lists_imports() -> None:
    module = parse()["config"]
    assert module.imports == ("os", "os.path", "collections", ".", ".pkg.mod")
    assert "import os" in module.source


def test_syntax_errors_are_reported_but_definitions_survive() -> None:
    broken = "def ok():\n    return 1\n\ndef broken(:\n    pass\n\nclass Fine:\n    pass\n"
    file_map = PythonParser().parse(make_source("b.py", broken))
    names = {s.qualified_name for s in file_map.symbols}
    assert file_map.has_syntax_errors
    assert {"ok", "Fine"} <= names


def test_valid_file_has_no_syntax_errors() -> None:
    assert not PythonParser().parse(make_source("a.py", "x = 1\n")).has_syntax_errors


def test_unicode_source_is_handled() -> None:
    symbols = parse('def saludo(nombre):\n    """Hola, ñandú ✓."""\n    return "¡hola!"\n')
    assert symbols["saludo"].docstring == "Hola, ñandú ✓."


def test_oversized_source_is_truncated_and_flagged() -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(5000))
    symbol = parse(f"def big():\n{body}\n")["big"]
    assert symbol.truncated
    assert len(symbol.source) == MAX_SOURCE_CHARS


def test_symbol_ids_are_unique_for_redefinitions() -> None:
    content = (
        "class A:\n    @property\n    def x(self): ...\n    @x.setter\n    def x(self, v): ...\n"
    )
    file_map = PythonParser().parse(make_source("a.py", content))
    ids = [s.id for s in file_map.symbols]
    assert len(ids) == len(set(ids))


def test_empty_file_yields_only_the_module() -> None:
    file_map = PythonParser().parse(make_source("pkg/__init__.py", ""))
    assert [s.kind for s in file_map.symbols] == [SymbolKind.MODULE]


def test_large_file_does_not_crash_the_parser() -> None:
    # Regression: reading Point.row segfaulted on files of a few hundred lines.
    body = "\n".join(f"    x{i} = {i}" for i in range(2000))
    symbols = parse(f"def big():\n{body}\n")
    assert symbols["big"].end_line == 2001
    assert symbols["big"].start_line == 1
