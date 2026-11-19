import sys
import time
from pathlib import Path

import pytest

from healer.adapters.subprocess_runner import SubprocessRunner
from healer.domain.errors import ExecutionError, RunAbandonedError

pytestmark = pytest.mark.integration

PYTHON = sys.executable


def run(code: str, cwd: Path, **kwargs: object) -> object:
    return SubprocessRunner(**kwargs).run([PYTHON, "-c", code], cwd)  # type: ignore[arg-type]


def test_success_and_combined_output(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        [PYTHON, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], tmp_path
    )
    assert result.passed
    assert result.exit_code == 0
    assert "out" in result.output and "err" in result.output


def test_failure_exit_code(tmp_path: Path) -> None:
    result = SubprocessRunner().run([PYTHON, "-c", "raise SystemExit(3)"], tmp_path)
    assert (result.exit_code, result.passed) == (3, False)


def test_runs_in_the_given_directory(tmp_path: Path) -> None:
    result = SubprocessRunner().run([PYTHON, "-c", "import os; print(os.getcwd())"], tmp_path)
    assert str(tmp_path.resolve()) in result.output


def test_timeout_kills_the_process_and_its_children(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([{PYTHON!r}, '-c', 'import time; time.sleep(60)'])\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()
    result = SubprocessRunner(timeout_seconds=1).run([PYTHON, "-c", code], tmp_path)
    assert time.monotonic() - start < 10
    assert result.timed_out
    assert result.exit_code is None
    assert not result.passed
    assert "started" in result.output


def test_output_is_capped_keeping_the_tail(tmp_path: Path) -> None:
    code = "print('x' * 5000); print('THE END')"
    result = SubprocessRunner(max_output_bytes=100).run([PYTHON, "-c", code], tmp_path)
    assert result.output_truncated
    assert len(result.output.encode()) <= 100
    assert result.output.rstrip().endswith("THE END")


def test_credentials_never_reach_the_child(tmp_path: Path) -> None:
    environ = {"PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "sk-secret", "AWS_SECRET": "x"}
    code = "import os; print(sorted(os.environ))"
    result = SubprocessRunner(environ=environ).run([PYTHON, "-c", code], tmp_path)
    assert "ANTHROPIC_API_KEY" not in result.output
    assert "AWS_SECRET" not in result.output
    assert "PATH" in result.output


def test_stdin_is_closed(tmp_path: Path) -> None:
    result = SubprocessRunner(timeout_seconds=5).run(
        [PYTHON, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path
    )
    assert "''" in result.output
    assert not result.timed_out


def test_shell_syntax_is_not_interpreted(tmp_path: Path) -> None:
    result = SubprocessRunner().run([PYTHON, "-c", "print('safe')", "; touch pwned"], tmp_path)
    assert result.passed
    assert not (tmp_path / "pwned").exists()


def test_missing_executable_raises_a_project_error(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError, match="Cannot start"):
        SubprocessRunner().run(["definitely-not-a-command"], tmp_path)


def running(pid: int) -> bool:
    """Whether a process runs. One that was killed but not yet collected by its parent, which
    is what is left when nothing in the container collects orphans, does not."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_run_whose_client_left_is_killed_with_its_children_and_cleaned_up(
    tmp_path: Path,
) -> None:
    code = (
        "import subprocess, time\n"
        f"child = subprocess.Popen([{PYTHON!r}, '-c', 'import time; time.sleep(60)'])\n"
        "open('child.pid', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    asked: list[float] = []
    cleaned: list[bool] = []

    def abandoned() -> bool:
        asked.append(time.monotonic())
        return (tmp_path / "child.pid").exists()

    runner = SubprocessRunner(
        timeout_seconds=60,
        after_exit=lambda: cleaned.append(True),
        abandoned=abandoned,
        poll_seconds=0.05,
    )
    start = time.monotonic()
    with pytest.raises(RunAbandonedError, match="the client left"):
        runner.run([PYTHON, "-c", code], tmp_path)
    assert time.monotonic() - start < 10
    assert cleaned == [True]
    child = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while running(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not running(child), "the child of the run was still running"


def test_a_client_that_stays_changes_nothing(tmp_path: Path) -> None:
    asked: list[bool] = []

    def abandoned() -> bool:
        asked.append(False)
        return False

    runner = SubprocessRunner(abandoned=abandoned, poll_seconds=0.05)
    result = runner.run([PYTHON, "-c", "import time; time.sleep(0.4); print('done')"], tmp_path)
    assert result.passed
    assert "done" in result.output
    assert len(asked) >= 2


def test_a_run_that_ends_first_is_never_taken_for_abandoned(tmp_path: Path) -> None:
    runner = SubprocessRunner(abandoned=lambda: True, poll_seconds=5)
    assert runner.run([PYTHON, "-c", "print('quick')"], tmp_path).passed


def test_the_time_limit_still_holds_while_the_client_is_watched(tmp_path: Path) -> None:
    runner = SubprocessRunner(timeout_seconds=1, abandoned=lambda: False, poll_seconds=0.05)
    start = time.monotonic()
    result = runner.run([PYTHON, "-c", "import time; time.sleep(60)"], tmp_path)
    assert time.monotonic() - start < 10
    assert result.timed_out
    assert result.exit_code is None
