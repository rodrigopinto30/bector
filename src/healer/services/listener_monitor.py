"""Keeps the record of ``healer listen`` that the panel reads."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from healer.domain.errors import HealerError
from healer.domain.listener import (
    EVENT_TEXT_CHARS,
    HEARTBEAT_SECONDS,
    RECENT_EVENTS,
    ListenerEvent,
    ListenerStatus,
)
from healer.domain.ports import ListenerStatusStore

Outcome = Literal["received", "ignored", "rejected", "result"]


class ListenerMonitor:
    """Saves the connection and the counts on every change, and a heartbeat every
    ``interval`` seconds, so a listener that died is told apart from one with nothing to
    receive. A record that cannot be saved never stops the listener: ``warn`` is told once,
    and again after it could be saved."""

    def __init__(
        self,
        store: ListenerStatusStore,
        *,
        node: str,
        topic: str,
        warn: Callable[[str], None],
        interval: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._warn = warn
        self._interval = interval
        self._clock = clock
        now = clock()
        self._status = ListenerStatus(
            node=node,
            topic=topic,
            connection="starting",
            connected=False,
            started_at=now,
            changed_at=now,
            updated_at=now,
        )
        self._lock = threading.Lock()
        self._failing = False
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="healer-heartbeat", daemon=True)

    def start(self) -> None:
        self._save()
        self._thread.start()

    def connection(self, line: str) -> None:
        now = self._clock()
        with self._lock:
            self._status = self._status.model_copy(
                update={
                    "connection": line,
                    "connected": line.startswith("connected"),
                    "changed_at": now,
                    "recent": self._with(
                        ListenerEvent(at=now, kind="connection", text=line[:EVENT_TEXT_CHARS])
                    ),
                }
            )
        self._save()

    def count(self, outcome: Outcome, text: str = "") -> None:
        """Count a message, and keep what it was among the recent events. A result counted
        is a message received, kept as an event of its own kind."""
        event = ListenerEvent(at=self._clock(), kind=outcome, text=text[:EVENT_TEXT_CHARS])
        field = "received" if outcome == "result" else outcome
        with self._lock:
            current: int = getattr(self._status, field)
            self._status = self._status.model_copy(
                update={field: current + 1, "recent": self._with(event)}
            )
        self._save()

    def _with(self, event: ListenerEvent) -> list[ListenerEvent]:
        return [event, *self._status.recent][:RECENT_EVENTS]

    def stop(self) -> None:
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        event = ListenerEvent(at=self._clock(), kind="connection", text="stopped")
        with self._lock:
            self._status = self._status.model_copy(
                update={"stopped": True, "recent": self._with(event)}
            )
        self._save()

    @property
    def status(self) -> ListenerStatus:
        with self._lock:
            return self._status

    def _update(self, **changes: object) -> None:
        with self._lock:
            self._status = self._status.model_copy(update=changes)
        self._save()

    def _beat(self) -> None:
        while not self._stopping.wait(self._interval):
            self._save()

    def _save(self) -> None:
        with self._lock:
            self._status = self._status.model_copy(update={"updated_at": self._clock()})
            status = self._status
            try:
                self._store.save(status)
            except HealerError as exc:
                if not self._failing:
                    self._failing = True
                    self._warn(f"the panel will not see this listener: {exc}")
                return
            if self._failing:
                self._failing = False
                self._warn("the listener status is saved again; the panel sees it")
