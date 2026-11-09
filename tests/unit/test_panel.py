import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from healer.domain.ledger import LedgerEntry, Resolution
from healer.domain.listener import STALE_AFTER, ListenerEvent, ListenerStatus
from healer.domain.panel import (
    CheckState,
    DiffLine,
    PanelSnapshot,
    activity,
    daily_tokens,
    edit_view,
    incident_view,
    listener_check,
    project_views,
    savings,
    team_fix_view,
    waiting_fix_view,
)
from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import TokenUsage
from healer.domain.repair import RepairStatus
from healer.domain.sharing import Evidence, PendingShare, ReceivedSolution, SharedSolution
from healer.domain.solution import Solution

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parents[2] / "panel" / "src" / "lib" / "fixtures" / "snapshot.json"


def entry(
    at: datetime,
    resolution: Resolution,
    *,
    project: str = "/workspace/shop",
    spent: int = 0,
    saved: int = 0,
) -> LedgerEntry:
    return LedgerEntry(
        recorded_at=at,
        project=project,
        signature="a" * 64,
        error_type="KeyError",
        status=RepairStatus.FIXED if resolution is not Resolution.NONE else RepairStatus.STOPPED,
        resolution=resolution,
        model_attempts=1 if spent else 0,
        usage=TokenUsage(input_tokens=spent, output_tokens=0),
        tokens_saved=saved,
    )


def test_each_day_counts_what_claude_cost_and_what_each_level_saved() -> None:
    entries = [
        entry(NOW, Resolution.MODEL, spent=1_000),
        entry(NOW - timedelta(hours=1), Resolution.CACHE, saved=700),
        entry(NOW - timedelta(days=1), Resolution.TEAM, saved=400),
        entry(NOW - timedelta(days=40), Resolution.MODEL, spent=9_000),
    ]
    days = daily_tokens(entries, today=NOW.date(), days=3)
    assert [d.day for d in days] == [date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9)]
    assert days[0].incidents == 0
    assert (days[1].saved_by_team, days[1].spent) == (400, 0)
    assert (days[2].incidents, days[2].spent, days[2].saved_by_cache) == (2, 1_000, 700)


def test_days_are_utc_days() -> None:
    late = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)
    days = daily_tokens([entry(late, Resolution.CACHE, saved=5)], today=date(2026, 11, 9), days=2)
    assert days[-1].saved_by_cache == 5


def test_savings_count_the_incidents_of_each_level() -> None:
    entries = [
        entry(NOW, Resolution.CACHE, saved=10),
        entry(NOW, Resolution.CACHE, saved=10),
        entry(NOW, Resolution.MODEL, spent=50),
        entry(NOW, Resolution.NONE, spent=70),
    ]
    summary = savings(entries, today=NOW.date())
    assert summary.resolutions.model_dump() == {"cache": 2, "team": 0, "model": 1, "none": 1}
    assert (summary.total.incidents, summary.total.fixed) == (4, 3)
    assert (summary.total.tokens_used, summary.total.tokens_saved) == (120, 20)
    assert len(summary.days) == 30


def kinds(lines: list[DiffLine]) -> str:
    return "".join(
        {"context": " ", "added": "+", "removed": "-", "gap": "~"}[x.kind] for x in lines
    )


def test_an_edit_shows_the_lines_it_removes_and_adds_around_its_context() -> None:
    old = "def f(c):\n    a = 1\n    return c['db']\n"
    new = "def f(c):\n    a = 1\n    return c.get('db')\n"
    view = edit_view(FileEdit(path="app.py", old=old, new=new))
    assert view.path == "app.py"
    assert not view.created
    assert kinds(view.lines) == "  -+"
    assert view.lines[2].text == "    return c['db']"
    assert view.lines[3].text == "    return c.get('db')"


def test_distant_changes_are_separated_by_a_gap() -> None:
    old = "\n".join(f"line {n}" for n in range(20))
    new = old.replace("line 1\n", "line one\n").replace("line 18", "line eighteen")
    view = edit_view(FileEdit(path="app.py", old=old, new=new))
    assert "~" in kinds(view.lines)
    assert kinds(view.lines).count("-") == 2


def test_a_new_file_is_all_added_lines() -> None:
    view = edit_view(FileEdit(path="tests/test_new.py", old="", new="def test():\n    pass\n"))
    assert view.created
    assert kinds(view.lines) == "++"


def test_projects_show_their_index_and_fixes_most_recently_active_first() -> None:
    entries = [
        entry(NOW - timedelta(days=2), Resolution.MODEL, project="/workspace/api", spent=10),
        entry(NOW, Resolution.NONE, project="/workspace/shop", spent=10),
        entry(NOW - timedelta(days=1), Resolution.CACHE, project="/workspace/shop", saved=10),
    ]
    fix = Solution(
        signature="a" * 64,
        error="KeyError: 'db'",
        patch=Patch(edits=(FileEdit(path="app.py", old="x", new="y"),)),
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=10,
        command="python -m pytest",
        exit_code_before=1,
        verified_at=NOW,
    )
    views = project_views(
        entries,
        symbols={"/workspace/shop": 12, "/workspace/api": None},
        fixes={"/workspace/shop": [fix]},
    )
    assert [v.path for v in views] == ["/workspace/shop", "/workspace/api"]
    shop, api = views
    assert (shop.incidents, shop.fixed, shop.symbols, shop.last_incident) == (2, 1, 12, NOW)
    assert shop.fixes[0].edits[0].lines[0].text == "x"
    assert (api.symbols, api.fixes) == (None, [])


def listener(**changes: object) -> ListenerStatus:
    status = ListenerStatus(
        node="laptop-luis",
        topic="healer/solutions/v1",
        connection="connected to mosquitto:1883",
        connected=True,
        started_at=NOW - timedelta(hours=1),
        changed_at=NOW - timedelta(minutes=30),
        updated_at=NOW - timedelta(seconds=10),
    )
    return status.model_copy(update=changes)


def test_the_listener_check_tells_running_from_stopped_and_silent() -> None:
    assert listener_check(None, now=NOW).state is CheckState.OFF
    running = listener_check(listener(), now=NOW)
    assert (running.state, running.detail) == (CheckState.OK, "connected to mosquitto:1883")
    assert running.since == NOW - timedelta(minutes=30)
    lost = listener(connected=False, connection="cannot reach mosquitto:1883; retrying")
    assert listener_check(lost, now=NOW).state is CheckState.WARNING
    assert listener_check(listener(stopped=True), now=NOW).state is CheckState.OFF
    silent = listener(updated_at=NOW - STALE_AFTER - timedelta(seconds=1))
    check = listener_check(silent, now=NOW)
    assert check.state is CheckState.FAIL
    assert "stopped without saying so" in check.detail


def test_a_waiting_fix_says_whether_it_is_for_the_current_topic() -> None:
    solution = SharedSolution(
        node="laptop-luis",
        published_at=NOW,
        signature="b" * 64,
        error="KeyError",
        patch=Patch(edits=(FileEdit(path="app.py", old="x", new="y"),)),
        explanation="",
        model="m",
        tokens_spent=1,
        evidence=Evidence(command="pytest", exit_code_before=1, verified_at=NOW),
    )
    pending = PendingShare(
        solution=solution, topic="team/a", queued_at=NOW, attempts=2, last_error="down"
    )
    assert waiting_fix_view(pending, topic="team/a").for_current_topic
    assert not waiting_fix_view(pending, topic="team/b").for_current_topic


def test_the_panel_sample_matches_the_snapshot_the_server_sends() -> None:
    """The panel's tests use this sample; it must keep every field the model has, and no
    other, so the TypeScript types and the Python model cannot drift apart unnoticed."""
    raw = json.loads(FIXTURE.read_text())
    snapshot = PanelSnapshot.model_validate(raw)
    assert json.loads(snapshot.model_dump_json()) == raw


def test_recent_activity_mixes_every_source_newest_first() -> None:
    entries = [
        entry(NOW - timedelta(hours=3), Resolution.MODEL, spent=1_200),
        entry(NOW - timedelta(hours=1), Resolution.TEAM, saved=2_900),
        entry(NOW - timedelta(hours=2), Resolution.NONE, spent=500),
    ]
    status = listener(
        recent=[
            ListenerEvent(at=NOW - timedelta(minutes=5), kind="rejected", text="forged"),
            ListenerEvent(at=NOW - timedelta(minutes=6), kind="received", text="KeyError"),
            ListenerEvent(at=NOW - timedelta(minutes=7), kind="ignored", text="sent by this node"),
            ListenerEvent(at=NOW - timedelta(minutes=8), kind="connection", text="cannot reach"),
        ]
    )
    solution = SharedSolution(
        node="laptop-ana",
        published_at=NOW,
        signature="b" * 64,
        error="KeyError: 'db'",
        patch=Patch(edits=(FileEdit(path="app.py", old="x", new="y"),)),
        explanation="",
        model="m",
        tokens_spent=1,
        evidence=Evidence(command="pytest", exit_code_before=1, verified_at=NOW),
    )
    received = ReceivedSolution(solution=solution, received_at=NOW - timedelta(minutes=6))
    pending = PendingShare(
        solution=solution,
        topic="t",
        queued_at=NOW - timedelta(minutes=1),
        attempts=1,
        last_error="broker down",
    )
    items = activity(
        incidents=[incident_view(e) for e in entries],
        team=[team_fix_view(received)],
        outbox=[waiting_fix_view(pending, topic="t")],
        listener=status,
    )
    assert [(i.kind, i.state.value, i.title) for i in items] == [
        ("waiting", "warning", "Fix waiting to be sent"),
        ("rejected", "fail", "Message rejected"),
        ("suggestion", "ok", "Fix received from laptop-ana"),
        ("ignored", "off", "Message ignored"),
        ("connection", "warning", "Team listener"),
        ("incident", "ok", "KeyError fixed in shop"),
        ("incident", "fail", "KeyError not fixed in shop"),
        ("incident", "ok", "KeyError fixed in shop"),
    ]
    assert items[0].detail == "KeyError: 'db': broker down"
    assert items[5].detail == "by the team, 0 tokens spent, 2,900 saved"
    assert items[6].detail == "stopped, 500 tokens spent"
    assert items[7].detail == "by Claude, 1,200 tokens spent"
    assert len(activity(incidents=[], team=[], outbox=[], listener=status, limit=2)) == 2
