"""What the local panel shows: a read-only picture of this node, ready to be drawn."""

import difflib
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from healer.domain.ledger import LedgerEntry, LedgerSummary, Resolution, total
from healer.domain.listener import STALE_AFTER, ListenerStatus
from healer.domain.patch import FileEdit, Patch
from healer.domain.repair import RepairStatus
from healer.domain.sharing import PendingShare, ReceivedSolution
from healer.domain.solution import Solution

DAYS_SHOWN = 30
INCIDENTS_SHOWN = 50
ACTIVITY_SHOWN = 12
_CONTEXT_LINES = 3


class CheckState(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAIL = "fail"
    OFF = "off"


class Check(BaseModel):
    """One line of the health of this node, like those of ``healer doctor``."""

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    state: CheckState
    detail: str
    since: datetime | None = Field(default=None, description="When the state began, if known.")


class DailyTokens(BaseModel):
    """One day (UTC): what the incidents cost and what reuse avoided."""

    model_config = ConfigDict(frozen=True)

    day: date
    incidents: int = 0
    spent: int = Field(default=0, description="Tokens the model used.")
    saved_by_cache: int = 0
    saved_by_team: int = 0


class ResolutionCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    cache: int = 0
    team: int = 0
    model: int = 0
    none: int = 0


class Savings(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: LedgerSummary
    resolutions: ResolutionCounts
    days: list[DailyTokens]


class IncidentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    recorded_at: datetime
    project: str
    signature: str
    error_type: str
    status: RepairStatus
    resolution: Resolution
    model_attempts: int
    tokens_used: int
    tokens_saved: int


class DiffLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["context", "added", "removed", "gap"]
    text: str


class EditView(BaseModel):
    """One edit of a patch, as the lines it removes and adds."""

    model_config = ConfigDict(frozen=True)

    path: str
    created: bool = Field(description="The edit creates the file.")
    lines: list[DiffLine]


class FixView(BaseModel):
    """A fix this node verified and keeps for its project."""

    model_config = ConfigDict(frozen=True)

    signature: str
    error: str
    explanation: str
    model: str
    tokens_spent: int
    command: str
    exit_code_before: int | None
    verified_at: datetime
    edits: list[EditView]


class ProjectView(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    symbols: int | None = Field(description="Symbols in its code index; None if never indexed.")
    incidents: int
    fixed: int
    last_incident: datetime | None
    fixes: list[FixView]


class TeamFixView(BaseModel):
    """A suggestion received from another node."""

    model_config = ConfigDict(frozen=True)

    fix_id: str = Field(description="One per fix; an error may have several (ADR 0021).")
    fixes_for_error: int = Field(ge=1, description="Suggestions kept for the same error.")
    signature: str
    node: str
    error: str
    explanation: str
    model: str
    tokens_spent: int
    published_at: datetime
    received_at: datetime
    command: str
    exit_code_before: int | None
    edits: list[EditView]


class WaitingFixView(BaseModel):
    """A fix the broker did not take, waiting in the outbox."""

    model_config = ConfigDict(frozen=True)

    signature: str
    error: str
    topic: str
    for_current_topic: bool
    published_at: datetime
    queued_at: datetime
    attempts: int
    last_error: str
    edits: list[EditView]


class ActivityItem(BaseModel):
    """One thing that happened, for the list of recent activity."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    kind: Literal["incident", "suggestion", "waiting", "connection", "ignored", "rejected"]
    state: CheckState
    title: str
    detail: str


class PanelSnapshot(BaseModel):
    """Everything the panel shows at one moment. A section that could not be read is empty
    and its key in ``problems`` says why, so one broken file never hides the rest."""

    model_config = ConfigDict(frozen=True)

    taken_at: datetime
    node: str
    topic: str
    sharing: bool
    checks: list[Check]
    listener: ListenerStatus | None
    savings: Savings
    incidents: list[IncidentView]
    projects: list[ProjectView]
    team: list[TeamFixView]
    outbox: list[WaitingFixView]
    activity: list[ActivityItem] = Field(default_factory=list)
    problems: dict[str, str] = Field(default_factory=dict)


def savings(entries: Iterable[LedgerEntry], *, today: date, days: int = DAYS_SHOWN) -> Savings:
    items = list(entries)
    counts = {resolution: 0 for resolution in Resolution}
    for entry in items:
        counts[entry.resolution] += 1
    return Savings(
        total=total(items),
        resolutions=ResolutionCounts(
            cache=counts[Resolution.CACHE],
            team=counts[Resolution.TEAM],
            model=counts[Resolution.MODEL],
            none=counts[Resolution.NONE],
        ),
        days=daily_tokens(items, today=today, days=days),
    )


def daily_tokens(
    entries: Iterable[LedgerEntry], *, today: date, days: int = DAYS_SHOWN
) -> list[DailyTokens]:
    """One row per day (UTC) of the last ``days`` up to ``today``, empty days included,
    oldest first. Tokens saved count for the level that reused the fix."""
    first = today - timedelta(days=days - 1)
    rows = {
        first + timedelta(days=offset): DailyTokens(day=first + timedelta(days=offset))
        for offset in range(days)
    }
    for entry in entries:
        day = entry.recorded_at.date()
        row = rows.get(day)
        if row is None:
            continue
        rows[day] = row.model_copy(
            update={
                "incidents": row.incidents + 1,
                "spent": row.spent + entry.usage.total,
                "saved_by_cache": row.saved_by_cache
                + (entry.tokens_saved if entry.resolution is Resolution.CACHE else 0),
                "saved_by_team": row.saved_by_team
                + (entry.tokens_saved if entry.resolution is Resolution.TEAM else 0),
            }
        )
    return list(rows.values())


def incident_view(entry: LedgerEntry) -> IncidentView:
    return IncidentView(
        recorded_at=entry.recorded_at,
        project=entry.project,
        signature=entry.signature,
        error_type=entry.error_type,
        status=entry.status,
        resolution=entry.resolution,
        model_attempts=entry.model_attempts,
        tokens_used=entry.usage.total,
        tokens_saved=entry.tokens_saved,
    )


def edit_views(patch: Patch) -> list[EditView]:
    return [edit_view(edit) for edit in patch.edits]


def edit_view(edit: FileEdit) -> EditView:
    """The edit as removed and added lines, with a few unchanged lines around each change.
    A ``gap`` line stands for unchanged lines left out."""
    old, new = edit.old.splitlines(), edit.new.splitlines()
    lines: list[DiffLine] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for group in matcher.get_grouped_opcodes(_CONTEXT_LINES):
        if lines:
            lines.append(DiffLine(kind="gap", text=""))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines.extend(DiffLine(kind="context", text=line) for line in old[i1:i2])
                continue
            lines.extend(DiffLine(kind="removed", text=line) for line in old[i1:i2])
            lines.extend(DiffLine(kind="added", text=line) for line in new[j1:j2])
    return EditView(path=edit.path, created=not edit.old, lines=lines)


def fix_view(solution: Solution) -> FixView:
    return FixView(
        signature=solution.signature,
        error=solution.error,
        explanation=solution.explanation,
        model=solution.model,
        tokens_spent=solution.tokens_spent,
        command=solution.command,
        exit_code_before=solution.exit_code_before,
        verified_at=solution.verified_at,
        edits=edit_views(solution.patch),
    )


def team_fix_views(suggestions: list[ReceivedSolution]) -> list[TeamFixView]:
    """Each suggestion, in the same order, with how many the team gave for its error."""
    counts = Counter(received.solution.signature for received in suggestions)
    return [
        team_fix_view(received, fixes_for_error=counts[received.solution.signature])
        for received in suggestions
    ]


def team_fix_view(received: ReceivedSolution, *, fixes_for_error: int = 1) -> TeamFixView:
    solution = received.solution
    return TeamFixView(
        fix_id=solution.fix_id,
        fixes_for_error=fixes_for_error,
        signature=solution.signature,
        node=solution.node,
        error=solution.error,
        explanation=solution.explanation,
        model=solution.model,
        tokens_spent=solution.tokens_spent,
        published_at=solution.published_at,
        received_at=received.received_at,
        command=solution.evidence.command,
        exit_code_before=solution.evidence.exit_code_before,
        edits=edit_views(solution.patch),
    )


def waiting_fix_view(pending: PendingShare, *, topic: str) -> WaitingFixView:
    solution = pending.solution
    return WaitingFixView(
        signature=solution.signature,
        error=solution.error,
        topic=pending.topic,
        for_current_topic=pending.topic == topic,
        published_at=solution.published_at,
        queued_at=pending.queued_at,
        attempts=pending.attempts,
        last_error=pending.last_error,
        edits=edit_views(solution.patch),
    )


def project_views(
    entries: Iterable[LedgerEntry],
    *,
    symbols: dict[str, int | None],
    fixes: dict[str, list[Solution]],
) -> list[ProjectView]:
    """One row per project with recorded incidents, the most recently active first."""
    by_project: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        by_project.setdefault(entry.project, []).append(entry)
    views = [
        ProjectView(
            path=path,
            symbols=symbols.get(path),
            incidents=len(items),
            fixed=total(items).fixed,
            last_incident=max(e.recorded_at for e in items),
            fixes=[fix_view(s) for s in fixes.get(path, [])],
        )
        for path, items in by_project.items()
    ]
    return sorted(views, key=lambda v: (v.last_incident or datetime.min, v.path), reverse=True)


def listener_check(status: ListenerStatus | None, *, now: datetime) -> Check:
    """Whether the listener runs, judged by its own record and how fresh it is."""
    name = "Team listener"
    if status is None:
        return Check(
            key="listener",
            name=name,
            state=CheckState.OFF,
            detail="not running; start it with: docker compose --profile team up -d listener",
        )
    if status.stopped:
        return Check(
            key="listener",
            name=name,
            state=CheckState.OFF,
            detail="stopped",
            since=status.updated_at,
        )
    if now - status.updated_at > STALE_AFTER:
        return Check(
            key="listener",
            name=name,
            state=CheckState.FAIL,
            detail="not answering: it stopped without saying so; see docker compose logs listener",
            since=status.updated_at,
        )
    state = CheckState.OK if status.connected else CheckState.WARNING
    return Check(
        key="listener",
        name=name,
        state=state,
        detail=status.connection,
        since=status.changed_at,
    )


_SOLVED_BY = {
    Resolution.CACHE: "the local cache",
    Resolution.TEAM: "the team",
    Resolution.MODEL: "Claude",
}
_LISTENER_EVENTS: dict[str, tuple[str, CheckState]] = {
    "ignored": ("Message ignored", CheckState.OFF),
    "rejected": ("Message rejected", CheckState.FAIL),
}


def activity(
    *,
    incidents: Iterable[IncidentView],
    team: Iterable[TeamFixView],
    outbox: Iterable[WaitingFixView],
    listener: ListenerStatus | None,
    limit: int = ACTIVITY_SHOWN,
) -> list[ActivityItem]:
    """What happened lately, newest first: incidents, suggestions received, fixes left
    waiting, and what the listener saw. A suggestion counts once, from the team's list."""
    items = [_incident_item(incident) for incident in incidents]
    items += [
        ActivityItem(
            at=fix.received_at,
            kind="suggestion",
            state=CheckState.OK,
            title=f"Fix received from {fix.node}",
            detail=fix.error,
        )
        for fix in team
    ]
    items += [
        ActivityItem(
            at=fix.queued_at,
            kind="waiting",
            state=CheckState.WARNING,
            title="Fix waiting to be sent",
            detail=f"{fix.error}: {fix.last_error}",
        )
        for fix in outbox
    ]
    for event in listener.recent if listener is not None else []:
        if event.kind == "connection":
            connected = event.text.startswith("connected")
            items.append(
                ActivityItem(
                    at=event.at,
                    kind="connection",
                    state=CheckState.OK if connected else CheckState.WARNING,
                    title="Team listener",
                    detail=event.text,
                )
            )
        elif event.kind in _LISTENER_EVENTS:
            title, state = _LISTENER_EVENTS[event.kind]
            items.append(
                ActivityItem(
                    at=event.at, kind=event.kind, state=state, title=title, detail=event.text
                )
            )
    return sorted(items, key=lambda item: item.at, reverse=True)[:limit]


def _incident_item(incident: IncidentView) -> ActivityItem:
    project = PurePosixPath(incident.project).name or incident.project
    solved_by = _SOLVED_BY.get(incident.resolution)
    if solved_by is None:
        return ActivityItem(
            at=incident.recorded_at,
            kind="incident",
            state=CheckState.FAIL,
            title=f"{incident.error_type} not fixed in {project}",
            detail=(
                f"{incident.status.value.replace('_', ' ')}, {incident.tokens_used:,} tokens spent"
            ),
        )
    cost = (
        f"{incident.tokens_used:,} tokens spent"
        if incident.tokens_used
        else f"0 tokens spent, {incident.tokens_saved:,} saved"
    )
    return ActivityItem(
        at=incident.recorded_at,
        kind="incident",
        state=CheckState.OK,
        title=f"{incident.error_type} fixed in {project}",
        detail=f"by {solved_by}, {cost}",
    )
