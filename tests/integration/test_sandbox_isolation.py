"""The test sandbox contains hostile code: it runs a probe there and checks what it reached.

These tests use the real sandbox container, so they only run where ``SANDBOX_SOCKET`` and
``SANDBOX_DIR`` point to it, as in the cli container.
"""

import json
import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from healer.adapters.sandbox_client import SandboxServiceRunner
from healer.adapters.tempdir_sandbox import TempDirSandbox, TempDirSandboxProvider
from healer.domain.errors import ExecutionError

SOCKET = os.environ.get("SANDBOX_SOCKET")
COPIES = os.environ.get("SANDBOX_DIR")


def sandbox_answers() -> bool:
    if not SOCKET or not COPIES:
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        try:
            connection.connect(SOCKET)
        except OSError:
            return False
    return True


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not sandbox_answers(), reason="the test sandbox is not running here"),
]

PROBE = r"""
import json, os, socket, sys, time

results = {"uid": os.getuid()}


def attempt(label, action):
    try:
        action()
        results[label] = "allowed"
    except Exception as exc:
        results[label] = type(exc).__name__


attempt("connect to the broker", lambda: socket.create_connection(("mosquitto", 1883), 2).close())
attempt("connect to the internet", lambda: socket.create_connection(("8.8.8.8", 53), 2).close())
attempt("read the environment of init", lambda: open("/proc/1/environ", "rb").read())
runner_environ = f"/proc/{os.getppid()}/environ"
next_to_the_copy = os.path.join(os.path.dirname(os.getcwd()), "planted")
attempt("read the environment of the runner", lambda: open(runner_environ, "rb").read())
attempt("write next to the copy", lambda: open(next_to_the_copy, "w").write("x"))
attempt("write to the code", lambda: open("/app/planted", "w").write("x"))
attempt("write to the system", lambda: open("/usr/local/lib/planted", "w").write("x"))
attempt("open the socket folder", lambda: os.listdir(os.path.dirname(sys.argv[1])))
attempt("open another copy", lambda: os.listdir(sys.argv[2]))
attempt("read the workspace", lambda: os.listdir("/workspace"))
attempt("read the data volumes", lambda: os.listdir("/data"))
results["environment"] = sorted(os.environ)
open("/tmp/planted-marker", "w").write("x")
os.makedirs("/tmp/.local/lib", exist_ok=True)
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        time.sleep(300)
    os._exit(0)
print(json.dumps(results))
"""

SURVIVORS = r"""
import json, os

others = []
for pid in os.listdir("/proc"):
    if pid.isdigit() and int(pid) not in (1, os.getpid(), os.getppid()):
        try:
            if os.stat(f"/proc/{pid}").st_uid == os.getuid():
                others.append(open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace"))
        except OSError:
            pass
print(json.dumps({"processes": others, "tmp": sorted(os.listdir("/tmp"))}))
"""


@pytest.fixture
def copy_of(tmp_path: Path) -> Iterator[object]:
    """Make a copy of a workspace holding one script, as the cli container does."""
    made: list[TempDirSandbox] = []

    def make(name: str, source: str) -> TempDirSandbox:
        workspace = tmp_path / name
        workspace.mkdir()
        (workspace / "script.py").write_text(source)
        sandbox = TempDirSandboxProvider(workspace, base_dir=Path(str(COPIES))).create()
        made.append(sandbox)
        return sandbox

    yield make
    for sandbox in made:
        sandbox.close()


def runner(timeout: float = 60) -> SandboxServiceRunner:
    return SandboxServiceRunner(
        Path(str(SOCKET)), timeout_seconds=timeout, max_output_bytes=100_000
    )


def run_json(sandbox: TempDirSandbox, *args: str) -> dict[str, object]:
    result = runner().run(["python", "script.py", *args], sandbox.root)
    assert result.exit_code == 0, result.output
    data: dict[str, object] = json.loads(result.output.strip().splitlines()[-1])
    return data


def test_hostile_code_reaches_nothing_outside_its_copy(copy_of: object) -> None:
    waiting = copy_of("waiting", "print('waiting for its turn')")  # type: ignore[operator]
    probe = copy_of("probe", PROBE)  # type: ignore[operator]

    results = run_json(probe, str(SOCKET), str(waiting.root))

    assert results["uid"] != 0
    blocked = {label: outcome for label, outcome in results.items() if outcome == "allowed"}
    assert blocked == {}, f"the probe could {sorted(blocked)}"
    allowed = {"HOME", "LANG", "LC_CTYPE", "PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE"}
    assert set(results["environment"]) <= allowed | {"TMPDIR"}  # type: ignore[arg-type]


def test_nothing_a_run_leaves_behind_reaches_the_next_one(copy_of: object) -> None:
    run_json(copy_of("probe", PROBE), str(SOCKET), "/nonexistent")  # type: ignore[operator]

    after = run_json(copy_of("check", SURVIVORS))  # type: ignore[operator]

    assert after == {"processes": [], "tmp": []}


def test_the_sandbox_stops_a_run_at_the_time_limit(copy_of: object) -> None:
    sandbox = copy_of("slow", "import time; time.sleep(120)")  # type: ignore[operator]
    result = runner(timeout=2).run(["python", "script.py"], sandbox.root)
    assert result.timed_out
    assert result.duration_seconds < 30


def test_the_sandbox_refuses_a_folder_that_is_not_a_copy() -> None:
    with pytest.raises(ExecutionError, match="is not a copy prepared for the sandbox"):
        runner().run(["python", "-c", "print(1)"], Path("/etc"))


LIMITS = r"""
import json, os

def read(path):
    with open(path) as source:
        return source.read().strip()

mounts = [line.split() for line in read("/proc/mounts").splitlines()]
copies = next(fields for fields in mounts if fields[1] == "/sandbox")
print(json.dumps({
    "cpu": read("/sys/fs/cgroup/cpu.max"),
    "processes": read("/sys/fs/cgroup/pids.max"),
    "memory": read("/sys/fs/cgroup/memory.max"),
    "copies filesystem": copies[2],
    "copies options": copies[3],
}))
"""


def test_a_run_has_a_ceiling_for_cpu_processes_memory_and_space(copy_of: object) -> None:
    limits = run_json(copy_of("limits", LIMITS))  # type: ignore[operator]

    quota, period = str(limits["cpu"]).split()
    assert quota != "max" and int(quota) // int(period) >= 1
    assert limits["processes"] != "max"
    assert limits["memory"] != "max"
    assert limits["copies filesystem"] == "tmpfs"
    assert "size=" in str(limits["copies options"])
