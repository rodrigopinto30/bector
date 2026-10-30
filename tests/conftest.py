import hashlib
import re
from pathlib import Path
from typing import Any

import pytest
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from healer.domain.models import SourceFile


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


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own ledger, so none can write to the real one."""
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setenv("LEDGER_PATH", str(path))
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


@pytest.fixture
def fake_embeddings() -> FakeEmbeddingFunction:
    return FakeEmbeddingFunction()


def make_source(path: str, content: str) -> SourceFile:
    return SourceFile(
        path=path, content=content, content_hash=hashlib.sha256(content.encode()).hexdigest()
    )
