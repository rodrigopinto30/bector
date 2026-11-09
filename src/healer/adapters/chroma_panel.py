"""``ProjectStore`` over the Chroma collections the other commands write."""

from collections.abc import Callable
from typing import Any

from chromadb.api import ClientAPI
from chromadb.api.types import EmbeddingFunction

from healer.adapters.chroma_repository import ChromaSymbolRepository
from healer.adapters.chroma_solutions import ChromaSolutionRepository
from healer.adapters.chroma_team_solutions import ChromaTeamSolutions
from healer.domain.sharing import ReceivedSolution
from healer.domain.solution import Solution


class ChromaPanelStore:
    """Opens only the collections that already exist, so reading never creates one. Each
    collection keeps a single writer: ``healer index`` and ``healer run`` for a project's
    code and fixes, ``healer listen`` for the team."""

    def __init__(
        self,
        client: ClientAPI,
        *,
        code_collection: Callable[[str], str],
        fixes_collection: Callable[[str], str],
        team_collection: str,
        embedding_function: EmbeddingFunction[Any] | None = None,
    ) -> None:
        self._client = client
        self._code = code_collection
        self._fixes = fixes_collection
        self._team = team_collection
        self._embedder = embedding_function

    def symbols(self, project: str) -> int | None:
        name = self._code(project)
        if not self._exists(name):
            return None
        return ChromaSymbolRepository(self._client, name, self._embedder).count()

    def fixes(self, project: str) -> list[Solution]:
        name = self._fixes(project)
        if not self._exists(name):
            return []
        return ChromaSolutionRepository(self._client, name, self._embedder).all()

    def team(self) -> list[ReceivedSolution]:
        if not self._exists(self._team):
            return []
        return ChromaTeamSolutions(self._client, self._team, self._embedder).all()

    def _exists(self, name: str) -> bool:
        return any(collection.name == name for collection in self._client.list_collections())
