"""Data contracts for incident diagnosis."""

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.models import Symbol


class StackFrame(BaseModel):
    """One entry of a stack trace, outermost first."""

    model_config = ConfigDict(frozen=True)

    file: str = Field(description="Path exactly as printed in the trace.")
    line: int = Field(ge=0)
    function: str = ""
    code: str = ""
    workspace_file: str | None = Field(
        default=None, description="Path relative to the workspace when the file belongs to it."
    )


class TracedError(BaseModel):
    """An exception recovered from raw output, before it is related to the workspace."""

    model_config = ConfigDict(frozen=True)

    error_type: str
    message: str = ""
    frames: tuple[StackFrame, ...] = ()
    causes: tuple[str, ...] = Field(
        default=(), description="Earlier exceptions of the same chain, oldest first."
    )

    @property
    def summary(self) -> str:
        return f"{self.error_type}: {self.message}" if self.message else self.error_type


class Diagnosis(BaseModel):
    """Structured error specification handed to the rest of the pipeline."""

    model_config = ConfigDict(frozen=True)

    error: TracedError
    origin: StackFrame | None = Field(
        description="Innermost frame inside the workspace: where the fix most likely goes."
    )
    location: Symbol | None = Field(
        default=None, description="Indexed symbol that encloses the origin line."
    )
    signature: str = Field(description="Stable hash of the error, independent of the machine.")
    signature_basis: str
    occurrences: int = Field(default=1, ge=1)
