from pathlib import Path

import pytest

from healer.adapters.python_traceback import PythonTraceParser
from healer.domain.diagnosis import TracedError

TRACES = Path(__file__).parent.parent / "fixtures" / "traces"


def parse_fixture(name: str) -> list[TracedError]:
    return PythonTraceParser().parse((TRACES / name).read_text())


@pytest.mark.parametrize(
    ("fixture", "summary", "last_frame", "frame_count"),
    [
        ("script_key.txt", "KeyError: 'db'", ("/tmp/sample/app/cfg.py", 2, "read_db"), 2),
        (
            "script_lib.txt",
            "json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes:"
            " line 1 column 2 (char 1)",
            ("/usr/local/lib/python3.11/json/decoder.py", 353, "raw_decode"),
            4,
        ),
        ("syntax_direct.txt", "SyntaxError: invalid syntax", ("/tmp/sample/bad.py", 4, ""), 1),
        ("syntax_import.txt", "SyntaxError: invalid syntax", ("/tmp/sample/bad.py", 4, ""), 2),
        (
            "pytest_import_error.txt",
            "ModuleNotFoundError: No module named 'app'",
            ("tests/test_app.py", 1, "<module>"),
            2,
        ),
        (
            "pytest_collect.txt",
            "SyntaxError: invalid syntax",
            ("/tmp/sample/tests/test_collect.py", 1, ""),
            11,
        ),
    ],
)
def test_single_error_fixtures(
    fixture: str, summary: str, last_frame: tuple[str, int, str], frame_count: int
) -> None:
    [error] = parse_fixture(fixture)
    frame = error.frames[-1]
    assert error.summary == summary
    assert (frame.file, frame.line, frame.function) == last_frame
    assert len(error.frames) == frame_count


@pytest.mark.parametrize(
    "fixture", ["pytest_long.txt", "pytest_short.txt", "pytest_color.txt", "pytest_native.txt"]
)
def test_pytest_formats_agree(fixture: str) -> None:
    key, assertion = parse_fixture(fixture)
    assert key.summary == "KeyError: 'db'"
    assert key.frames[-1].file.endswith("app/cfg.py")
    assert (key.frames[-1].line, key.frames[-1].function) == (2, "read_db")
    assert key.frames[-1].code == 'return config["db"]'
    assert [f.function for f in key.frames[-3:]] == ["test_key", "helper", "read_db"]
    assert assertion.summary == "AssertionError: assert 2 == 3"
    assert assertion.frames[-1].function == "test_assert"


def test_explicit_chain_keeps_the_cause() -> None:
    [error] = parse_fixture("script_chain.txt")
    assert error.summary == "RuntimeError: cannot load /nope/settings.toml"
    assert error.causes == (
        "FileNotFoundError: [Errno 2] No such file or directory: '/nope/settings.toml'",
    )


def test_exception_during_handling_keeps_the_cause() -> None:
    [error] = parse_fixture("script_handling.txt")
    assert error.error_type == "ValueError"
    assert error.causes == ("KeyError: 'db'",)


def test_caret_lines_are_not_code() -> None:
    [error] = parse_fixture("script_key.txt")
    assert all("^" not in frame.code for frame in error.frames)


def test_independent_errors_in_one_log_are_all_found() -> None:
    text = (TRACES / "script_key.txt").read_text() + "\nsome log line\n"
    text += (TRACES / "syntax_direct.txt").read_text()
    assert [e.error_type for e in PythonTraceParser().parse(text)] == ["KeyError", "SyntaxError"]


def test_windows_line_endings_are_supported() -> None:
    text = (TRACES / "script_key.txt").read_text().replace("\n", "\r\n")
    [error] = PythonTraceParser().parse(text)
    assert error.summary == "KeyError: 'db'"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "all good\n3 passed in 0.1s\n",
        "Traceback (most recent call last):\n",
        'Traceback (most recent call last):\n  File "a.py", line 1, in f\n',
        "KeyError: 'db'\n",
    ],
)
def test_text_without_a_complete_traceback_yields_nothing(text: str) -> None:
    assert PythonTraceParser().parse(text) == []


def test_error_without_message() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 3, in f\n'
        "    stop()\n"
        "KeyboardInterrupt\n"
    )
    [error] = PythonTraceParser().parse(text)
    assert (error.error_type, error.message) == ("KeyboardInterrupt", "")
