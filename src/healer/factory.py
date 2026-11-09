"""Builds concrete adapters from configuration. The only place that wires layers together."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import anthropic
import chromadb
from chromadb.api.types import EmbeddingFunction

from healer.adapters.anthropic_proposer import AnthropicProposer
from healer.adapters.chroma_panel import ChromaPanelStore
from healer.adapters.chroma_repository import ChromaSymbolRepository
from healer.adapters.chroma_solutions import ChromaSolutionRepository
from healer.adapters.chroma_team_solutions import ChromaTeamSolutions
from healer.adapters.filesystem_patcher import FilesystemPatcher
from healer.adapters.json_listener_status import JsonListenerStatus
from healer.adapters.mqtt_publisher import MqttPublisher
from healer.adapters.mqtt_subscriber import MqttSubscriber
from healer.adapters.panel_server import LOCAL_HOSTS, PanelApp
from healer.adapters.python_parser import PythonParser
from healer.adapters.python_traceback import PythonTraceParser
from healer.adapters.sandbox_client import SandboxServiceRunner
from healer.adapters.sqlite_ledger import SqliteLedger
from healer.adapters.sqlite_outbox import SqliteOutbox
from healer.adapters.subprocess_runner import SubprocessRunner
from healer.adapters.tempdir_sandbox import TempDirSandboxProvider
from healer.adapters.workspace_paths import WorkspacePathResolver
from healer.adapters.workspace_scanner import WorkspaceScanner
from healer.config import Settings
from healer.domain.errors import LLMError, SharingError
from healer.domain.execution import CommandPolicy
from healer.domain.panel import Check, CheckState
from healer.domain.patch import PatchPolicy
from healer.domain.ports import CommandRunner, PatchProposer, SolutionPublisher
from healer.domain.proposal import Proposal
from healer.domain.redaction import Redactor
from healer.domain.sharing import MIN_KEY_LENGTH
from healer.pipeline.repair import RepairPipeline
from healer.services.code_index import CodeIndex
from healer.services.diagnoser import Diagnoser
from healer.services.fix_installer import FixInstaller
from healer.services.fix_proposer import FixProposer
from healer.services.panel import PanelReader, Probe
from healer.services.sandbox_runner import SandboxRunner
from healer.services.solution_ingester import SolutionIngester
from healer.services.solution_sharer import SolutionSharer
from healer.services.suggestion_finder import SuggestionFinder


def collection_name(root: Path, kind: str = "code") -> str:
    """One collection per workspace so projects with equal relative paths never mix."""
    return f"{kind}-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]


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
    provider = TempDirSandboxProvider(
        workspace, max_bytes=settings.max_sandbox_bytes, base_dir=settings.sandbox_dir
    )
    timeout = timeout_seconds or settings.test_timeout_seconds
    runner: CommandRunner
    if settings.sandbox_socket is not None:
        runner = SandboxServiceRunner(
            settings.sandbox_socket,
            timeout_seconds=timeout,
            max_output_bytes=settings.max_output_bytes,
        )
    else:
        runner = SubprocessRunner(
            timeout_seconds=timeout, max_output_bytes=settings.max_output_bytes
        )
    policy = CommandPolicy(settings.allowed_test_commands)
    diagnoser = build_diagnoser(workspace, settings, embedding_function)
    applier = FilesystemPatcher(max_changed_lines=settings.max_patch_changed_lines)
    return SandboxRunner(provider, runner, policy, diagnoser, applier, _patch_policy(settings))


def build_sandbox_client(settings: Settings) -> SandboxServiceRunner | None:
    """The test sandbox, or None when the tests run in this container."""
    if settings.sandbox_socket is None:
        return None
    return SandboxServiceRunner(
        settings.sandbox_socket,
        timeout_seconds=settings.test_timeout_seconds,
        max_output_bytes=settings.max_output_bytes,
    )


def build_fix_installer(workspace: Path, settings: Settings) -> FixInstaller:
    applier = FilesystemPatcher(max_changed_lines=settings.max_patch_changed_lines)
    return FixInstaller(workspace, applier, _patch_policy(settings))


def _patch_policy(settings: Settings) -> PatchPolicy:
    return PatchPolicy(max_files=settings.max_patch_files, max_bytes=settings.max_patch_bytes)


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


def build_repair_pipeline(
    workspace: Path,
    settings: Settings,
    embedding_function: EmbeddingFunction[Any] | None = None,
    *,
    code_index: CodeIndex | None = None,
    llm: PatchProposer | None = None,
    timeout_seconds: float | None = None,
    max_attempts: int | None = None,
) -> RepairPipeline:
    runner = build_sandbox_runner(
        workspace, settings, embedding_function, timeout_seconds=timeout_seconds
    )
    proposer = build_fix_proposer(
        workspace, settings, embedding_function, code_index=code_index, llm=llm
    )
    root = WorkspaceScanner(workspace).root
    return RepairPipeline(
        runner,
        proposer,
        build_solutions(workspace, settings, embedding_function),
        build_ledger(settings),
        suggestions=build_suggestion_finder(settings, embedding_function),
        project=str(root),
        max_attempts=max_attempts or settings.repair_max_attempts,
        max_tokens=settings.repair_max_tokens,
    )


def build_solutions(
    workspace: Path,
    settings: Settings,
    embedding_function: EmbeddingFunction[Any] | None = None,
) -> ChromaSolutionRepository:
    root = WorkspaceScanner(workspace).root
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    return ChromaSolutionRepository(client, collection_name(root, "solutions"), embedding_function)


def build_ledger(settings: Settings) -> SqliteLedger:
    return SqliteLedger(settings.ledger_path)


def build_publisher(settings: Settings) -> MqttPublisher:
    password = settings.mqtt_password
    return MqttPublisher(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        topic=settings.mqtt_topic,
        client_id=f"healer-{settings.node_id}",
        username=settings.mqtt_username,
        password=password.get_secret_value() if password is not None else None,
        tls=settings.mqtt_tls,
        ca_cert=settings.mqtt_ca_cert,
        timeout_seconds=settings.mqtt_timeout_seconds,
        max_retries=settings.mqtt_max_retries,
    )


def build_outbox(settings: Settings) -> SqliteOutbox:
    return SqliteOutbox(settings.outbox_path)


def build_solution_sharer(
    settings: Settings, publisher: SolutionPublisher | None = None
) -> SolutionSharer:
    return SolutionSharer(
        publisher or build_publisher(settings),
        Redactor(),
        node=settings.node_id,
        key=_team_key(settings),
        max_bytes=settings.max_share_bytes,
        outbox=build_outbox(settings),
    )


def build_subscriber(settings: Settings) -> MqttSubscriber:
    password = settings.mqtt_password
    return MqttSubscriber(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        topic=settings.mqtt_topic,
        client_id=f"healer-listener-{settings.node_id}",
        username=settings.mqtt_username,
        password=password.get_secret_value() if password is not None else None,
        tls=settings.mqtt_tls,
        ca_cert=settings.mqtt_ca_cert,
    )


def build_team_solutions(
    settings: Settings, embedding_function: EmbeddingFunction[Any] | None = None
) -> ChromaTeamSolutions:
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    return ChromaTeamSolutions(client, _team_collection(settings), embedding_function)


def _team_collection(settings: Settings) -> str:
    return "team-" + hashlib.sha256(settings.mqtt_topic.encode()).hexdigest()[:16]


def build_suggestion_finder(
    settings: Settings, embedding_function: EmbeddingFunction[Any] | None = None
) -> SuggestionFinder:
    return SuggestionFinder(
        build_team_solutions(settings, embedding_function),
        max_distance=settings.team_max_distance,
        max_matches=settings.team_max_suggestions,
    )


def build_solution_ingester(
    settings: Settings, embedding_function: EmbeddingFunction[Any] | None = None
) -> SolutionIngester:
    return SolutionIngester(
        build_team_solutions(settings, embedding_function),
        _patch_policy(settings),
        node=settings.node_id,
        key=_team_key(settings),
        max_bytes=settings.max_share_bytes,
    )


def build_listener_status(settings: Settings) -> JsonListenerStatus:
    return JsonListenerStatus(settings.listener_status_path)


def build_panel_reader(
    settings: Settings, embedding_function: EmbeddingFunction[Any] | None = None
) -> PanelReader:
    """Reads what the other commands write. The broker is asked with a short timeout, so a
    broker that is down never makes the panel wait long."""
    client = chromadb.PersistentClient(path=str(settings.chroma_path))
    projects = ChromaPanelStore(
        client,
        code_collection=lambda path: collection_name(Path(path)),
        fixes_collection=lambda path: collection_name(Path(path), "solutions"),
        team_collection=_team_collection(settings),
        embedding_function=embedding_function,
    )
    quick = settings.model_copy(
        update={"mqtt_timeout_seconds": min(settings.mqtt_timeout_seconds, 3.0)}
    )
    broker = f"{settings.mqtt_host}:{settings.mqtt_port}"
    over = " over TLS" if settings.mqtt_tls else ""
    sandbox = build_sandbox_client(settings)
    return PanelReader(
        node=settings.node_id,
        topic=settings.mqtt_topic,
        sharing=settings.share_solutions,
        ledger=build_ledger(settings),
        outbox=build_outbox(settings),
        projects=projects,
        listener=build_listener_status(settings),
        checks=[
            Probe(
                key="broker",
                name="Message broker",
                target=build_publisher(quick),
                ok=f"{broker} accepts {settings.mqtt_username or 'anonymous clients'}{over}",
            ),
            Probe(
                key="sandbox",
                name="Test sandbox",
                target=sandbox,
                ok="no network, no secrets, no access to your files",
                off="off: the tests run in the cli container, with its access",
            ),
            _node_check(settings),
            _sharing_check(settings),
            _api_key_check(settings),
        ],
    )


def build_panel_app(settings: Settings, *, extra_hosts: Iterable[str] = ()) -> PanelApp:
    return PanelApp(
        build_panel_reader(settings).snapshot,
        settings.panel_pages,
        allowed_hosts=LOCAL_HOSTS | {host.lower() for host in extra_hosts},
    )


def _node_check(settings: Settings) -> Check:
    """Without NODE_ID each container takes its own name, so the team sees several nodes and
    the listener cannot pick up what was sent while it was off."""
    name = "Node name"
    if "node_id" in settings.model_fields_set:
        return Check(key="node", name=name, state=CheckState.OK, detail=settings.node_id)
    return Check(
        key="node",
        name=name,
        state=CheckState.WARNING if settings.share_solutions else CheckState.OFF,
        detail=(
            f"not set: each container uses its own name, this one {settings.node_id}; "
            "add NODE_ID to .env to give this computer one name in the team"
        ),
    )


def _sharing_check(settings: Settings) -> Check:
    name = "Sharing"
    key = settings.share_key.get_secret_value().strip() if settings.share_key else ""
    if not settings.share_solutions:
        return Check(
            key="sharing",
            name=name,
            state=CheckState.OFF,
            detail="off: set SHARE_SOLUTIONS=true to send accepted fixes",
        )
    if len(key) < MIN_KEY_LENGTH:
        return Check(
            key="sharing",
            name=name,
            state=CheckState.FAIL,
            detail=f"on, but SHARE_KEY is missing or shorter than {MIN_KEY_LENGTH} characters",
        )
    return Check(
        key="sharing",
        name=name,
        state=CheckState.OK,
        detail=f"on, as node {settings.node_id} on {settings.mqtt_topic}",
    )


def _api_key_check(settings: Settings) -> Check:
    name = "Anthropic API key"
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value().strip():
        return Check(
            key="api_key",
            name=name,
            state=CheckState.FAIL,
            detail="not set: add ANTHROPIC_API_KEY to .env; fixes found before are still reused",
        )
    return Check(key="api_key", name=name, state=CheckState.OK, detail="set")


def _team_key(settings: Settings) -> bytes:
    key = settings.share_key
    if key is None or not key.get_secret_value().strip():
        raise SharingError("SHARE_KEY is not set: add the team key to .env to share solutions")
    return key.get_secret_value().encode()


class _LazyProposer:
    """Creates the Anthropic client on first use, so building a prompt needs no API key."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._llm: PatchProposer | None = None

    def propose(self, system: str, prompt: str) -> Proposal:
        if self._llm is None:
            self._llm = build_llm(self._settings)
        return self._llm.propose(system, prompt)
