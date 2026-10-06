import pytest

from healer.domain.errors import CommandNotAllowedError
from healer.domain.execution import CommandPolicy

POLICY = CommandPolicy(["pytest", "python -m pytest"])


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("pytest", ("pytest",)),
        ("python -m pytest", ("python", "-m", "pytest")),
        (
            "python -m pytest tests/test_app.py -k 'read and db'",
            (
                "python",
                "-m",
                "pytest",
                "tests/test_app.py",
                "-k",
                "read and db",
            ),
        ),
        ("pytest -x --tb=short tests", ("pytest", "-x", "--tb=short", "tests")),
    ],
)
def test_allowed_commands_are_split_into_argv(command: str, argv: tuple[str, ...]) -> None:
    assert POLICY.parse(command) == argv


@pytest.mark.parametrize(
    "command",
    [
        "",
        "rm -rf /",
        "python",
        "python -c 'import os'",
        "python -m pip install evil",
        "bash -c pytest",
        "pytest; rm -rf /",
        "/usr/bin/pytest",
        "pytester",
    ],
)
def test_commands_outside_the_allowlist_are_rejected(command: str) -> None:
    with pytest.raises(CommandNotAllowedError):
        POLICY.parse(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest /etc",
        "pytest ../other_project",
        "pytest tests/../../x",
        "pytest --rootdir=/",
        "pytest --junitxml=/tmp/report.xml",
        "pytest -c ~/.config/pytest.ini",
        "pytest C:\\\\Windows",
        "pytest 'tests\0'",
    ],
)
def test_arguments_escaping_the_sandbox_are_rejected(command: str) -> None:
    with pytest.raises(CommandNotAllowedError, match="outside the sandbox"):
        POLICY.parse(command)


def test_malformed_quoting_is_rejected() -> None:
    with pytest.raises(CommandNotAllowedError, match="Malformed"):
        POLICY.parse("pytest 'unclosed")


def test_shell_operators_are_plain_arguments_never_executed() -> None:
    assert POLICY.parse("pytest '&&' echo") == ("pytest", "&&", "echo")
