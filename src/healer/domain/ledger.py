"""Token savings ledger (6.3): what each incident cost and what reuse avoided."""

from collections.abc import Callable, Iterable
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.proposal import TokenUsage
from healer.domain.repair import AttemptSource, RepairResult, RepairStatus

Grouping = Literal["day", "project"]


class Resolution(StrEnum):
    """The level that solved the incident (6.1), or none."""

    CACHE = "cache"
    MODEL = "model"
    NONE = "none"


class LedgerEntry(BaseModel):
    """One incident. Metrics only: never code, logs or error messages."""

    model_config = ConfigDict(frozen=True)

    recorded_at: datetime
    project: str
    signature: str
    error_type: str
    status: RepairStatus
    resolution: Resolution
    model_attempts: int = Field(ge=0)
    usage: TokenUsage
    tokens_saved: int = Field(ge=0)


class LedgerSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    incidents: int
    fixed: int
    from_cache: int
    model_attempts: int
    tokens_used: int
    tokens_saved: int


def ledger_entry(result: RepairResult, *, project: str, at: datetime) -> LedgerEntry | None:
    """The record for a run, or None when nothing was attempted (no incident to count)."""
    if result.target is None or not result.attempts:
        return None
    fix = result.fix
    if fix is None:
        resolution = Resolution.NONE
    elif fix.source is AttemptSource.CACHE:
        resolution = Resolution.CACHE
    else:
        resolution = Resolution.MODEL
    return LedgerEntry(
        recorded_at=at,
        project=project,
        signature=result.target.signature,
        error_type=result.target.error.error_type,
        status=result.status,
        resolution=resolution,
        model_attempts=result.model_attempts,
        usage=result.usage,
        tokens_saved=result.tokens_saved,
    )


def summarize(entries: Iterable[LedgerEntry], by: Grouping) -> list[LedgerSummary]:
    """One row per day (UTC) or project, oldest day or alphabetical project first."""
    key: Callable[[LedgerEntry], str] = (
        (lambda e: e.recorded_at.date().isoformat()) if by == "day" else (lambda e: e.project)
    )
    groups: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        groups.setdefault(key(entry), []).append(entry)
    return [total(groups[name], key=name) for name in sorted(groups)]


def total(entries: Iterable[LedgerEntry], *, key: str = "total") -> LedgerSummary:
    items = list(entries)
    return LedgerSummary(
        key=key,
        incidents=len(items),
        fixed=sum(1 for e in items if e.resolution is not Resolution.NONE),
        from_cache=sum(1 for e in items if e.resolution is Resolution.CACHE),
        model_attempts=sum(e.model_attempts for e in items),
        tokens_used=sum(e.usage.total for e in items),
        tokens_saved=sum(e.tokens_saved for e in items),
    )
