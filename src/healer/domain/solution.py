"""Verified solutions kept so the same error can be fixed again without the model."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.patch import Patch
from healer.domain.proposal import Proposal, TokenUsage


class Solution(BaseModel):
    """A patch that made the tests pass, keyed by the signature of the error it fixed.

    It carries the evidence required to share it later (6.2): the command, its exit code
    before the patch, and that it passed after it.
    """

    model_config = ConfigDict(frozen=True)

    signature: str = Field(min_length=1)
    error: str = Field(description="Summary of the error, for people and semantic search.")
    patch: Patch
    explanation: str
    model: str = Field(description="The model that wrote the patch.")
    tokens_spent: int = Field(ge=0, description="Tokens the model used to find it.")
    command: str
    exit_code_before: int | None = Field(
        description="Exit code of the command without the patch; None if it timed out."
    )
    verified_at: datetime

    def as_proposal(self) -> Proposal:
        """The solution as a free proposal, so it can be tried like any other."""
        return Proposal(
            patch=self.patch,
            explanation=self.explanation,
            model=self.model,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )
