import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from healer.adapters.sqlite_ledger import SqliteLedger
from healer.domain.diagnosis import Diagnosis, TracedError
from healer.domain.errors import LedgerError
from healer.domain.execution import RunReport, RunResult
from healer.domain.ledger import LedgerEntry, Resolution, ledger_entry, summarize, total
from healer.domain.patch import FileEdit, Patch
from healer.domain.proposal import Proposal, TokenUsage
from healer.domain.repair import (
    Attempt,
    AttemptOutcome,
    AttemptSource,
    RepairResult,
    RepairStatus,
)

DAY = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)


def entry(
    at: datetime = DAY,
    project: str = "/workspace",
    resolution: Resolution = Resolution.MODEL,
    used: int = 1_000,
    saved: int = 0,
) -> LedgerEntry:
    return LedgerEntry(
        recorded_at=at,
        project=project,
        signature="s" * 64,
        error_type="KeyError",
        status=RepairStatus.FIXED if resolution is not Resolution.NONE else RepairStatus.STOPPED,
        resolution=resolution,
        model_attempts=0 if resolution is Resolution.CACHE else 2,
        usage=TokenUsage(input_tokens=used, output_tokens=0),
        tokens_saved=saved,
    )


def test_summary_per_day_and_total() -> None:
    entries = [
        entry(),
        entry(at=DAY + timedelta(hours=1), resolution=Resolution.CACHE, used=0, saved=1_000),
        entry(at=DAY + timedelta(days=1), resolution=Resolution.NONE, used=500),
    ]
    rows = summarize(entries, "day")
    assert [r.key for r in rows] == ["2026-10-16", "2026-10-17"]
    first = rows[0]
    assert (first.incidents, first.fixed, first.from_cache) == (2, 2, 1)
    assert (first.model_attempts, first.tokens_used, first.tokens_saved) == (2, 1_000, 1_000)
    overall = total(entries)
    assert (overall.key, overall.incidents, overall.fixed, overall.tokens_used) == (
        "total",
        3,
        2,
        1_500,
    )


def test_summary_per_project() -> None:
    rows = summarize([entry(project="/b"), entry(project="/a"), entry(project="/b")], "project")
    assert [(r.key, r.incidents) for r in rows] == [("/a", 1), ("/b", 2)]


def test_no_entry_when_nothing_was_attempted() -> None:
    baseline = RunReport(
        result=RunResult(command=("pytest",), exit_code=0, output="", duration_seconds=0.1),
        files_copied=1,
    )
    passing = RepairResult(status=RepairStatus.PASSING, baseline=baseline)
    assert ledger_entry(passing, project="/w", at=DAY) is None
    target = Diagnosis(
        error=TracedError(error_type="KeyError"), origin=None, signature="x", signature_basis="x"
    )
    stopped = RepairResult(status=RepairStatus.STOPPED, baseline=baseline, target=target)
    assert ledger_entry(stopped, project="/w", at=DAY) is None


def test_entry_never_contains_code_or_messages() -> None:
    patch = Patch(edits=(FileEdit(path="app/cfg.py", old="secret_code()", new="fixed()"),))
    proposal = Proposal(
        patch=patch,
        explanation="secret explanation",
        model="m",
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )
    attempt = Attempt(
        number=1, proposal=proposal, outcome=AttemptOutcome.FAILED, source=AttemptSource.MODEL
    )
    target = Diagnosis(
        error=TracedError(error_type="KeyError", message="secret message"),
        origin=None,
        signature="sig",
        signature_basis="x",
    )
    baseline = RunReport(
        result=RunResult(command=("pytest",), exit_code=1, output="", duration_seconds=0.1),
        files_copied=1,
    )
    result = RepairResult(
        status=RepairStatus.OUT_OF_ATTEMPTS, baseline=baseline, target=target, attempts=(attempt,)
    )
    recorded = ledger_entry(result, project="/w", at=DAY)
    assert recorded is not None
    dumped = recorded.model_dump_json()
    assert "secret" not in dumped
    assert recorded.resolution is Resolution.NONE and recorded.usage.total == 15


def test_sqlite_ledger_round_trip_and_since(tmp_path: Path) -> None:
    ledger = SqliteLedger(tmp_path / "nested" / "ledger.sqlite3")
    assert ledger.entries() == []
    old, new = entry(at=DAY - timedelta(days=10)), entry(at=DAY, saved=7)
    ledger.record(new)
    ledger.record(old)
    assert ledger.entries() == [old, new]
    assert ledger.entries(since=DAY - timedelta(days=1)) == [new]


def test_naive_times_are_treated_as_utc(tmp_path: Path) -> None:
    ledger = SqliteLedger(tmp_path / "ledger.sqlite3")
    ledger.record(entry(at=datetime(2026, 10, 16, 9, 30)))
    assert ledger.entries()[0].recorded_at == DAY


def test_unusable_ledger_path_raises_a_project_error(tmp_path: Path) -> None:
    (tmp_path / "ledger.sqlite3").mkdir()
    with pytest.raises(LedgerError, match="ledger"):
        SqliteLedger(tmp_path / "ledger.sqlite3").record(entry())


def test_corrupt_records_raise_a_project_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    ledger = SqliteLedger(path)
    ledger.record(entry())
    db = sqlite3.connect(path)
    with db:
        db.execute("UPDATE incidents SET resolution = 'magic'")
    db.close()
    with pytest.raises(LedgerError, match="corrupt"):
        ledger.entries()
