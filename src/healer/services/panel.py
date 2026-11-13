"""Builds the picture of this node that the panel shows. It only reads."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from healer import __version__
from healer.domain.errors import HealerError
from healer.domain.history import run_id
from healer.domain.ledger import LedgerEntry
from healer.domain.listener import ListenerStatus
from healer.domain.panel import (
    INCIDENTS_SHOWN,
    Check,
    CheckState,
    IncidentView,
    PanelSnapshot,
    ProjectView,
    RunView,
    Savings,
    TeamFixView,
    WaitingFixView,
    activity,
    incident_view,
    listener_check,
    project_views,
    run_view,
    savings,
    team_fix_views,
    waiting_fix_view,
)
from healer.domain.ports import (
    Checkable,
    Ledger,
    ListenerStatusStore,
    ProjectStore,
    ResultStore,
    RunHistory,
    ShareOutbox,
)
from healer.domain.sharing import PendingResult
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
        results: ResultStore,
        history: RunHistory,
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
        self._results = results
        self._history = history
        self._listener = listener
        self._checks = checks
        self._clock = clock

    def snapshot(self) -> PanelSnapshot:
        now = self._clock()
        problems: dict[str, str] = {}

        entries: list[LedgerEntry] = self._read("savings", problems, self._ledger.entries, [])
        recent = sorted(entries, key=lambda e: e.recorded_at, reverse=True)[:INCIDENTS_SHOWN]
        kept_runs: set[str] = self._read(
            "history",
            problems,
            lambda: self._history.known(run_id(e.project, e.recorded_at) for e in recent),
            set(),
        )
        incidents: list[IncidentView] = [incident_view(e, kept_runs=kept_runs) for e in recent]
        listener: ListenerStatus | None = self._read(
            "listener", problems, self._listener.load, None
        )
        waiting: list[WaitingFixView] = self._read("outbox", problems, self._waiting, [])
        results: list[PendingResult] = self._read(
            "outbox", problems, self._outbox.pending_results, []
        )
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
        checks.append(self._outbox_check(waiting, results, problems.get("outbox")))

        summary: Savings = savings(entries, today=now.astimezone(UTC).date())
        return PanelSnapshot(
            taken_at=now,
            version=__version__,
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

    def run(self, identifier: str) -> RunView | None:
        """One run, step by step, or None if the history does not keep it. A history that
        cannot be read raises ``HistoryError``."""
        record = self._history.get(identifier)
        return run_view(record) if record is not None else None

    def _waiting(self) -> list[WaitingFixView]:
        return [waiting_fix_view(p, topic=self._topic) for p in self._outbox.pending()]

    def _team(self) -> list[TeamFixView]:
        suggestions = self._projects.team()
        tallies = self._results.tally(r.solution.fix_id for r in suggestions)
        return team_fix_views(suggestions, tallies)

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
    def _outbox_check(
        waiting: list[WaitingFixView], results: list[PendingResult], problem: str | None
    ) -> Check:
        name = "Outbox"
        if problem is not None:
            return Check(key="outbox", name=name, state=CheckState.FAIL, detail=problem)
        if not waiting and not results:
            return Check(
                key="outbox",
                name=name,
                state=CheckState.OK,
                detail="empty: every shared fix and result reached the broker",
            )
        parts = [
            f"{count} {noun if count == 1 else plural}"
            for count, noun, plural in (
                (len(waiting), "fix", "fixes"),
                (len(results), "result", "results"),
            )
            if count
        ]
        verb = "is" if len(waiting) + len(results) == 1 else "are"
        return Check(
            key="outbox",
            name=name,
            state=CheckState.WARNING,
            detail=f"{' and '.join(parts)} {verb} waiting to be sent",
            since=min([w.queued_at for w in waiting] + [r.queued_at for r in results]),
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
