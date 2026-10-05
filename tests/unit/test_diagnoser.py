from healer.domain.diagnosis import StackFrame, TracedError
from healer.domain.models import Symbol, SymbolKind
from healer.services.diagnoser import Diagnoser


class FakeParser:
    def __init__(self, errors: list[TracedError]) -> None:
        self.errors = errors

    def parse(self, text: str) -> list[TracedError]:
        return self.errors


class FakeResolver:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def resolve(self, raw_path: str) -> str | None:
        return self.mapping.get(raw_path)


class FakeLocator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def enclosing_symbol(self, file: str, line: int) -> Symbol | None:
        self.calls.append((file, line))
        return Symbol(
            file=file,
            kind=SymbolKind.FUNCTION,
            name="read_db",
            qualified_name="read_db",
            start_line=1,
            end_line=3,
            signature="def read_db(config):",
            source="...",
        )


def frame(file: str, line: int, function: str) -> StackFrame:
    return StackFrame(file=file, line=line, function=function)


WORKSPACE = {"/w/main.py": "main.py", "/w/app/cfg.py": "app/cfg.py", "/w/tests/t.py": "tests/t.py"}
FROM_SCRIPT = TracedError(
    error_type="KeyError",
    message="'db'",
    frames=(
        frame("/w/main.py", 8, "<module>"),
        frame("/w/app/cfg.py", 2, "read_db"),
        frame("/usr/lib/python3.11/site-packages/lib/x.py", 5, "get"),
    ),
)
FROM_TEST = TracedError(
    error_type="KeyError",
    message="'host'",
    frames=(frame("/w/tests/t.py", 3, "test_x"), *FROM_SCRIPT.frames[1:]),
)


def diagnoser(*errors: TracedError, locator: FakeLocator | None = None) -> Diagnoser:
    return Diagnoser(FakeParser(list(errors)), FakeResolver(WORKSPACE), locator)


def test_origin_is_the_innermost_workspace_frame() -> None:
    [d] = diagnoser(FROM_SCRIPT).diagnose("")
    assert d.origin is not None
    assert (d.origin.workspace_file, d.origin.line) == ("app/cfg.py", 2)
    assert [f.workspace_file for f in d.error.frames] == ["main.py", "app/cfg.py", None]


def test_same_bug_from_different_entry_points_is_grouped() -> None:
    [d] = diagnoser(FROM_SCRIPT, FROM_TEST).diagnose("")
    assert d.occurrences == 2
    assert d.error.frames[0].workspace_file == "main.py"


def test_different_errors_are_kept_in_order() -> None:
    other = TracedError(error_type="ValueError", frames=(frame("/w/app/cfg.py", 9, "parse"),))
    result = diagnoser(other, FROM_SCRIPT).diagnose("")
    assert [d.error.error_type for d in result] == ["ValueError", "KeyError"]


def test_error_outside_the_workspace_has_no_origin() -> None:
    external = TracedError(
        error_type="OSError", frames=(frame("/usr/lib/python3.11/os.py", 1, "f"),)
    )
    locator = FakeLocator()
    [d] = diagnoser(external, locator=locator).diagnose("")
    assert d.origin is None
    assert d.location is None
    assert locator.calls == []
    assert d.signature_basis == "OSError||os.py:f"


def test_location_comes_from_the_locator() -> None:
    locator = FakeLocator()
    [d] = diagnoser(FROM_SCRIPT, locator=locator).diagnose("")
    assert locator.calls == [("app/cfg.py", 2)]
    assert d.location is not None and d.location.name == "read_db"


def test_no_errors_yields_no_diagnoses() -> None:
    assert diagnoser().diagnose("all good") == []
