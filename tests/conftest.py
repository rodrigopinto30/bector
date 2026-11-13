import hashlib
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from healer.domain.actions import PanelAction
from healer.domain.models import SourceFile
from healer.domain.ranking import Tally
from healer.domain.sharing import FixResult


class FakeEmbeddingFunction(EmbeddingFunction[Documents]):
    """Deterministic bag-of-words embedding so tests never download a model."""

    DIMENSIONS = 64

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed(text) for text in input]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.DIMENSIONS
        for word in re.findall(r"[a-z]+", text.lower()):
            slot = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIMENSIONS
            vector[slot] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    @staticmethod
    def name() -> str:
        return "healer-fake"

    def get_config(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "FakeEmbeddingFunction":
        return FakeEmbeddingFunction()


class FakeResults:
    """``ResultStore`` in memory: one vote per node and fix, its latest."""

    def __init__(self, *results: FixResult) -> None:
        self.votes: dict[tuple[str, str], FixResult] = {}
        for result in results:
            self.record(result)

    def record(self, result: FixResult) -> bool:
        key = (result.fix_id, result.node)
        known = self.votes.get(key)
        if known is not None and known.reported_at >= result.reported_at:
            return False
        self.votes[key] = result
        return known is None or known.worked != result.worked

    def tally(self, fix_ids: Iterable[str]) -> dict[str, Tally]:
        wanted = set(fix_ids)
        tallies: dict[str, Tally] = {}
        for (fix, _), result in self.votes.items():
            if fix in wanted:
                tally = tallies.get(fix, Tally())
                tallies[fix] = Tally(
                    worked=tally.worked + result.worked, failed=tally.failed + (not result.worked)
                )
        return tallies


class FakeForgotten:
    """``ForgottenFixes`` in memory."""

    def __init__(self, *fixes: str) -> None:
        self.fixes = set(fixes)

    def forget(self, fix: str, signature: str, at: datetime) -> None:
        self.fixes.add(fix)

    def forgotten(self, fix_ids: Iterable[str]) -> set[str]:
        return set(fix_ids) & self.fixes


class FakeActionLog:
    """``ActionLog`` in memory, newest first."""

    def __init__(self) -> None:
        self.actions: list[PanelAction] = []

    def record(self, action: PanelAction) -> None:
        self.actions.insert(0, action)

    def recent(self, limit: int) -> list[PanelAction]:
        return self.actions[:limit]


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own ledger, so none can write to the real one."""
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setenv("LEDGER_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def isolated_outbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own outbox, so none can send or drop the real waiting fixes."""
    path = tmp_path / "outbox.sqlite3"
    monkeypatch.setenv("OUTBOX_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def isolated_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own results of the team's fixes, so none changes the real ones."""
    path = tmp_path / "results.sqlite3"
    monkeypatch.setenv("RESULTS_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own history of runs, so none changes what the panel shows."""
    path = tmp_path / "history.sqlite3"
    monkeypatch.setenv("HISTORY_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def isolated_listener_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own listener status, so none can change what the panel shows."""
    path = tmp_path / "listener.json"
    monkeypatch.setenv("LISTENER_STATUS_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def no_sharing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test sends solutions to the real broker: sharing is off and the broker unreachable.

    Tests that share build their own publisher.
    """
    monkeypatch.setenv("SHARE_SOLUTIONS", "false")
    monkeypatch.delenv("SHARE_KEY", raising=False)
    monkeypatch.delenv("NODE_ID", raising=False)
    monkeypatch.setenv("MQTT_HOST", "127.0.0.1")
    monkeypatch.setenv("MQTT_PORT", "1")


@pytest.fixture(autouse=True)
def tests_run_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests run their own commands in this container; the sandbox has its own tests."""
    monkeypatch.delenv("SANDBOX_SOCKET", raising=False)
    monkeypatch.delenv("SANDBOX_DIR", raising=False)


@pytest.fixture
def fake_embeddings() -> FakeEmbeddingFunction:
    return FakeEmbeddingFunction()


def make_source(path: str, content: str) -> SourceFile:
    return SourceFile(
        path=path, content=content, content_hash=hashlib.sha256(content.encode()).hexdigest()
    )
