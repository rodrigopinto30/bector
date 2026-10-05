import pytest

from healer.domain.diagnosis import StackFrame, TracedError
from healer.domain.signature import (
    compute_signature,
    normalize_message,
    portable_path,
    signature_basis,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("'db'", "<str>"),
        ('name "x" is not defined', "name <str> is not defined"),
        ("assert 2 == 3", "assert <num> == <num>"),
        ("object at 0x7f3a2b1c", "object at <addr>"),
        ("cannot load /home/ana/app/settings.toml", "cannot load <path>"),
        (r"cannot open C:\Users\ana\app.cfg", "cannot open <path>"),
        (
            "invalid literal for int() with base 10: 'abc'",
            "invalid literal for int() with base <num>: <str>",
        ),
        ("  too   many\tspaces ", "too many spaces"),
    ],
)
def test_normalize_message(message: str, expected: str) -> None:
    assert normalize_message(message) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/usr/lib/python3.11/site-packages/requests/models.py", "requests/models.py"),
        ("/opt/venv/lib/python3.11/dist-packages/yaml/parser.py", "yaml/parser.py"),
        ("/usr/local/lib/python3.11/json/decoder.py", "decoder.py"),
        (r"C:\Python311\Lib\site-packages\click\core.py", "click/core.py"),
        ("<frozen importlib._bootstrap>", "<frozen importlib._bootstrap>"),
    ],
)
def test_portable_path(path: str, expected: str) -> None:
    assert portable_path(path) == expected


def frame(file: str, line: int, function: str, workspace_file: str | None = None) -> StackFrame:
    return StackFrame(file=file, line=line, function=function, workspace_file=workspace_file)


def key_error(*frames: StackFrame, message: str = "'db'") -> TracedError:
    return TracedError(error_type="KeyError", message=message, frames=frames)


ORIGIN_A = frame("/home/ana/proj/app/cfg.py", 2, "read_db", "app/cfg.py")
ORIGIN_B = frame("/ci/build/app/cfg.py", 40, "read_db", "app/cfg.py")


def test_same_bug_on_two_machines_has_one_signature() -> None:
    a = key_error(frame("/home/ana/proj/main.py", 8, "<module>", "main.py"), ORIGIN_A)
    b = key_error(
        frame("/ci/build/tests/t.py", 3, "test_x", "tests/t.py"), ORIGIN_B, message="'host'"
    )
    assert compute_signature(signature_basis(a, 1)) == compute_signature(signature_basis(b, 1))


def test_different_function_changes_the_signature() -> None:
    other = frame("/x/app/cfg.py", 9, "read_cache", "app/cfg.py")
    assert signature_basis(key_error(ORIGIN_A), 0) != signature_basis(key_error(other), 0)


def test_different_error_type_changes_the_signature() -> None:
    value_error = TracedError(error_type="ValueError", message="'db'", frames=(ORIGIN_A,))
    assert signature_basis(value_error, 0) != signature_basis(key_error(ORIGIN_A), 0)


def test_library_frames_below_the_origin_are_part_of_the_signature() -> None:
    lib = frame("/usr/lib/python3.11/site-packages/yaml/parser.py", 77, "parse")
    basis = signature_basis(key_error(ORIGIN_A, lib), 0)
    assert basis == "KeyError|<str>|app/cfg.py:read_db|yaml/parser.py:parse"


def test_without_origin_every_frame_is_used_with_portable_paths() -> None:
    basis = signature_basis(key_error(frame("/a/b/tool.py", 1, "run")), None)
    assert basis == "KeyError|<str>|tool.py:run"


def test_signature_is_a_sha256_hex_digest() -> None:
    assert len(compute_signature("x")) == 64
