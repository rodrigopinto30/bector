import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from healer.adapters.sandbox_client import SandboxServiceRunner
from healer.adapters.sandbox_protocol import MAX_REQUEST_BYTES
from healer.adapters.sandbox_server import SandboxServer, _peer_gone
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


def test_a_client_is_gone_only_when_it_closed_the_connection() -> None:
    server_end, client_end = socket.socketpair()
    with server_end, client_end:
        gone = _peer_gone(server_end)
        assert not gone()
        client_end.sendall(b"more than the request")
        assert not gone()
        client_end.shutdown(socket.SHUT_WR)
        assert not gone()
        client_end.close()
        assert gone()


def test_a_client_that_closed_before_its_run_starts_gets_no_run(copies: Path) -> None:
    server = SandboxServer(copies / "run.sock", copies, user=None)
    copy = a_copy(copies)
    marker = copy / "ran"
    script = f"open({str(marker)!r}, 'w').close()"
    response = server.handle(
        request(argv=[sys.executable, "-c", script], cwd=str(copy)), gone=lambda: True
    )
    assert not response.ok
    assert "the client left before its run started" in response.error
    assert not marker.exists()
    assert not copy.exists()


def test_the_copy_of_an_abandoned_run_is_deleted_even_with_what_the_run_left_in_it(
    copies: Path,
) -> None:
    server = SandboxServer(copies / "run.sock", copies, user=None)
    copy = a_copy(copies)
    locked = copy / "locked" / "deep"
    locked.mkdir(parents=True)
    (locked / "file.txt").write_text("x")
    (copy / "locked").chmod(0o500)
    locked.chmod(0o500)
    response = server.handle(request(argv=["true"], cwd=str(copy)), gone=lambda: True)
    assert not response.ok
    assert not copy.exists()


def test_a_run_whose_client_leaves_while_it_runs_is_stopped_and_its_copy_deleted(
    copies: Path,
) -> None:
    cleaned: list[bool] = []
    server = SandboxServer(
        copies / "run.sock", copies, user=None, after_run=lambda: cleaned.append(True)
    )
    copy = a_copy(copies)
    started = copy / "started"
    script = f"import time; open({str(started)!r}, 'w').close(); time.sleep(60)"
    start = time.monotonic()
    response = server.handle(
        request(argv=[sys.executable, "-c", script], cwd=str(copy), timeout_seconds=60),
        gone=started.exists,
    )
    assert time.monotonic() - start < 10
    assert not response.ok
    assert "the client left" in response.error
    assert cleaned == [True]
    assert not copy.exists()


def alive(pid: int) -> bool:
    """Whether a process runs. One that was killed but not yet collected does not."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_client_that_leaves_frees_the_sandbox_for_the_next_one(
    serving: Path, copies: Path
) -> None:
    copy = a_copy(copies)
    pid_file = copy / "pid"
    script = (
        f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    )
    leaving = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leaving.connect(str(serving))
    leaving.sendall(
        request(argv=[sys.executable, "-c", script], cwd=str(copy), timeout_seconds=60) + b"\n"
    )
    deadline = time.monotonic() + 10
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int(pid_file.read_text())
    assert alive(pid)

    leaving.close()
    waiting = client(serving, timeout=30)
    start = time.monotonic()
    result = waiting.run(
        [sys.executable, "-c", "print('served')"], a_copy(copies, "healer-sandbox-next")
    )
    assert time.monotonic() - start < 10
    assert result.output.strip() == "served"
    deadline = time.monotonic() + 5
    while (alive(pid) or copy.exists()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid)
    assert not copy.exists()


def test_a_client_that_only_closes_its_writing_half_still_gets_its_answer(
    serving: Path, copies: Path
) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(serving))
        connection.sendall(
            request(
                argv=[sys.executable, "-c", "import time; time.sleep(0.6); print('ok')"],
                cwd=str(a_copy(copies)),
            )
            + b"\n"
        )
        connection.shutdown(socket.SHUT_WR)
        answer = json.loads(connection.recv(65_536))
    assert answer["ok"] is True
    assert answer["result"]["output"].strip() == "ok"


def make_old(path: Path, hours: float) -> None:
    then = time.time() - hours * 3_600
    os.utime(path, (then, then))


def test_a_copy_nobody_used_for_longer_than_a_run_can_last_is_deleted(
    copies: Path, tmp_path: Path
) -> None:
    server = SandboxServer(copies / "run.sock", copies, user=None)
    stale = a_copy(copies, "healer-sandbox-stale")
    (stale / "file.txt").write_text("x")
    make_old(stale, 3)
    recent = a_copy(copies, "healer-sandbox-recent")
    make_old(recent, 1)
    fresh = a_copy(copies, "healer-sandbox-fresh")
    other = copies / "someone-else"
    other.mkdir()
    make_old(other, 5)
    target = tmp_path / "outside"
    target.mkdir()
    make_old(target, 5)
    target.chmod(0o755)
    link = copies / "healer-sandbox-link"
    link.symlink_to(target)
    a_file = copies / "healer-sandbox-file"
    a_file.write_text("x")
    make_old(a_file, 5)

    server._sweep()

    assert not stale.exists()
    assert recent.exists()
    assert fresh.exists()
    assert other.exists()
    assert link.is_symlink()
    assert target.exists()
    assert target.stat().st_mode & 0o777 == 0o755
    assert a_file.exists()


def test_the_sweep_goes_by_the_clock_it_is_given(copies: Path) -> None:
    now = [time.time()]
    server = SandboxServer(copies / "run.sock", copies, user=None, clock=lambda: now[0])
    copy = a_copy(copies)
    server._sweep()
    assert copy.exists()
    now[0] += 2 * 3_600 - 60
    server._sweep()
    assert copy.exists()
    now[0] += 120
    server._sweep()
    assert not copy.exists()


def test_the_sandbox_clears_the_stale_copies_when_it_starts_and_after_each_request(
    copies: Path,
) -> None:
    before = a_copy(copies, "healer-sandbox-before")
    make_old(before, 3)
    socket_path = copies.parent / "run.sock"
    server = SandboxServer(socket_path, copies, user=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.monotonic() + 5
    while (before.exists() or not socket_path.exists()) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not before.exists()

    later = a_copy(copies, "healer-sandbox-later")
    make_old(later, 3)
    client(socket_path).check()
    deadline = time.monotonic() + 5
    while later.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not later.exists()


def test_a_copy_that_cannot_be_deleted_is_told_in_the_log(
    copies: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        failed = argv[0] == "rm"
        return subprocess.CompletedProcess(
            argv, 1 if failed else 0, b"", b"Permission denied" if failed else b""
        )

    monkeypatch.setattr("healer.adapters.sandbox_server.subprocess.run", fake_run)
    server = SandboxServer(copies / "run.sock", copies, user=None, client=(1000, 1000))
    copy = a_copy(copies)
    with caplog.at_level(logging.WARNING):
        server._discard(copy)
    assert [argv[0] for argv, _ in calls] == ["chmod", "rm"]
    for argv, kwargs in calls:
        assert argv[-2:] == ["--", str(copy)]
        assert (kwargs["user"], kwargs["group"], kwargs["extra_groups"]) == (1000, 1000, [])
    assert "rm of the copy healer-sandbox-abc123 failed: Permission denied" in caplog.text
