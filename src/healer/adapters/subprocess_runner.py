"""``CommandRunner`` based on ``subprocess``: no shell, clean environment, hard limits."""

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from healer.domain.errors import ExecutionError, RunAbandonedError
from healer.domain.execution import RunResult

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_POLL_SECONDS = 0.2
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
SAFE_ENV_NAMES = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM"})


class SubprocessRunner:
    """Runs a command in its own process group so a timeout also kills its children.

    The child gets a minimal environment, so credentials such as ``ANTHROPIC_API_KEY`` are
    not handed to the code under test. On its own this is not a security boundary: that
    code runs as the same user and can still read what this user can, including this
    process's environment through ``/proc``. The test sandbox adds that boundary: it runs
    this class in another container, as another user (``user``), and ``after_exit`` kills
    whatever the command left running before its output is collected.

    ``abandoned`` is asked every ``poll_seconds`` while the command runs. When it says that
    whoever asked for the run has left, the command and its children are killed like on a
    timeout, ``after_exit`` runs, and ``RunAbandonedError`` is raised instead of a result.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        environ: Mapping[str, str] | None = None,
        extra_env: Mapping[str, str] | None = None,
        user: int | None = None,
        after_exit: Callable[[], None] | None = None,
        abandoned: Callable[[], bool] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._env = {**_safe_env(os.environ if environ is None else environ), **(extra_env or {})}
        self._user = user
        self._after_exit = after_exit
        self._abandoned = abandoned
        self._poll = poll_seconds

    def run(self, argv: Sequence[str], cwd: Path) -> RunResult:
        start = time.monotonic()
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=self._env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                user=self._user,
                group=self._user,
                extra_groups=[] if self._user is not None else None,
            )
        except OSError as exc:
            raise ExecutionError(f"Cannot start {argv[0]!r}: {exc.strerror}") from exc
        assert process.stdout is not None
        tail = _Tail(self._max_output)
        reader = threading.Thread(target=tail.consume, args=(process.stdout,), daemon=True)
        reader.start()
        timed_out, abandoned = self._wait(process)
        if timed_out or abandoned:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if self._after_exit is not None:
            self._after_exit()
        reader.join(timeout=5)
        if abandoned:
            raise RunAbandonedError(f"the client left, so {argv[0]!r} was stopped")
        return RunResult(
            command=tuple(argv),
            exit_code=None if timed_out else process.returncode,
            output=tail.text(),
            duration_seconds=round(time.monotonic() - start, 3),
            timed_out=timed_out,
            output_truncated=tail.truncated,
        )

    def _wait(self, process: "subprocess.Popen[bytes]") -> tuple[bool, bool]:
        """Wait for the command. Returns whether it timed out, and whether it was abandoned."""
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return process.poll() is None, False
            try:
                process.wait(timeout=min(remaining, self._poll))
            except subprocess.TimeoutExpired:
                if self._abandoned is not None and self._abandoned():
                    return False, True
            else:
                return False, False


class _Tail:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self.truncated = False

    def consume(self, stream: IO[bytes]) -> None:
        while chunk := stream.read(65_536):
            self._data += chunk
            if len(self._data) > self._limit:
                del self._data[: len(self._data) - self._limit]
                self.truncated = True

    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")


def _safe_env(environ: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if k in SAFE_ENV_NAMES}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
