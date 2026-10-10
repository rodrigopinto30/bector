"""Builds concrete adapters from configuration. The only place that wires layers together."""

import hashlib
from pathlib import Path
from typing import Any

import anthropic
import chromadb
from chromadb.api.types import EmbeddingFunction

from healer.adapters.anthropic_proposer import AnthropicProposer
from healer.adapters.chroma_repository import ChromaSymbolRepository
from healer.adapters.filesystem_patcher import FilesystemPatcher
from healer.adapters.python_parser import PythonParser
from healer.adapters.python_traceback import PythonTraceParser
from healer.adapters.subprocess_runner import SubprocessRunner
from healer.adapters.tempdir_sandbox import TempDirSandboxProvider
from healer.adapters.workspace_paths import WorkspacePathResolver
from healer.adapters.workspace_scanner import WorkspaceScanner
from healer.config import Settings
from healer.domain.errors import LLMError
from healer.domain.execution import CommandPolicy
from healer.domain.patch import PatchPolicy
from healer.domain.ports import PatchProposer
from healer.domain.proposal import Proposal
from healer.domain.redaction import Redactor
from healer.services.code_index import CodeIndex
from healer.services.diagnoser import Diagnoser
from healer.services.fix_proposer import FixProposer
from healer.services.sandbox_runner import SandboxRunner


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


def build_sandbox_runner(
    workspace: Path,
    settings: Settings,
    embedding_function: EmbeddingFunction[Any] | None = None,
    *,
    timeout_seconds: float | None = None,
) -> SandboxRunner:
    provider = TempDirSandboxProvider(workspace, max_bytes=settings.max_sandbox_bytes)
    runner = SubprocessRunner(
        timeout_seconds=timeout_seconds or settings.test_timeout_seconds,
        max_output_bytes=settings.max_output_bytes,
    )
    policy = CommandPolicy(settings.allowed_test_commands)
    diagnoser = build_diagnoser(workspace, settings, embedding_function)
    applier = FilesystemPatcher(max_changed_lines=settings.max_patch_changed_lines)
    patch_policy = PatchPolicy(
        max_files=settings.max_patch_files, max_bytes=settings.max_patch_bytes
    )
    return SandboxRunner(provider, runner, policy, diagnoser, applier, patch_policy)


def build_llm(settings: Settings) -> PatchProposer:
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        raise LLMError("ANTHROPIC_API_KEY is not set: add it to .env and recreate the container")
    client = anthropic.Anthropic(
        api_key=key.get_secret_value(),
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
    )
    return AnthropicProposer(
        client,
        model=settings.anthropic_model,
        effort=settings.anthropic_effort,
        max_tokens=settings.anthropic_max_tokens,
    )


def build_fix_proposer(
    workspace: Path,
    settings: Settings,
    embedding_function: EmbeddingFunction[Any] | None = None,
    *,
    code_index: CodeIndex | None = None,
    llm: PatchProposer | None = None,
) -> FixProposer:
    index = code_index or build_code_index(workspace, settings, embedding_function)
    return FixProposer(
        index,
        index,
        llm or _LazyProposer(settings),
        Redactor(),
        max_context_chars=settings.context_max_chars,
        max_distance=settings.context_max_distance,
    )


class _LazyProposer:
    """Creates the Anthropic client on first use, so building a prompt needs no API key."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._llm: PatchProposer | None = None

    def propose(self, system: str, prompt: str) -> Proposal:
        if self._llm is None:
            self._llm = build_llm(self._settings)
        return self._llm.propose(system, prompt)
