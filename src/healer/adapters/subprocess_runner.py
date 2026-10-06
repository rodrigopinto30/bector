"""``CommandRunner`` based on ``subprocess``: no shell, clean environment, hard limits."""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from healer.domain.errors import ExecutionError
from healer.domain.execution import RunResult

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
SAFE_ENV_NAMES = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM"})


class SubprocessRunner:
    """Runs a command in its own process group so a timeout also kills its children.

    The child only sees a minimal environment: credentials such as ``ANTHROPIC_API_KEY``
    never reach the code under test.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._env = _safe_env(os.environ if environ is None else environ)

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
            )
        except OSError as exc:
            raise ExecutionError(f"Cannot start {argv[0]!r}: {exc.strerror}") from exc
        assert process.stdout is not None
        tail = _Tail(self._max_output)
        reader = threading.Thread(target=tail.consume, args=(process.stdout,), daemon=True)
        reader.start()
        timed_out = False
        try:
            process.wait(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        reader.join(timeout=5)
        return RunResult(
            command=tuple(argv),
            exit_code=None if timed_out else process.returncode,
            output=tail.text(),
            duration_seconds=round(time.monotonic() - start, 3),
            timed_out=timed_out,
            output_truncated=tail.truncated,
        )


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
