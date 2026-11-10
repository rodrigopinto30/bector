"""Builds the picture of this node that the panel shows. It only reads."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from healer.domain.errors import HealerError
from healer.domain.ledger import LedgerEntry
from healer.domain.listener import ListenerStatus
from healer.domain.panel import (
    INCIDENTS_SHOWN,
    Check,
    CheckState,
    IncidentView,
    PanelSnapshot,
    ProjectView,
    Savings,
    TeamFixView,
    WaitingFixView,
    activity,
    incident_view,
    listener_check,
    project_views,
    savings,
    team_fix_views,
    waiting_fix_view,
)
from healer.domain.ports import (
    Checkable,
    Ledger,
    ListenerStatusStore,
    ProjectStore,
    ShareOutbox,
)
from healer.domain.solution import Solution

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class Probe:
    """A check that asks a service each time: ``ok`` when it answers, ``off`` when there is
    no ``target`` to ask."""

    key: str
    name: str
    target: Checkable | None
    ok: str
    off: str = "off"


class PanelReader:
    """Each section is read on its own. One that fails is left empty and its error is kept
    in ``problems``, so a corrupt file or a missing service never hides the others."""

    def __init__(
        self,
        *,
        node: str,
        topic: str,
        sharing: bool,
        ledger: Ledger,
        outbox: ShareOutbox,
        projects: ProjectStore,
        listener: ListenerStatusStore,
        checks: Sequence[Check | Probe] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._node = node
        self._topic = topic
        self._sharing = sharing
        self._ledger = ledger
        self._outbox = outbox
        self._projects = projects
        self._listener = listener
        self._checks = checks
        self._clock = clock

    def snapshot(self) -> PanelSnapshot:
        now = self._clock()
        problems: dict[str, str] = {}

        entries: list[LedgerEntry] = self._read("savings", problems, self._ledger.entries, [])
        recent = sorted(entries, key=lambda e: e.recorded_at, reverse=True)[:INCIDENTS_SHOWN]
        incidents: list[IncidentView] = [incident_view(e) for e in recent]
        listener: ListenerStatus | None = self._read(
            "listener", problems, self._listener.load, None
        )
        waiting: list[WaitingFixView] = self._read("outbox", problems, self._waiting, [])
        team: list[TeamFixView] = self._read("team", problems, self._team, [])
        projects: list[ProjectView] = self._read(
            "projects", problems, lambda: self._project_views(entries), []
        )

        checks = [self._check(item) for item in self._checks]
        if "listener" in problems:
            checks.append(
                Check(
                    key="listener",
                    name="Team listener",
                    state=CheckState.FAIL,
                    detail=problems["listener"],
                )
            )
        else:
            checks.append(listener_check(listener, now=now))
        checks.append(self._outbox_check(waiting, problems.get("outbox")))

        summary: Savings = savings(entries, today=now.astimezone(UTC).date())
        return PanelSnapshot(
            taken_at=now,
            node=self._node,
            topic=self._topic,
            sharing=self._sharing,
            checks=checks,
            listener=listener,
            savings=summary,
            incidents=incidents,
            projects=projects,
            team=team,
            outbox=waiting,
            activity=activity(incidents=incidents, team=team, outbox=waiting, listener=listener),
            problems=problems,
        )

    def _waiting(self) -> list[WaitingFixView]:
        return [waiting_fix_view(p, topic=self._topic) for p in self._outbox.pending()]

    def _team(self) -> list[TeamFixView]:
        return team_fix_views(self._projects.team())

    def _project_views(self, entries: list[LedgerEntry]) -> list[ProjectView]:
        paths = sorted({entry.project for entry in entries})
        symbols: dict[str, int | None] = {path: self._projects.symbols(path) for path in paths}
        fixes: dict[str, list[Solution]] = {path: self._projects.fixes(path) for path in paths}
        return project_views(entries, symbols=symbols, fixes=fixes)

    def _check(self, item: Check | Probe) -> Check:
        if isinstance(item, Check):
            return item
        if item.target is None:
            return Check(key=item.key, name=item.name, state=CheckState.OFF, detail=item.off)
        try:
            item.target.check()
        except HealerError as exc:
            return Check(key=item.key, name=item.name, state=CheckState.FAIL, detail=str(exc))
        return Check(key=item.key, name=item.name, state=CheckState.OK, detail=item.ok)

    @staticmethod
    def _outbox_check(waiting: list[WaitingFixView], problem: str | None) -> Check:
        name = "Outbox"
        if problem is not None:
            return Check(key="outbox", name=name, state=CheckState.FAIL, detail=problem)
        if not waiting:
            return Check(
                key="outbox",
                name=name,
                state=CheckState.OK,
                detail="empty: every shared fix reached the broker",
            )
        count = len(waiting)
        noun = "fix is" if count == 1 else "fixes are"
        return Check(
            key="outbox",
            name=name,
            state=CheckState.WARNING,
            detail=f"{count} {noun} waiting to be sent",
            since=min(w.queued_at for w in waiting),
        )

    @staticmethod
    def _read(section: str, problems: dict[str, str], read: Callable[[], T], empty: T) -> T:
        """Any error leaves the section empty and is shown, never hidden: an unexpected one,
        such as a database error from Chroma, is also logged with its traceback."""
        try:
            return read()
        except HealerError as exc:
            problems[section] = str(exc)
        except Exception as exc:
            logger.exception("The panel could not read %s", section)
            problems[section] = f"{type(exc).__name__}: {exc}"
        return empty
