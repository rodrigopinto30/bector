"""Data contracts and rules for running test commands in an isolated copy."""

import re
import shlex
from collections.abc import Iterable
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import CommandNotAllowedError
from healer.domain.patch import AppliedPatch

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class RunResult(BaseModel):
    """Outcome of one command execution."""

    model_config = ConfigDict(frozen=True)

    command: tuple[str, ...]
    exit_code: int | None = Field(description="None when the command was killed by the timeout.")
    output: str = Field(description="Combined stdout and stderr; only the tail when truncated.")
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False
    output_truncated: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class RunReport(BaseModel):
    """A test run in a sandbox, the patch applied first (if any) and what failed."""

    model_config = ConfigDict(frozen=True)

    result: RunResult
    files_copied: int = Field(ge=0)
    diagnoses: tuple[Diagnosis, ...] = ()
    patch: AppliedPatch | None = Field(
        default=None, description="The patch applied to the copy before running, if any."
    )


class CommandPolicy:
    """Allowlist of test commands. Arguments may not point outside the sandbox."""

    def __init__(self, allowed: Iterable[str]) -> None:
        self._allowed = [tuple(shlex.split(command)) for command in allowed]

    def parse(self, command: str) -> tuple[str, ...]:
        """Split ``command`` into argv, or raise ``CommandNotAllowedError``."""
        try:
            argv = tuple(shlex.split(command))
        except ValueError as exc:
            raise CommandNotAllowedError(f"Malformed command: {command!r}") from exc
        prefix = next((p for p in self._allowed if p and argv[: len(p)] == p), None)
        if prefix is None:
            allowed = ", ".join(" ".join(p) for p in self._allowed)
            raise CommandNotAllowedError(f"Command not allowed: {command!r}. Allowed: {allowed}")
        for argument in argv[len(prefix) :]:
            if _escapes(argument):
                raise CommandNotAllowedError(f"Argument {argument!r} points outside the sandbox")
        return argv


def _escapes(argument: str) -> bool:
    if "\0" in argument:
        return True
    value = argument.split("=", 1)[1] if argument.startswith("-") and "=" in argument else argument
    path = PurePosixPath(value.replace("\\", "/"))
    return (
        path.is_absolute()
        or bool(_WINDOWS_DRIVE.match(value))
        or value.startswith("~")
        or ".." in path.parts
    )
