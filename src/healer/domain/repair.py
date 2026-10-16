"""Records of the self-correction loop: each attempt and how the whole run ended."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.diagnosis import Diagnosis
from healer.domain.execution import RunReport
from healer.domain.proposal import Proposal, TokenUsage

_MAX_FAILURES = 3
_OUTPUT_TAIL_CHARS = 2_000


class AttemptOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    REJECTED = "rejected"
    REPEATED = "repeated"


class AttemptSource(StrEnum):
    CACHE = "cache"
    MODEL = "model"


class Attempt(BaseModel):
    """One proposed patch and what happened when it was tried on a copy."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    proposal: Proposal
    outcome: AttemptOutcome
    source: AttemptSource = AttemptSource.MODEL
    saved_tokens: int = Field(
        default=0, ge=0, description="For a reused solution, the tokens it cost originally."
    )
    report: RunReport | None = Field(
        default=None, description="The test run with the patch; None when it was not run."
    )
    problem: str = Field(default="", description="Why the patch was rejected or not run.")

    def feedback(self) -> str:
        """What the model is told about this attempt before it tries again."""
        if self.outcome is AttemptOutcome.REJECTED:
            return f"The patch could not be applied: {self.problem}"
        if self.outcome is AttemptOutcome.REPEATED:
            return f"{self.problem}. It was not tested again."
        if self.report is None or self.report.result.passed:
            return "The tests pass with this patch."
        result = self.report.result
        if result.timed_out:
            return "The tests timed out after applying the patch."
        if not self.report.diagnoses:
            tail = result.output[-_OUTPUT_TAIL_CHARS:].strip()
            return f"The tests still fail after applying the patch. End of the output:\n{tail}"
        lines = ["The tests still fail after applying the patch:"]
        for diagnosis in self.report.diagnoses[:_MAX_FAILURES]:
            lines.append(f"- {diagnosis.error.summary}")
            origin = diagnosis.origin
            if origin is not None:
                where = f" in {origin.function}" if origin.function else ""
                lines.append(f"  at {origin.workspace_file}:{origin.line}{where}")
                if origin.code:
                    lines.append(f"  failing line: {origin.code}")
        hidden = len(self.report.diagnoses) - _MAX_FAILURES
        if hidden > 0:
            lines.append(f"- and {hidden} more")
        return "\n".join(lines)


class RepairStatus(StrEnum):
    FIXED = "fixed"
    PASSING = "passing"
    UNDIAGNOSED = "undiagnosed"
    OUT_OF_ATTEMPTS = "out_of_attempts"
    OUT_OF_BUDGET = "out_of_budget"
    STOPPED = "stopped"


class RepairResult(BaseModel):
    """How a run of the self-correction loop ended, with every attempt it made."""

    model_config = ConfigDict(frozen=True)

    status: RepairStatus
    detail: str = ""
    baseline: RunReport = Field(description="The run without any patch, before the loop.")
    target: Diagnosis | None = Field(default=None, description="The error the loop tried to fix.")
    attempts: tuple[Attempt, ...] = ()
    stored: bool = Field(default=False, description="The fix was kept for reuse.")

    @property
    def fix(self) -> Attempt | None:
        return next((a for a in self.attempts if a.outcome is AttemptOutcome.PASSED), None)

    @property
    def model_attempts(self) -> int:
        return sum(1 for a in self.attempts if a.source is AttemptSource.MODEL)

    @property
    def tokens_saved(self) -> int:
        """Tokens avoided by reusing a solution instead of asking the model again."""
        fix = self.fix
        return fix.saved_tokens if fix is not None else 0

    @property
    def usage(self) -> TokenUsage:
        usages = [a.proposal.usage for a in self.attempts]
        return TokenUsage(
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            cache_read_tokens=sum(u.cache_read_tokens for u in usages),
            cache_write_tokens=sum(u.cache_write_tokens for u in usages),
        )
