"""What the cli container and the test sandbox say to each other: one JSON line each way."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.execution import RunResult

MAX_REQUEST_BYTES = 64_000
MAX_TIMEOUT_SECONDS = 3_600.0
MAX_OUTPUT_BYTES = 10_000_000


class SandboxRequest(BaseModel):
    """Run ``argv`` in the copy at ``cwd``, or only check that the sandbox answers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: Literal["run", "ping"] = "run"
    argv: tuple[str, ...] = Field(default=(), max_length=64)
    cwd: str = ""
    timeout_seconds: float = Field(default=300.0, gt=0, le=MAX_TIMEOUT_SECONDS)
    max_output_bytes: int = Field(default=1_000_000, gt=0, le=MAX_OUTPUT_BYTES)


class SandboxResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    error: str = ""
    result: RunResult | None = None
