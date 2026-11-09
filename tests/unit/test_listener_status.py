import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.json_listener_status import JsonListenerStatus
from healer.domain.errors import StatusError
from healer.domain.listener import EVENT_TEXT_CHARS, RECENT_EVENTS, ListenerStatus
from healer.services.listener_monitor import ListenerMonitor

START = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class MemoryStore:
    def __init__(self) -> None:
        self.saved: list[ListenerStatus] = []
        self.failing = False
        self.saved_again = threading.Event()

    def save(self, status: ListenerStatus) -> None:
        if self.failing:
            raise StatusError("Cannot write the listener status: disk full")
        self.saved.append(status)
        self.saved_again.set()

    def load(self) -> ListenerStatus | None:
        return self.saved[-1] if self.saved else None


def monitor(
    store: MemoryStore, clock: Clock, warnings: list[str], interval: float = 60
) -> ListenerMonitor:
    return ListenerMonitor(
        store,
        node="laptop-luis",
        topic="healer/solutions/v1",
        warn=warnings.append,
        interval=interval,
        clock=clock,
    )


def test_the_record_follows_the_connection_and_counts_what_arrives() -> None:
    store, clock, warnings = MemoryStore(), Clock(), []
    watcher = monitor(store, clock, warnings)
    watcher.start()
    assert store.saved[-1].connection == "starting"
    assert not store.saved[-1].connected
    clock.advance(5)
    watcher.connection("connected to mosquitto:1883")
    watcher.count("received")
    watcher.count("received")
    watcher.count("ignored")
    watcher.count("rejected")
    status = store.saved[-1]
    assert (status.connected, status.changed_at) == (True, START + timedelta(seconds=5))
    assert (status.received, status.ignored, status.rejected) == (2, 1, 1)
    clock.advance(5)
    watcher.connection("connection lost; retrying")
    assert not store.saved[-1].connected
    clock.advance(5)
    watcher.stop()
    final = store.saved[-1]
    assert final.stopped
    assert [(e.kind, e.text) for e in final.recent] == [
        ("connection", "stopped"),
        ("connection", "connection lost; retrying"),
        ("rejected", ""),
        ("ignored", ""),
        ("received", ""),
        ("received", ""),
        ("connection", "connected to mosquitto:1883"),
    ]
    assert final.updated_at == START + timedelta(seconds=15)
    assert final.started_at == START
    assert warnings == []


def test_a_heartbeat_shows_the_listener_is_alive_while_nothing_happens() -> None:
    store, clock, warnings = MemoryStore(), Clock(), []
    watcher = monitor(store, clock, warnings, interval=0.01)
    watcher.start()
    store.saved_again.clear()
    clock.advance(30)
    assert store.saved_again.wait(2)
    watcher.stop()
    assert any(status.updated_at == START + timedelta(seconds=30) for status in store.saved)


def test_a_record_that_cannot_be_saved_never_stops_the_listener() -> None:
    store, clock, warnings = MemoryStore(), Clock(), []
    watcher = monitor(store, clock, warnings)
    store.failing = True
    watcher.start()
    watcher.count("received")
    watcher.connection("connected to mosquitto:1883")
    assert warnings == [
        "the panel will not see this listener: Cannot write the listener status: disk full"
    ]
    store.failing = False
    watcher.count("received")
    assert warnings[-1] == "the listener status is saved again; the panel sees it"
    assert store.saved[-1].received == 2
    watcher.stop()


def a_status() -> ListenerStatus:
    return ListenerStatus(
        node="laptop-luis",
        topic="healer/solutions/v1",
        connection="connected to mosquitto:1883",
        connected=True,
        started_at=START,
        changed_at=START,
        updated_at=START,
        received=1,
    )


def test_the_status_file_is_replaced_whole(tmp_path: Path) -> None:
    store = JsonListenerStatus(tmp_path / "data" / "listener.json")
    assert store.load() is None
    store.save(a_status())
    store.save(a_status().model_copy(update={"received": 2}))
    loaded = store.load()
    assert loaded is not None
    assert loaded.received == 2
    assert [p.name for p in (tmp_path / "data").iterdir()] == ["listener.json"]


def test_a_corrupt_status_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "listener.json"
    path.write_text('{"node": "half')
    with pytest.raises(StatusError, match="is corrupt"):
        JsonListenerStatus(path).load()


def test_a_status_that_cannot_be_written_is_reported(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    with pytest.raises(StatusError, match="Cannot write the listener status"):
        JsonListenerStatus(blocked / "listener.json").save(a_status())
    with pytest.raises(StatusError, match="Cannot read the listener status"):
        JsonListenerStatus(tmp_path).load()


def test_only_the_latest_events_are_kept_and_kept_short() -> None:
    store, clock, warnings = MemoryStore(), Clock(), []
    watcher = monitor(store, clock, warnings)
    for number in range(RECENT_EVENTS + 5):
        clock.advance(1)
        watcher.count("rejected", f"message {number} " + "x" * 400)
    recent = store.saved[-1].recent
    assert len(recent) == RECENT_EVENTS
    assert recent[0].text.startswith(f"message {RECENT_EVENTS + 4} ")
    assert all(len(event.text) == EVENT_TEXT_CHARS for event in recent)
