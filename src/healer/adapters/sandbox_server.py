"""The test sandbox: runs test commands for the cli container, away from its secrets (7.5).

It runs in its own container, with no network, no ``.env``, no workspace or data volumes,
a read-only root filesystem and only the capabilities it needs. The only thing it shares
with the cli container is the volume with the copies, and the socket it listens on, which
only root can use. Each command runs as an unprivileged user that owns nothing else; when
it ends, every process it left behind is killed and ``/tmp`` is emptied, so one run cannot
affect the next.

Run it with ``python -m healer.adapters.sandbox_server``.
"""

import contextlib
import logging
import os
import re
import signal
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from healer.adapters.sandbox_protocol import (
    MAX_REQUEST_BYTES,
    SandboxRequest,
    SandboxResponse,
)
from healer.adapters.subprocess_runner import SubprocessRunner
from healer.domain.errors import ExecutionError

logger = logging.getLogger(__name__)

SANDBOX_USER = 10001
_COPY_NAME = re.compile(r"^healer-sandbox-[A-Za-z0-9_]+$")
_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "HOME": "/tmp",
    "TMPDIR": "/tmp",
    "PYTHONNOUSERSITE": "1",
}


class SandboxServer:
    """Answers one request per connection, one at a time."""

    def __init__(
        self,
        socket_path: Path,
        copies_dir: Path,
        *,
        user: int | None = SANDBOX_USER,
        after_run: Callable[[], None] | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._copies_dir = copies_dir
        self._user = user
        self._after_run = after_run

    def handle(self, data: bytes) -> SandboxResponse:
        """Answer one raw request. Nothing it receives is trusted."""
        try:
            request = SandboxRequest.model_validate_json(data)
        except ValidationError as exc:
            return SandboxResponse(ok=False, error=f"invalid request: {exc.error_count()} errors")
        if request.op == "ping":
            return SandboxResponse(ok=True)
        try:
            copy = self._copy(request.cwd)
            if not request.argv:
                raise ExecutionError("the request has no command")
            self._hand_over(copy)
            runner = SubprocessRunner(
                timeout_seconds=request.timeout_seconds,
                max_output_bytes=request.max_output_bytes,
                environ={},
                extra_env=_ENV,
                user=self._user,
                after_exit=self._after_run,
            )
            result = runner.run(request.argv, copy)
        except ExecutionError as exc:
            return SandboxResponse(ok=False, error=str(exc))
        except OSError as exc:
            return SandboxResponse(ok=False, error=f"the copy could not be prepared: {exc}")
        logger.info(
            "ran %s in %s: exit %s in %.1fs",
            request.argv[0],
            copy.name,
            result.exit_code,
            result.duration_seconds,
        )
        return SandboxResponse(ok=True, result=result)

    def serve_forever(self) -> None:
        self._copies_dir.mkdir(parents=True, exist_ok=True)
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._socket_path.parent, 0o700)
        self._socket_path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self._socket_path))
            os.chmod(self._socket_path, 0o600)
            server.listen(8)
            logger.info("test sandbox ready at %s", self._socket_path)
            try:
                while True:
                    connection, _ = server.accept()
                    with connection:
                        self._serve(connection)
            finally:
                self._socket_path.unlink(missing_ok=True)

    def _serve(self, connection: socket.socket) -> None:
        connection.settimeout(10)
        data = b""
        try:
            while b"\n" not in data and len(data) <= MAX_REQUEST_BYTES:
                chunk = connection.recv(65_536)
                if not chunk:
                    break
                data += chunk
            connection.settimeout(None)
            response = (
                SandboxResponse(ok=False, error="the request is too large")
                if len(data) > MAX_REQUEST_BYTES
                else self.handle(data.split(b"\n", 1)[0])
            )
            connection.sendall(response.model_dump_json().encode() + b"\n")
        except OSError as exc:
            logger.warning("a request could not be answered: %s", exc)

    def _copy(self, cwd: str) -> Path:
        """The copy to run in: a real folder directly inside the copies folder."""
        path = Path(cwd)
        if path.parent != self._copies_dir or not _COPY_NAME.match(path.name):
            raise ExecutionError(f"{cwd} is not a copy prepared for the sandbox")
        if path.is_symlink() or not path.is_dir():
            raise ExecutionError(f"{cwd} is not a copy prepared for the sandbox")
        return path

    def _hand_over(self, copy: Path) -> None:
        """Give the copy to the sandbox user. Until now only root could read it, so a run
        cannot touch the copies that are waiting for theirs."""
        if self._user is None:
            return
        # Bottom up, and the mode before the owner: once a folder belongs to the sandbox
        # user, this process has no right left to read or change it.
        for directory, dirnames, filenames in os.walk(copy, topdown=False, followlinks=False):
            for name in [*dirnames, *filenames]:
                os.lchown(os.path.join(directory, name), self._user, self._user)
        os.chmod(copy, 0o711)
        os.lchown(copy, self._user, self._user)


def clean_up_after_run(user: int = SANDBOX_USER) -> None:
    """Kill every process the run left behind and empty ``/tmp`` as the sandbox user.

    ``kill(-1)`` reaches every process of the container except this one and init.
    """
    with contextlib.suppress(ProcessLookupError):
        os.kill(-1, signal.SIGKILL)
    subprocess.run(
        ["find", "/tmp", "-mindepth", "1", "-delete"],
        user=user,
        group=user,
        extra_groups=[],
        env=_ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


def _stop(*_: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    signal.signal(signal.SIGTERM, _stop)
    server = SandboxServer(
        Path(os.environ.get("SANDBOX_SOCKET", "/sandbox/run/runner.sock")),
        Path(os.environ.get("SANDBOX_DIR", "/sandbox/copies")),
        after_run=clean_up_after_run,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("test sandbox stopped")


if __name__ == "__main__":
    main()
