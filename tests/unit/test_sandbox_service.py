import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from healer.adapters.sandbox_client import SandboxServiceRunner
from healer.adapters.sandbox_protocol import MAX_REQUEST_BYTES
from healer.adapters.sandbox_server import SandboxServer
from healer.domain.errors import ExecutionError


@pytest.fixture
def copies(tmp_path: Path) -> Path:
    path = tmp_path / "copies"
    path.mkdir()
    return path


def a_copy(copies: Path, name: str = "healer-sandbox-abc123") -> Path:
    path = copies / name
    path.mkdir()
    return path


def request(**fields: object) -> bytes:
    return json.dumps(fields).encode()


def test_a_command_runs_in_the_copy_and_the_clean_up_follows(copies: Path) -> None:
    cleaned: list[bool] = []
    server = SandboxServer(
        copies / "run.sock", copies, user=None, after_run=lambda: cleaned.append(True)
    )
    copy = a_copy(copies)
    response = server.handle(
        request(argv=[sys.executable, "-c", "import os; print(os.getcwd())"], cwd=str(copy))
    )
    assert response.ok, response.error
    assert response.result is not None
    assert response.result.exit_code == 0
    assert response.result.output.strip() == str(copy)
    assert cleaned == [True]


def test_the_command_gets_a_clean_environment(
    copies: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    server = SandboxServer(copies / "run.sock", copies, user=None)
    script = "import os; print(sorted(os.environ))"
    response = server.handle(request(argv=[sys.executable, "-c", script], cwd=str(a_copy(copies))))
    assert response.result is not None
    names = response.result.output
    assert "ANTHROPIC_API_KEY" not in names
    assert "PYTHONNOUSERSITE" in names


@pytest.mark.parametrize(
    ("make_cwd", "problem"),
    [
        (lambda copies: str(copies.parent / "healer-sandbox-out"), "is not a copy prepared"),
        (lambda copies: str(copies / "other-name"), "is not a copy prepared"),
        (lambda copies: str(copies / "healer-sandbox-missing"), "is not a copy prepared"),
        (lambda copies: str(copies / "healer-sandbox-x" / ".." / ".." / "etc"), "is not a copy"),
    ],
    ids=["outside the copies folder", "another name", "missing", "dot-dot"],
)
def test_only_prepared_copies_are_run(copies: Path, make_cwd: object, problem: str) -> None:
    server = SandboxServer(copies / "run.sock", copies, user=None)
    cwd = make_cwd(copies)  # type: ignore[operator]
    response = server.handle(request(argv=["true"], cwd=cwd))
    assert not response.ok
    assert problem in response.error


def test_a_symlink_is_not_a_copy(copies: Path, tmp_path: Path) -> None:
    (copies / "healer-sandbox-link").symlink_to(tmp_path)
    server = SandboxServer(copies / "run.sock", copies, user=None)
    response = server.handle(request(argv=["true"], cwd=str(copies / "healer-sandbox-link")))
    assert not response.ok
    assert "is not a copy prepared" in response.error


@pytest.mark.parametrize(
    "data",
    [
        b"not json",
        request(argv=["true"], cwd="/x", extra="field"),
        request(argv=["true"], cwd="/x", timeout_seconds=99_999),
        request(argv=["x"] * 65, cwd="/x"),
    ],
    ids=["not json", "unknown field", "timeout too long", "too many arguments"],
)
def test_invalid_requests_are_refused(copies: Path, data: bytes) -> None:
    response = SandboxServer(copies / "run.sock", copies, user=None).handle(data)
    assert not response.ok
    assert "invalid request" in response.error


def test_a_request_without_a_command_is_refused(copies: Path) -> None:
    server = SandboxServer(copies / "run.sock", copies, user=None)
    response = server.handle(request(argv=[], cwd=str(a_copy(copies))))
    assert not response.ok
    assert "no command" in response.error


@pytest.fixture
def serving(copies: Path) -> Path:
    socket_path = copies.parent / "run.sock"
    server = SandboxServer(socket_path, copies, user=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.monotonic() + 5
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return socket_path


def client(socket_path: Path, timeout: float = 30) -> SandboxServiceRunner:
    return SandboxServiceRunner(socket_path, timeout_seconds=timeout, max_output_bytes=10_000)


def test_the_client_runs_a_command_through_the_socket(serving: Path, copies: Path) -> None:
    runner = client(serving)
    runner.check()
    result = runner.run([sys.executable, "-c", "print('from the sandbox')"], a_copy(copies))
    assert (result.exit_code, result.output.strip()) == (0, "from the sandbox")
    assert serving.stat().st_mode & 0o777 == 0o600


def test_the_sandbox_enforces_the_time_limit(serving: Path, copies: Path) -> None:
    result = client(serving, timeout=1).run(
        [sys.executable, "-c", "import time; time.sleep(30)"], a_copy(copies)
    )
    assert result.timed_out
    assert result.exit_code is None


def test_a_refused_run_is_reported(serving: Path, copies: Path) -> None:
    with pytest.raises(ExecutionError, match="the test sandbox refused the run: .*not a copy"):
        client(serving).run(["true"], copies.parent)


def test_a_command_too_long_for_the_sandbox_is_not_sent(serving: Path, copies: Path) -> None:
    with pytest.raises(ExecutionError, match="the command is too long for the test sandbox"):
        client(serving).run(["x" * 2_000] * 40, a_copy(copies))


def test_the_server_refuses_a_request_that_is_too_large(serving: Path) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(serving))
        connection.sendall(b"x" * (MAX_REQUEST_BYTES + 100))
        connection.shutdown(socket.SHUT_WR)
        answer = connection.recv(65_536)
    assert json.loads(answer) == {"ok": False, "error": "the request is too large", "result": None}


def test_a_sandbox_that_is_not_running_is_explained(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError, match="the test sandbox is not running .*./start.sh"):
        client(tmp_path / "missing.sock").check()
