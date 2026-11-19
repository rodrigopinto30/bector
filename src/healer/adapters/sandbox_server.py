"""The test sandbox: runs test commands for the cli container, away from its secrets (7.5).

It runs in its own container, with no network, no ``.env``, no workspace or data volumes,
a read-only root filesystem and only the capabilities it needs. The only thing it shares
with the cli container is the volume with the copies, and the socket it listens on, which
only root and the cli container's group can use. Each command runs as an unprivileged user
that owns nothing else; when it ends, every process it left behind is killed, ``/tmp`` is
emptied and the copy goes back to the cli container's user, so one run cannot affect the
next.

It answers one request at a time, so a run whose client left must not go on: while a command
runs, the connection is watched, and when the client closes it the command and its children are
killed and the copy, which nobody else would delete, is deleted. Whoever is waiting behind it
is served at once. A copy that nobody used for longer than any run can last, such as one whose
client died before it sent its request, is deleted after each request and when the sandbox
starts.

Run it with ``python -m healer.adapters.sandbox_server``.
"""

import contextlib
import logging
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from healer.adapters.sandbox_protocol import (
    MAX_REQUEST_BYTES,
    MAX_TIMEOUT_SECONDS,
    SandboxRequest,
    SandboxResponse,
)
from healer.adapters.subprocess_runner import SubprocessRunner
from healer.domain.errors import ExecutionError, RunAbandonedError

logger = logging.getLogger(__name__)

SANDBOX_USER = 10001
# A run lasts at most MAX_TIMEOUT_SECONDS, so a copy untouched for twice that is not in use.
STALE_COPY_SECONDS = 2 * MAX_TIMEOUT_SECONDS
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
        client: tuple[int, int] | None = None,
        after_run: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """``client`` is the uid and gid of the cli container, which owns each copy before
        and after its run. Without it, only root can reach the socket."""
        self._socket_path = socket_path
        self._copies_dir = copies_dir
        self._user = user
        self._client = client
        self._after_run = after_run
        self._clock = clock

    def handle(self, data: bytes, *, gone: Callable[[], bool] | None = None) -> SandboxResponse:
        """Answer one raw request. Nothing it receives is trusted.

        ``gone`` says whether the client has left. If it has, before the command starts or
        while it runs, the command is not run or is killed, and the copy is deleted.
        """
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
            if gone is not None and gone():
                self._discard(copy)
                raise RunAbandonedError("the client left before its run started")
            runner = SubprocessRunner(
                timeout_seconds=request.timeout_seconds,
                max_output_bytes=request.max_output_bytes,
                environ={},
                extra_env=_ENV,
                user=self._user,
                after_exit=self._after_run,
                abandoned=gone,
            )
            abandoned = False
            try:
                self._hand_over(copy)
                result = runner.run(request.argv, copy)
            except RunAbandonedError:
                abandoned = True
                raise
            finally:
                self._hand_back(copy)
                if abandoned:
                    self._discard(copy)
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
        # The cli container's group may create copies and use the socket. The sandbox user
        # may only pass through the copies folder to reach the copy it was handed.
        self._copies_dir.mkdir(parents=True, exist_ok=True)
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._share(self._copies_dir, 0o771, 0o700)
        self._share(self._socket_path.parent, 0o750, 0o700)
        self._socket_path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self._socket_path))
            self._share(self._socket_path, 0o660, 0o600)
            server.listen(8)
            logger.info("test sandbox ready at %s", self._socket_path)
            try:
                self._sweep()
                while True:
                    connection, _ = server.accept()
                    with connection:
                        self._serve(connection)
                    self._sweep()
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
            gone = _peer_gone(connection)
            response = (
                SandboxResponse(ok=False, error="the request is too large")
                if len(data) > MAX_REQUEST_BYTES
                else self.handle(data.split(b"\n", 1)[0], gone=gone)
            )
            if gone():
                logger.info("the client left, so there is no one to answer")
                return
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

    def _share(self, path: Path, shared: int, private: int) -> None:
        if self._client is None:
            os.chmod(path, private)
            return
        os.chown(path, 0, self._client[1])
        os.chmod(path, shared)

    def _discard(self, copy: Path) -> None:
        """Delete the copy of a run whose client left, which nobody else would delete. It is
        deleted as the cli container's user, who owns it again by now: this process has no
        right to delete what another user owns. A failure is told and changes nothing else."""
        try:
            if self._client is None:
                _delete_tree(copy)
                return
            uid, gid = self._client
            for argv in (["chmod", "-R", "u+rwX", "--", str(copy)], ["rm", "-rf", "--", str(copy)]):
                done = subprocess.run(
                    argv,
                    user=uid,
                    group=gid,
                    extra_groups=[],
                    env=_ENV,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=60,
                    check=False,
                )
                if done.returncode != 0:
                    reason = done.stderr.decode(errors="replace").strip()[:200]
                    logger.warning("%s of the copy %s failed: %s", argv[0], copy.name, reason)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("the copy %s of a stopped run was not deleted: %s", copy.name, exc)

    def _sweep(self) -> None:
        """Delete the copies that nobody used for longer than any run can last. They are what a
        client left that died before its request reached this server, or that a failure here
        left behind: without this they stay in memory until the sandbox is removed."""
        try:
            entries = list(self._copies_dir.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if not _COPY_NAME.match(entry.name) or entry.is_symlink() or not entry.is_dir():
                    continue
                if self._clock() - entry.stat().st_mtime < STALE_COPY_SECONDS:
                    continue
                logger.info("deleting %s, which nobody used for a long time", entry.name)
                self._hand_back(entry)
                self._discard(entry)
            except OSError as exc:
                logger.warning("the copy %s could not be checked: %s", entry.name, exc)

    def _hand_over(self, copy: Path) -> None:
        """Give the copy to the sandbox user. Until now only its owner could read it, so a
        run cannot touch the copies that are waiting for theirs."""
        if self._user is None:
            return
        self._give(copy, self._user, self._user, 0o711)

    def _hand_back(self, copy: Path) -> None:
        """Return the copy to the cli container's user, so it can delete it."""
        if self._user is None or self._client is None:
            return
        self._give(copy, *self._client, 0o700)

    @staticmethod
    def _give(copy: Path, uid: int, gid: int, mode: int) -> None:
        # Bottom up, never following links, and the mode of the copy before its owner,
        # while this process is still allowed to change it.
        for directory, dirnames, filenames in os.walk(copy, topdown=False, followlinks=False):
            for name in [*dirnames, *filenames]:
                os.lchown(os.path.join(directory, name), uid, gid)
        os.chmod(copy, mode)
        os.lchown(copy, uid, gid)


def _peer_gone(connection: socket.socket) -> Callable[[], bool]:
    """A function that says whether the other end of a connection was closed.

    The client sends its request and waits, and may close only its writing half while it
    waits, which is not leaving. On a Unix socket the hang-up is reported only when the other
    end was closed altogether, and asking does not read anything.
    """
    poller = select.poll()
    poller.register(connection, select.POLLHUP)

    def gone() -> bool:
        return any(events & (select.POLLHUP | select.POLLERR) for _, events in poller.poll(0))

    return gone


def _delete_tree(path: Path) -> None:
    """Delete a folder, also when a run left folders in it that cannot be entered or written."""
    for directory, _, _ in os.walk(path):
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o700)
    shutil.rmtree(path, ignore_errors=True)


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
    uid, gid = os.environ.get("SANDBOX_CLIENT_UID"), os.environ.get("SANDBOX_CLIENT_GID")
    server = SandboxServer(
        Path(os.environ.get("SANDBOX_SOCKET", "/sandbox/run/runner.sock")),
        Path(os.environ.get("SANDBOX_DIR", "/sandbox/copies")),
        client=(int(uid), int(gid)) if uid and gid else None,
        after_run=clean_up_after_run,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("test sandbox stopped")


if __name__ == "__main__":
    main()
