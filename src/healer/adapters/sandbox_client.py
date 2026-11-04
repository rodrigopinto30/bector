"""``CommandRunner`` that hands each test run to the test sandbox container."""

import socket
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from healer.adapters.sandbox_protocol import MAX_REQUEST_BYTES, SandboxRequest, SandboxResponse
from healer.domain.errors import ExecutionError
from healer.domain.execution import RunResult

_ANSWER_MARGIN_SECONDS = 30.0


class SandboxServiceRunner:
    """Sends the command and the copy's path over the sandbox's socket and waits for the
    result. The sandbox enforces the time and output limits; this side only waits a little
    longer than the time limit before it gives up."""

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> None:
        self._socket_path = socket_path
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes

    def run(self, argv: Sequence[str], cwd: Path) -> RunResult:
        response = self._ask(
            SandboxRequest(
                argv=tuple(argv),
                cwd=str(cwd),
                timeout_seconds=self._timeout,
                max_output_bytes=self._max_output,
            ),
            wait=self._timeout + _ANSWER_MARGIN_SECONDS,
        )
        if response.result is None:
            raise ExecutionError("the test sandbox gave no result")
        return response.result

    def check(self) -> None:
        """Raise ``ExecutionError`` unless the sandbox answers."""
        self._ask(SandboxRequest(op="ping"), wait=5)

    def _ask(self, request: SandboxRequest, *, wait: float) -> SandboxResponse:
        message = request.model_dump_json().encode() + b"\n"
        if len(message) > MAX_REQUEST_BYTES:
            raise ExecutionError("the command is too long for the test sandbox")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(wait)
            try:
                connection.connect(str(self._socket_path))
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                raise ExecutionError(
                    f"the test sandbox is not running (no answer at {self._socket_path}); "
                    "start it with ./start.sh"
                ) from exc
            except OSError as exc:
                raise ExecutionError(f"cannot reach the test sandbox: {exc}") from exc
            try:
                connection.sendall(message)
                data = _read_all(connection, limit=2 * self._max_output + 1_000_000)
            except TimeoutError as exc:
                raise ExecutionError("the test sandbox did not answer in time") from exc
            except OSError as exc:
                raise ExecutionError(f"the test sandbox stopped answering: {exc}") from exc
        try:
            response = SandboxResponse.model_validate_json(data)
        except ValidationError as exc:
            raise ExecutionError("the test sandbox sent an invalid answer") from exc
        if not response.ok:
            raise ExecutionError(f"the test sandbox refused the run: {response.error}")
        return response


def _read_all(connection: socket.socket, *, limit: int) -> bytes:
    data = b""
    while chunk := connection.recv(65_536):
        data += chunk
        if len(data) > limit:
            raise ExecutionError("the test sandbox sent too much data")
    return data
