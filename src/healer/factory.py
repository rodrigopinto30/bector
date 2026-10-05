"""Builds concrete adapters from configuration. The only place that wires layers together."""

import hashlib
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.types import EmbeddingFunction

from healer.adapters.chroma_repository import ChromaSymbolRepository
from healer.adapters.python_parser import PythonParser
from healer.adapters.python_traceback import PythonTraceParser
from healer.adapters.workspace_paths import WorkspacePathResolver
from healer.adapters.workspace_scanner import WorkspaceScanner
from healer.config import Settings
from healer.services.code_index import CodeIndex
from healer.services.diagnoser import Diagnoser


def collection_name(root: Path) -> str:
    """One collection per workspace so projects with equal relative paths never mix."""
    return "code-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]


def build_code_index(
    workspace: Path,
    settings: Settings,
    embedding_function: EmbeddingFunction[Any] | None = None,
) -> CodeIndex:
    scanner = WorkspaceScanner(workspace)
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    repository = ChromaSymbolRepository(client, collection_name(scanner.root), embedding_function)
    return CodeIndex(scanner, PythonParser(), repository)


def build_diagnoser(
    workspace: Path,
    settings: Settings,
    embedding_function: EmbeddingFunction[Any] | None = None,
) -> Diagnoser:
    resolver = WorkspacePathResolver(workspace)
    code_index = build_code_index(workspace, settings, embedding_function)
    return Diagnoser(PythonTraceParser(), resolver, code_index)
