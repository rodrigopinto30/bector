import hashlib
import re
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


@pytest.fixture
def fake_embeddings() -> FakeEmbeddingFunction:
    return FakeEmbeddingFunction()


def make_source(path: str, content: str) -> SourceFile:
    return SourceFile(
        path=path, content=content, content_hash=hashlib.sha256(content.encode()).hexdigest()
    )
