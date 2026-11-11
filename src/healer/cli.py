import importlib
import json
import os
import signal
import threading
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from healer import __version__
from healer.adapters.log_reader import read_log
from healer.config import Settings
from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import HealerError, RejectedMessageError
from healer.domain.execution import RunReport
from healer.domain.ledger import LedgerSummary, summarize, total
from healer.domain.patch import AppliedPatch, Patch, parse_patch
from healer.domain.proposal import SYSTEM_PROMPT, TokenUsage, render_request
from healer.domain.ranking import results_of
from healer.domain.redaction import Redactor
from healer.domain.repair import (
    Attempt,
    AttemptOutcome,
    AttemptSource,
    RepairResult,
    RepairStatus,
)
from healer.domain.sharing import (
    MIN_KEY_LENGTH,
    Ingested,
    IngestOutcome,
    PendingResult,
    PendingShare,
    ResendReport,
    ResultIngested,
    ResultOutcome,
    TeamMatch,
)
from healer.domain.solution import Solution
from healer.factory import (
    build_code_index,
    build_diagnoser,
    build_fix_installer,
    build_fix_proposer,
    build_ledger,
    build_listener_status,
    build_outbox,
    build_panel_app,
    build_publisher,
    build_repair_pipeline,
    build_results,
    build_sandbox_client,
    build_sandbox_runner,
    build_solution_ingester,
    build_solution_sharer,
    build_subscriber,
    build_team_solutions,
)
from healer.services.listener_monitor import ListenerMonitor
from healer.services.solution_sharer import SolutionSharer

app = typer.Typer(help="Local-first autonomous code remediation system.", no_args_is_help=True)

DEPENDENCIES = ["typer", "tree_sitter", "chromadb", "langgraph", "anthropic", "git", "paho.mqtt"]


@app.command()
def version() -> None:
    """Show the installed version."""
    typer.echo(f"healer {__version__}")


@app.command()
def doctor() -> None:
    """Check that dependencies and configuration are in place."""
    failed = False
    for module in DEPENDENCIES:
        try:
            importlib.import_module(module)
            typer.echo(f"[ok]   {module}")
        except ImportError:
            failed = True
            typer.echo(f"[fail] {module}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("[ok]   ANTHROPIC_API_KEY")
    else:
        failed = True
        typer.echo("[fail] ANTHROPIC_API_KEY is not set")
    settings = Settings()
    if os.geteuid() == 0:
        typer.echo("[info] user: root; ./start.sh makes the containers run as your own user")
    else:
        typer.echo(f"[ok]   user: uid {os.geteuid()}, not root")
    if not _sandbox_ready(settings):
        failed = True
    if not _broker_ready(settings):
        failed = True
    key = settings.share_key.get_secret_value().strip() if settings.share_key else ""
    if not settings.share_solutions:
        typer.echo("[info] sharing: off (set SHARE_SOLUTIONS=true to send accepted fixes)")
    elif len(key) < MIN_KEY_LENGTH:
        failed = True
        typer.echo(
            f"[fail] sharing is on, but SHARE_KEY is missing or shorter than "
            f"{MIN_KEY_LENGTH} characters"
        )
    else:
        typer.echo(f"[ok]   sharing: on, as node {settings.node_id} on {settings.mqtt_topic}")
    try:
        store = build_outbox(settings)
        waiting = _waiting(len(store.pending()), len(store.pending_results()))
    except HealerError as exc:
        failed = True
        typer.echo(f"[fail] outbox: {exc}")
    else:
        if waiting:
            typer.echo(f"[info] outbox: {waiting} waiting to be sent; 'healer outbox' lists them")
    raise typer.Exit(code=1 if failed else 0)


def _sandbox_ready(settings: Settings) -> bool:
    """Print where the tests run, and whether the test sandbox answers."""
    sandbox = build_sandbox_client(settings)
    if sandbox is None:
        typer.echo("[info] test sandbox: off; tests run in this container, with its access")
        return True
    try:
        sandbox.check()
    except HealerError as exc:
        typer.echo(f"[fail] test sandbox: {exc}")
        return False
    typer.echo("[ok]   test sandbox: no network, no secrets, no access to your files")
    return True


def _broker_ready(settings: Settings) -> bool:
    """Print whether the broker accepts this node's credentials."""
    address = f"{settings.mqtt_host}:{settings.mqtt_port}"
    user = settings.mqtt_username
    try:
        build_publisher(settings).check()
    except HealerError as exc:
        typer.echo(f"[fail] MQTT broker {address}: {exc}")
        if user is None:
            typer.echo(
                "       MQTT_USERNAME and MQTT_PASSWORD are not set: ./start.sh creates them "
                "for the broker of this machine, and './broker.sh add NODE_ID' on another "
                "machine gives them for its broker"
            )
        return False
    over = " over TLS" if settings.mqtt_tls else ""
    typer.echo(f"[ok]   MQTT broker {address} accepts {user or 'anonymous clients'}{over}")
    return True


WorkspaceOption = Annotated[
    Path, typer.Option("--path", "-p", help="Workspace root that was indexed")
]

ApplyOption = Annotated[
    bool | None,
    typer.Option(
        "--apply/--no-apply",
        help="Apply the fix to your files without asking, or never; asks by default",
    ),
]


@app.command()
def index(
    path: Annotated[Path, typer.Argument(help="Repository path to index")] = Path("."),
) -> None:
    """Index the repository into the local vector database."""
    try:
        code_index = build_code_index(path, Settings())
        report = code_index.index()
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"indexed {report.files_indexed} files ({report.symbols_indexed} symbols), "
        f"{report.files_unchanged} unchanged, {report.files_removed} removed"
    )
    if report.files_with_syntax_errors:
        typer.echo(f"warning: {report.files_with_syntax_errors} files have syntax errors", err=True)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Natural-language description of the code")],
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=50)] = 5,
    path: WorkspaceOption = Path("."),
) -> None:
    """Find the indexed symbols most similar in meaning to QUERY."""
    try:
        hits = build_code_index(path, Settings()).search(query, limit)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not hits:
        typer.echo("no results (has the workspace been indexed?)")
        raise typer.Exit(code=1)
    for hit in hits:
        s = hit.symbol
        typer.echo(
            f"{hit.distance:.3f}  {s.file}:{s.start_line}-{s.end_line}  {s.signature or s.name}"
        )


@app.command(name="map")
def map_file(
    file: Annotated[str, typer.Argument(help="File path relative to the workspace root")],
    path: WorkspaceOption = Path("."),
) -> None:
    """Show the structural map (signatures and line ranges) of an indexed file."""
    try:
        symbols = build_code_index(path, Settings()).outline(file)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not symbols:
        typer.echo(f"{file} is not indexed")
        raise typer.Exit(code=1)
    for s in symbols:
        depth = s.qualified_name.count(".") if s.kind.value != "module" else 0
        label = f"module {s.name}" if s.kind.value == "module" else s.signature
        typer.echo(f"{s.start_line:>5}-{s.end_line:<5} {'  ' * depth}{label}")
    module = symbols[0]
    if module.imports:
        typer.echo(f"imports: {', '.join(module.imports)}")


@app.command()
def diagnose(
    log: Annotated[
        Path | None, typer.Argument(help="Log file with the failure; reads stdin when omitted")
    ] = None,
    path: WorkspaceOption = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON")] = False,
) -> None:
    """Turn a traceback or pytest output into structured error diagnoses."""
    try:
        text = read_log(None if log is None or str(log) == "-" else log)
        diagnoses = build_diagnoser(path, Settings()).diagnose(text)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if as_json:
        typer.echo(json.dumps([d.model_dump(mode="json") for d in diagnoses], indent=2))
    else:
        for number, diagnosis in enumerate(diagnoses, start=1):
            _print_diagnosis(number, diagnosis)
    if not diagnoses:
        typer.echo("no errors found in the log", err=True)
        raise typer.Exit(code=1)


def _print_diagnosis(number: int, d: Diagnosis) -> None:
    """Print a diagnosis. It comes from the output of the code under test, so control
    characters are masked: that code must not be able to rewrite what the terminal shows."""
    in_workspace = sum(1 for f in d.error.frames if f.workspace_file)
    typer.echo(f"[{number}] {_printable(d.error.summary)}")
    typer.echo(f"    signature  {d.signature[:12]}")
    if d.origin is None:
        typer.echo("    origin     no frame inside the workspace")
    else:
        where = f" in {_printable(d.origin.function)}" if d.origin.function else ""
        file = _printable(str(d.origin.workspace_file))
        typer.echo(f"    origin     {file}:{d.origin.line}{where}")
        if d.origin.code:
            typer.echo(f"    code       {_printable(d.origin.code)}")
        if d.location is None:
            typer.echo("    symbol     not indexed (run 'healer index')")
        else:
            s = d.location
            label = s.signature or f"module {s.name}"
            typer.echo(f"    symbol     {label}  ({s.file}:{s.start_line}-{s.end_line})")
    typer.echo(f"    frames     {len(d.error.frames)} ({in_workspace} in workspace)")
    for cause in d.error.causes:
        typer.echo(f"    caused by  {_printable(cause)}")
    if d.occurrences > 1:
        typer.echo(f"    seen       {d.occurrences} times")
    typer.echo("")


_PYTEST_EXIT_CODES = {
    0: "passed",
    1: "tests failed",
    2: "interrupted",
    3: "internal error",
    4: "usage error",
    5: "no tests collected",
}


@app.command(name="test")
def test_command(
    command: Annotated[
        str | None,
        typer.Argument(help="Test command; defaults to TEST_COMMAND ('python -m pytest')"),
    ] = None,
    path: WorkspaceOption = Path("."),
    timeout: Annotated[
        float | None, typer.Option("--timeout", min=1, help="Seconds before the run is killed")
    ] = None,
    show_output: Annotated[
        bool, typer.Option("--output", help="Also print the captured test output")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON")] = False,
) -> None:
    """Run the tests on an isolated copy of the workspace and diagnose the failures."""
    settings = Settings()
    try:
        runner = build_sandbox_runner(path, settings, timeout_seconds=timeout)
        report = runner.run_tests(command or settings.test_command)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _report_run(report, show_output=show_output, as_json=as_json)


@app.command()
def patch(
    patch_file: Annotated[
        Path, typer.Argument(help="JSON patch with search-and-replace edits; '-' reads stdin")
    ],
    command: Annotated[
        str | None,
        typer.Argument(help="Test command; defaults to TEST_COMMAND ('python -m pytest')"),
    ] = None,
    path: WorkspaceOption = Path("."),
    timeout: Annotated[
        float | None, typer.Option("--timeout", min=1, help="Seconds before the run is killed")
    ] = None,
    show_output: Annotated[
        bool, typer.Option("--output", help="Also print the captured test output")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON")] = False,
) -> None:
    """Apply a patch to an isolated copy, show the diff and run the tests against it.

    The real workspace is never modified.
    """
    settings = Settings()
    try:
        proposal = parse_patch(read_log(None if str(patch_file) == "-" else patch_file))
        runner = build_sandbox_runner(path, settings, timeout_seconds=timeout)
        report = runner.run_tests(command or settings.test_command, patch=proposal)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not as_json:
        _print_applied(proposal, report.patch, show_purpose=True)
    _report_run(report, show_output=show_output, as_json=as_json, verdict=True)


@app.command()
def propose(
    log: Annotated[
        Path | None, typer.Argument(help="Log file with the failure; reads stdin when omitted")
    ] = None,
    command: Annotated[
        str | None,
        typer.Option("--command", "-c", help="Test command used to validate the proposal"),
    ] = None,
    path: WorkspaceOption = Path("."),
    error_number: Annotated[
        int, typer.Option("--error", "-e", min=1, help="Which diagnosed error to fix")
    ] = 1,
    run_tests: Annotated[
        bool, typer.Option("--test/--no-test", help="Validate the proposal in a sandbox")
    ] = True,
    save: Annotated[
        Path | None, typer.Option("--save", help="Write the proposed patch as JSON to this file")
    ] = None,
    timeout: Annotated[
        float | None, typer.Option("--timeout", min=1, help="Seconds before the run is killed")
    ] = None,
    show_output: Annotated[
        bool, typer.Option("--output", help="Also print the captured test output")
    ] = False,
    show_prompt: Annotated[
        bool,
        typer.Option("--show-prompt", help="Print what would be sent to Claude and stop"),
    ] = False,
) -> None:
    """Ask Claude for a patch that fixes an error from the log, then test it on a copy.

    Only the diagnosis and the related functions are sent, after hiding secrets. The real
    workspace is never modified.
    """
    settings = Settings()
    try:
        text = read_log(None if log is None or str(log) == "-" else log)
        code_index = build_code_index(path, settings)
        code_index.index()
        diagnoses = build_diagnoser(path, settings).diagnose(text)
        if not diagnoses:
            typer.echo("no errors found in the log", err=True)
            raise typer.Exit(code=1)
        if error_number > len(diagnoses):
            typer.echo(f"error: the log has only {_plural(len(diagnoses), 'error')}", err=True)
            raise typer.Exit(code=2)
        diagnosis = diagnoses[error_number - 1]
        typer.echo(f"error      {diagnosis.error.summary}")
        proposer = build_fix_proposer(path, settings, code_index=code_index)
        if show_prompt:
            request, counts = proposer.prepare(diagnosis)
            typer.echo(f"secrets    {sum(counts.values())} hidden before sending")
            typer.echo("")
            typer.echo("----- system -----")
            typer.echo(SYSTEM_PROMPT)
            typer.echo("----- user -----")
            typer.echo(render_request(request))
            return
        proposal = proposer.propose(diagnosis)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    labels = ", ".join(f"{c.file}:{c.label}" for c in proposal.context)
    hidden = sum(proposal.redactions.values())
    typer.echo(f"context    {_plural(len(proposal.context), 'snippet')} sent ({labels})")
    typer.echo(f"secrets    {hidden} hidden before sending")
    typer.echo(f"model      {proposal.model}, {_tokens(proposal.usage)}")
    typer.echo(f"proposal   {proposal.explanation}")
    typer.echo("")
    if save is not None:
        save.write_text(proposal.patch.model_dump_json(indent=2) + "\n", encoding="utf-8")
        typer.echo(f"saved      {save} (apply it to a copy with 'healer patch {save}')")
        typer.echo("")
    if not run_tests:
        _echo_edits(proposal.patch)
        return
    try:
        runner = build_sandbox_runner(path, settings, timeout_seconds=timeout)
        report = runner.run_tests(command or settings.test_command, patch=proposal.patch)
    except HealerError as exc:
        typer.echo(f"error: the proposal could not be tested: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _print_applied(proposal.patch, report.patch, show_purpose=False)
    _report_run(report, show_output=show_output, as_json=False, verdict=True)


@app.command()
def run(
    command: Annotated[
        str | None,
        typer.Argument(help="Test command; defaults to TEST_COMMAND ('python -m pytest')"),
    ] = None,
    path: WorkspaceOption = Path("."),
    error_number: Annotated[
        int, typer.Option("--error", "-e", min=1, help="Which diagnosed error to fix")
    ] = 1,
    attempts: Annotated[
        int | None,
        typer.Option(
            "--attempts", min=1, max=10, help="Most patches to try; defaults to REPAIR_MAX_ATTEMPTS"
        ),
    ] = None,
    save: Annotated[
        Path | None,
        typer.Option("--save", help="Write the patch that fixed the tests to this file"),
    ] = None,
    timeout: Annotated[
        float | None, typer.Option("--timeout", min=1, help="Seconds before each run is killed")
    ] = None,
    use_cache: Annotated[
        bool,
        typer.Option("--cache/--no-cache", help="Try a solution stored for this workspace first"),
    ] = True,
    use_team: Annotated[
        bool,
        typer.Option("--team/--no-team", help="Offer the team's fixes before asking Claude"),
    ] = True,
    apply: ApplyOption = None,
    share: Annotated[
        bool | None,
        typer.Option(
            "--share/--no-share",
            help="Send the fix to the team once applied; defaults to SHARE_SOLUTIONS",
        ),
    ] = None,
) -> None:
    """Run the tests on a copy and, if they fail, find a patch that makes them pass.

    A solution already verified here for the same error is tried first, for free. Next,
    the team's suggestions for this error are tried: in the test sandbox at once, and
    without it only if you say so, because testing one executes code written on another
    machine. Otherwise Claude is asked
    for patches, and told why the previous ones failed, until the tests pass or the
    attempt or token limit is reached. Each patch is tested on an isolated copy. The fix
    is then shown and applied to your files only if you accept it. With sharing on, an
    applied fix is also sent to the team's other nodes.
    """
    settings = Settings()
    limit = attempts or settings.repair_max_attempts
    try:
        code_index = build_code_index(path, settings)
        code_index.index()
        pipeline = build_repair_pipeline(
            path, settings, code_index=code_index, timeout_seconds=timeout, max_attempts=limit
        )
        printer = _RepairPrinter(limit, settings.repair_max_tokens)
        result = pipeline.run(
            command or settings.test_command,
            error_number=error_number,
            use_cache=use_cache,
            listener=printer,
            approve_team=(
                partial(_approve_suggestion, isolated=settings.sandbox_socket is not None)
                if use_team
                else None
            ),
        )
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    sharing = settings.share_solutions if share is None else share
    fix = _report_repair(result, save)
    _tell_results(settings, result, enabled=sharing)
    if fix is None or fix.report is None or fix.report.patch is None:
        return
    from_team = fix.source is AttemptSource.TEAM
    if from_team and apply:
        typer.echo("team       --apply does not cover fixes from the team: confirm this one")
    applied = _offer_fix(
        path,
        settings,
        fix.proposal.patch,
        fix.report.patch,
        apply=None if from_team and apply else apply,
        saved=save,
    )
    if applied and result.solution is not None:
        _share(settings, result.solution, enabled=sharing)


def _approve_suggestion(match: TeamMatch, *, isolated: bool) -> bool:
    """Show a team suggestion and decide whether its code may run.

    In the test sandbox that code has no network, no secrets and no access to your files,
    so the suggestion is tested at once, as 6.2 of the plan asks. Without the sandbox the
    copy keeps a wrong patch away from your files but does not contain hostile code, so
    every edit is shown and the developer is asked first. Either way, writing the fix to
    your files needs its own yes.
    """
    solution = match.received.solution
    typer.echo("")
    typer.echo(
        f"team       suggestion from {_printable(solution.node)}, "
        f"received {match.received.received_at:%Y-%m-%d %H:%M}"
    )
    match_kind = "same signature" if match.same_signature else "similar error"
    typer.echo(f"fixes      {_printable(solution.error)} ({match_kind})")
    if match.worked or match.failed:
        typer.echo(f"results    {_results(match.worked, match.failed)}")
    typer.echo(f"proposal   {_printable(solution.explanation)}")
    if isolated:
        typer.echo("sandbox    testing it in the test sandbox, with no network, secrets or files")
        return True
    typer.echo("")
    _echo_edits(solution.patch)
    return _confirm(
        "test this suggestion on a copy? Its code will run on this machine with your access"
    )


class _RepairPrinter:
    """Prints each step of the loop as soon as it finishes."""

    def __init__(self, max_attempts: int, max_tokens: int) -> None:
        self._limits = f"{_plural(max_attempts, 'attempt')}, {max_tokens} tokens"

    def reproduced(self, baseline: RunReport, target: Diagnosis | None) -> None:
        result = baseline.result
        status = "passed" if result.passed else _status(result.exit_code)
        typer.echo(f"tests      {status} in {result.duration_seconds:.1f}s, on an isolated copy")
        if target is None:
            return
        origin = target.origin
        where = (
            f"  ({_printable(str(origin.workspace_file))}:{origin.line})"
            if origin is not None
            else ""
        )
        typer.echo(f"error      {_printable(target.error.summary)}{where}")
        typer.echo(f"limits     {self._limits}")

    def attempted(self, attempt: Attempt) -> None:
        proposal = attempt.proposal
        typer.echo("")
        if attempt.source is AttemptSource.CACHE:
            typer.echo(
                f"attempt {attempt.number}  local cache, 0 tokens "
                f"(found by {proposal.model} for {attempt.saved_tokens} tokens)"
            )
        elif attempt.source is AttemptSource.TEAM:
            typer.echo(
                f"attempt {attempt.number}  suggestion from {_printable(attempt.origin)}, 0 tokens"
            )
        else:
            typer.echo(f"attempt {attempt.number}  {proposal.model}, {_tokens(proposal.usage)}")
        if attempt.source is not AttemptSource.TEAM:
            typer.echo(f"proposal   {proposal.explanation}")
        report = attempt.report
        if report is not None and report.patch is not None:
            applied = report.patch
            typer.echo(
                f"patch      {_plural(len(applied.files), 'file')} "
                f"(+{applied.lines_added} -{applied.lines_removed})"
            )
        typer.echo(f"result     {_printable(_attempt_result(attempt))}")
        if attempt.outcome is AttemptOutcome.PASSED:
            return
        if attempt.source is AttemptSource.CACHE:
            typer.echo("cache      the stored solution no longer works and was discarded")
        elif attempt.source is AttemptSource.TEAM:
            typer.echo("team       this suggestion does not work here and is not recommended")


def _attempt_result(attempt: Attempt) -> str:
    if attempt.outcome is AttemptOutcome.PASSED:
        return "the tests pass"
    if attempt.outcome is AttemptOutcome.REJECTED:
        return f"rejected: {attempt.problem}"
    if attempt.outcome is AttemptOutcome.REPEATED:
        return f"{attempt.problem}; not tested again"
    report = attempt.report
    if report is None or report.result.timed_out:
        return "the tests timed out"
    if not report.diagnoses:
        return f"the tests still fail ({_status(report.result.exit_code)})"
    more = f" and {_plural(len(report.diagnoses) - 1, 'more error')}"
    extra = more if len(report.diagnoses) > 1 else ""
    return f"the tests still fail: {report.diagnoses[0].error.summary}{extra}"


def _report_repair(result: RepairResult, save: Path | None) -> Attempt | None:
    """Print how the loop ended; return the fix, or None when there is nothing to fix."""
    typer.echo("")
    if result.status is RepairStatus.PASSING:
        typer.echo("verdict    nothing to fix: the tests already pass")
        return None
    if result.status is RepairStatus.UNDIAGNOSED:
        typer.echo(f"verdict    cannot fix: {result.detail}")
        raise typer.Exit(code=1)
    spent = f"{_plural(len(result.attempts), 'attempt')}, {_tokens(result.usage)}"
    fix = result.fix
    if fix is None:
        if result.status is RepairStatus.STOPPED:
            typer.echo(f"error: {_printable(result.detail)}", err=True)
            if result.attempts:
                typer.echo(f"spent      {spent}")
            raise typer.Exit(code=2)
        typer.echo(f"verdict    not fixed: {result.detail}")
        typer.echo(f"spent      {spent}")
        raise typer.Exit(code=1)
    if fix.source is AttemptSource.CACHE:
        typer.echo("verdict    fixed: the tests pass with a solution from the local cache")
        typer.echo(f"spent      {spent}; saved about {result.tokens_saved} tokens")
    elif fix.source is AttemptSource.TEAM:
        typer.echo(
            f"verdict    fixed: the tests pass here with the fix from {_printable(fix.origin)}"
        )
        typer.echo(f"spent      {spent}; saved about {result.tokens_saved} tokens")
    else:
        typer.echo(f"verdict    fixed: the tests pass with the patch of attempt {fix.number}")
        typer.echo(f"spent      {spent}")
    if result.stored:
        typer.echo("cache      stored: the same error will be fixed without Claude next time")
    if save is not None:
        save.write_text(fix.proposal.patch.model_dump_json(indent=2) + "\n", encoding="utf-8")
        typer.echo(f"saved      {save}")
    if fix.report is not None and fix.report.patch is not None:
        applied = fix.report.patch
        typer.echo("")
        typer.echo(
            f"recommended patch: {_plural(len(applied.files), 'file')} "
            f"(+{applied.lines_added} -{applied.lines_removed}), tested on a copy"
        )
        typer.echo("")
        _echo_diff(applied.diff)
        typer.echo("")
    return fix


def _offer_fix(
    path: Path,
    settings: Settings,
    patch: Patch,
    tested: AppliedPatch,
    *,
    apply: bool | None,
    saved: Path | None,
) -> bool:
    """Write the tested patch to the workspace if the developer accepts it; True if written."""
    try:
        installer = build_fix_installer(path, settings)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    accepted = apply
    if accepted is None:
        accepted = _confirm(f"apply this patch to your files in {installer.workspace}?")
    if not accepted:
        later = (
            f"apply it later with 'healer apply {saved}'"
            if saved is not None
            else "save it with --save FILE to apply it later with 'healer apply FILE'"
        )
        typer.echo(f"skipped    your files were not changed; {later}")
        return False
    try:
        applied = installer.install(patch, tested)
    except HealerError as exc:
        typer.echo(f"error: not applied, your files were not changed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"applied    {_plural(len(applied.files), 'file')} changed in your workspace "
        f"(+{applied.lines_added} -{applied.lines_removed})"
    )
    return True


def _share(settings: Settings, solution: Solution, *, enabled: bool) -> None:
    """Send an applied fix to the team. A failure is reported but never undoes the fix.

    A fix the broker does not take waits in the outbox. Once one is delivered, the broker
    is back, so the ones waiting are sent as well.
    """
    if not enabled:
        return
    try:
        sharer = build_solution_sharer(settings)
        typer.echo(
            f"sharing    sending the fix to the team through "
            f"{settings.mqtt_host}:{settings.mqtt_port}..."
        )
        receipt = sharer.share(solution)
    except HealerError as exc:
        typer.echo(f"warning: the fix was applied but not shared: {exc}", err=True)
        return
    if not receipt.delivered:
        typer.echo(f"warning: the fix was applied but not sent yet: {receipt.failure}", err=True)
        typer.echo(
            "waiting    kept in the outbox: 'healer listen' sends it when the broker is back, "
            "or send it with 'healer outbox --send'"
        )
        return
    hidden = f", {_plural(receipt.hidden, 'secret')} hidden" if receipt.hidden else ""
    typer.echo(
        f"shared     sent to the team as node {receipt.solution.node} on "
        f"{settings.mqtt_topic} ({receipt.size} bytes{hidden})"
    )
    try:
        _echo_resend(sharer.resend())
    except HealerError as exc:
        typer.echo(f"warning: the fixes waiting in the outbox were not sent: {exc}", err=True)


def _tell_results(settings: Settings, result: RepairResult, *, enabled: bool) -> None:
    """Keep what this run learned about the team's fixes, and tell the team with sharing
    on (ADR 0022). A result says which fix worked or failed here, never code. A failure is
    reported and changes nothing else."""
    found = results_of(result.attempts, node=settings.node_id, at=datetime.now(UTC))
    if not found:
        return
    origins = {attempt.fix: attempt.origin for attempt in result.attempts if attempt.fix}
    try:
        store = build_results(settings)
        for item in found:
            store.record(item)
    except HealerError as exc:
        typer.echo(f"warning: the results of the team's fixes were not kept: {exc}", err=True)
    sharer: SolutionSharer | None = None
    if enabled:
        try:
            sharer = build_solution_sharer(settings)
        except HealerError as exc:
            typer.echo(f"warning: the team was not told the results: {exc}", err=True)
    for item in found:
        line = (
            f"result     the fix from {_printable(origins[item.fix_id])} "
            f"{'worked' if item.worked else 'failed'} here"
        )
        if sharer is None:
            typer.echo(line)
            continue
        try:
            receipt = sharer.report(item)
        except HealerError as exc:
            typer.echo(line)
            typer.echo(f"warning: the team was not told this result: {exc}", err=True)
            continue
        told = "the team was told" if receipt.delivered else "it waits in the outbox"
        typer.echo(f"{line}; {told}")


def _results(worked: int, failed: int) -> str:
    """For example ``worked on 2 nodes, failed on 1 node``, leaving out a zero."""
    parts = [f"worked on {_plural(worked, 'node')}"] if worked else []
    if failed:
        parts.append(f"failed on {_plural(failed, 'node')}")
    return ", ".join(parts)


def _waiting(fixes: int, results: int) -> str:
    """For example ``1 fix and 2 results``; empty when nothing waits."""
    parts = [_plural(fixes, "fix", "fixes")] if fixes else []
    if results:
        parts.append(_plural(results, "result"))
    return " and ".join(parts)


def _echo_resend(report: ResendReport) -> None:
    if report.sent:
        typer.echo(f"resent     {_plural(report.sent, 'fix', 'fixes')} waiting in the outbox")
    if report.results_sent:
        typer.echo(f"resent     {_plural(report.results_sent, 'result')} waiting in the outbox")
    if report.dropped:
        typer.echo(
            f"dropped    {_plural(report.dropped, 'fix', 'fixes')} over MAX_SHARE_BYTES, "
            "which could never be sent"
        )
    if report.error is not None:
        typer.echo(
            f"waiting    {_waiting(report.left, report.results_left) or 'nothing'} still in "
            f"the outbox: {_printable(report.error)}"
        )


def _confirm(question: str) -> bool:
    """Ask yes or no, defaulting to no; no answer (closed input) also means no."""
    try:
        return typer.confirm(question, default=False)
    except typer.Abort:
        typer.echo("")
        return False


_DIFF_COLORS = (
    ("+++", typer.colors.BRIGHT_WHITE),
    ("---", typer.colors.BRIGHT_WHITE),
    ("@@", typer.colors.CYAN),
    ("+", typer.colors.GREEN),
    ("-", typer.colors.RED),
)


def _echo_diff(diff: str) -> None:
    """Print a unified diff, in color when the terminal supports it.

    Control characters are masked: the developer decides from this text, and a patch must
    not be able to hide lines from it.
    """
    for line in _lines(diff.rstrip()):
        color = next((c for prefix, c in _DIFF_COLORS if line.startswith(prefix)), None)
        typer.secho(line, fg=color, bold=line.startswith(("+++", "---")))


def _echo_edits(patch: Patch) -> None:
    """Print a patch that has not been applied, so there is no diff yet."""
    for edit in patch.edits:
        path = _printable(edit.path)
        typer.echo(f"--- {path}" if edit.old else f"+++ {path} (new file)")
        for line in _lines(edit.old):
            typer.echo(f"- {line}")
        for line in _lines(edit.new):
            typer.echo(f"+ {line}")
        typer.echo("")


def _lines(text: str) -> list[str]:
    """The lines of ``text`` as they can be shown; only a real newline ends a line."""
    if not text:
        return []
    return [_printable(line.removesuffix("\r")) for line in text.removesuffix("\n").split("\n")]


def _tokens(usage: TokenUsage) -> str:
    cached = f" ({usage.cache_read_tokens} from cache)" if usage.cache_read_tokens else ""
    return f"{usage.input_total} tokens in{cached}, {usage.output_tokens} out"


def _status(exit_code: int | None) -> str:
    if exit_code is None:
        return "timed out"
    return f"{_PYTEST_EXIT_CODES.get(exit_code, 'failed')} (exit {exit_code})"


def _print_applied(patch: Patch, applied: AppliedPatch | None, *, show_purpose: bool) -> None:
    if applied is None:
        return
    created = f", {len(applied.created)} new" if applied.created else ""
    typer.echo(
        f"patch      {_plural(len(patch.edits), 'edit')} in "
        f"{_plural(len(applied.files), 'file')}{created} "
        f"(+{applied.lines_added} -{applied.lines_removed}), applied to the copy only"
    )
    if show_purpose and patch.description:
        typer.echo(f"purpose    {patch.description}")
    typer.echo("")
    _echo_diff(applied.diff)
    typer.echo("")


def _plural(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {plural or noun + 's'}"


def _report_run(
    report: RunReport, *, show_output: bool, as_json: bool, verdict: bool = False
) -> None:
    result = report.result
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
        raise typer.Exit(code=0 if result.passed else 1)
    _print_run(report, show_output=show_output)
    if verdict:
        outcome = "the tests pass with this patch" if result.passed else "the tests still fail"
        typer.echo(f"verdict    {outcome}")
    typer.echo("")
    for number, diagnosis in enumerate(report.diagnoses, start=1):
        _print_diagnosis(number, diagnosis)
    raise typer.Exit(code=0 if result.passed else 1)


def _print_run(report: RunReport, *, show_output: bool) -> None:
    result = report.result
    typer.echo(f"sandbox    {report.files_copied} files copied to an isolated copy, then deleted")
    typer.echo(f"command    {' '.join(result.command)}")
    typer.echo(f"result     {_status(result.exit_code)} in {result.duration_seconds:.1f}s")
    if result.output_truncated:
        typer.echo("output     truncated: only the last part was kept")
    if show_output:
        typer.echo("")
        for line in _lines(result.output.rstrip()):
            typer.echo(line)


@app.command(name="apply")
def apply_command(
    patch_file: Annotated[
        Path, typer.Argument(help="JSON patch, for example one saved with 'healer run --save'")
    ],
    command: Annotated[
        str | None,
        typer.Argument(help="Test command; defaults to TEST_COMMAND ('python -m pytest')"),
    ] = None,
    path: WorkspaceOption = Path("."),
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Apply without asking once the tests pass")
    ] = False,
    timeout: Annotated[
        float | None, typer.Option("--timeout", min=1, help="Seconds before the run is killed")
    ] = None,
    show_output: Annotated[
        bool, typer.Option("--output", help="Also print the captured test output")
    ] = False,
) -> None:
    """Test a patch on a copy and, if the tests pass and you accept it, apply it to your files.

    Your files are written only if every file the patch touches is still the one that was
    tested; otherwise nothing is changed.
    """
    settings = Settings()
    try:
        proposal = parse_patch(read_log(None if str(patch_file) == "-" else patch_file))
        runner = build_sandbox_runner(path, settings, timeout_seconds=timeout)
        report = runner.run_tests(command or settings.test_command, patch=proposal)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _print_applied(proposal, report.patch, show_purpose=True)
    _print_run(report, show_output=show_output)
    if not report.result.passed or report.patch is None:
        typer.echo("verdict    the tests still fail with this patch; your files were not changed")
        typer.echo("")
        for number, diagnosis in enumerate(report.diagnoses, start=1):
            _print_diagnosis(number, diagnosis)
        raise typer.Exit(code=1)
    typer.echo("verdict    the tests pass with this patch")
    typer.echo("")
    saved = None if str(patch_file) == "-" else patch_file
    _offer_fix(path, settings, proposal, report.patch, apply=True if yes else None, saved=saved)


class Grouping(StrEnum):
    DAY = "day"
    PROJECT = "project"


@app.command()
def savings(
    by: Annotated[Grouping, typer.Option("--by", help="One row per day (UTC) or project")] = (
        Grouping.DAY
    ),
    days: Annotated[
        int | None, typer.Option("--days", min=1, help="Only the incidents of the last N days")
    ] = None,
) -> None:
    """Show the tokens spent fixing errors and the tokens saved by reusing solutions."""
    since = datetime.now(UTC) - timedelta(days=days) if days is not None else None
    try:
        entries = build_ledger(Settings()).entries(since)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not entries:
        typer.echo("no incidents recorded yet; 'healer run' records them")
        return
    rows = [*summarize(entries, by.value), total(entries)]
    width = max(len(by.value), *(len(row.key) for row in rows))
    typer.echo(
        f"{by.value:<{width}}  incidents  fixed  from cache  from team  model calls  "
        "tokens used  tokens saved"
    )
    for row in rows:
        if row.key == "total":
            typer.echo("")
        typer.echo(_savings_row(row, width))
    spent_and_saved = rows[-1].tokens_used + rows[-1].tokens_saved
    if rows[-1].tokens_saved and spent_and_saved:
        share = round(100 * rows[-1].tokens_saved / spent_and_saved)
        typer.echo(f"\nreuse avoided {share}% of the tokens these incidents would have cost")


def _savings_row(row: LedgerSummary, width: int) -> str:
    return (
        f"{row.key:<{width}}  {row.incidents:>9}  {row.fixed:>5}  {row.from_cache:>10}  "
        f"{row.from_team:>9}  {row.model_attempts:>11}  {row.tokens_used:>11}  "
        f"{row.tokens_saved:>12}"
    )


@app.command()
def redact(
    source: Annotated[
        Path | None, typer.Argument(help="File to redact; reads stdin when omitted")
    ] = None,
) -> None:
    """Print the text with secrets replaced by [REDACTED:<kind>] placeholders."""
    try:
        text = read_log(None if source is None or str(source) == "-" else source)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    result = Redactor().redact(text)
    typer.echo(result.text, nl=False)
    if result.total:
        details = ", ".join(f"{kind} {n}" for kind, n in sorted(result.counts.items()))
        typer.echo(f"redacted {result.total} secrets: {details}", err=True)
    else:
        typer.echo("no secrets found", err=True)


@app.command()
def listen() -> None:
    """Receive the fixes the team shares and keep them as suggestions, until stopped.

    Each message must carry a valid team signature, follow the expected format and hold a
    safe patch; anything else is refused. Nothing is ever applied or run. A node with a
    fixed NODE_ID also receives what was sent while it was off.

    With SHARE_SOLUTIONS on, it also sends this node's fixes waiting in the outbox: right
    after each connection, and every OUTBOX_RETRY_SECONDS while connected.
    """
    settings = Settings()
    resender: _Resender | None = None
    try:
        ingester = build_solution_ingester(settings)
        subscriber = build_subscriber(settings)
        if settings.share_solutions:
            resender = _Resender(build_solution_sharer(settings), settings.outbox_retry_seconds)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if "node_id" not in settings.model_fields_set:
        typer.echo(
            "warning: NODE_ID is not set; what is sent while this node is off may be lost",
            err=True,
        )
    monitor = ListenerMonitor(
        build_listener_status(settings),
        node=settings.node_id,
        topic=settings.mqtt_topic,
        warn=lambda line: typer.echo(f"warning: {line}", err=True),
    )
    typer.echo(f"listening  as node {settings.node_id} on {settings.mqtt_topic}; stop with Ctrl+C")

    def on_message(data: bytes) -> None:
        try:
            ingested = ingester.ingest(data)
        except RejectedMessageError as exc:
            monitor.count("rejected", _printable(str(exc)))
            typer.echo(f"rejected   {_printable(str(exc))}")
        except HealerError as exc:
            typer.echo(f"warning: a valid message could not be stored: {exc}", err=True)
        else:
            if isinstance(ingested, ResultIngested):
                counted = ingested.outcome is ResultOutcome.COUNTED
                line = _result_line(ingested)
                monitor.count("result" if counted else "ignored", line.split(maxsplit=1)[1])
            else:
                kept = ingested.outcome in (IngestOutcome.NEW, IngestOutcome.REPLACED)
                line = _ingested_line(ingested)
                monitor.count("received" if kept else "ignored", line.split(maxsplit=1)[1])
            typer.echo(line)

    def on_status(status: str) -> None:
        typer.echo(f"broker     {status}")
        monitor.connection(status)
        if resender is not None:
            resender.status(status)

    previous = signal.signal(signal.SIGTERM, _interrupt)
    monitor.start()
    if resender is not None:
        resender.start()
    try:
        subscriber.run(on_message, on_status)
    except KeyboardInterrupt:
        typer.echo("stopped")
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        if resender is not None:
            resender.stop()
        monitor.stop()
        signal.signal(signal.SIGTERM, previous)


def _interrupt(*_: object) -> None:
    raise KeyboardInterrupt


class _Resender:
    """Sends the outbox from a thread of its own, so receiving never waits for it: once
    after each connection, and every ``interval`` seconds while connected."""

    def __init__(self, sharer: SolutionSharer, interval: float) -> None:
        self._sharer = sharer
        self._interval = interval
        self._wake = threading.Event()
        self._connected = threading.Event()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="healer-resender", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def status(self, status: str) -> None:
        if status.startswith("connected"):
            self._connected.set()
            self._wake.set()
        else:
            self._connected.clear()

    def stop(self) -> None:
        """Start no new pass, and give the one under way a moment to finish."""
        self._stopping.set()
        self._wake.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stopping.is_set():
                return
            if not self._connected.is_set():
                continue
            try:
                _echo_resend(self._sharer.resend())
            except HealerError as exc:
                typer.echo(
                    f"warning: the fixes waiting in the outbox were not sent: {exc}", err=True
                )


def _ingested_line(ingested: Ingested) -> str:
    solution = ingested.solution
    summary = (
        f"{_printable(solution.error)} from {_printable(solution.node)} "
        f"({_plural(len(solution.patch.files), 'file')})"
    )
    if ingested.outcome is IngestOutcome.NEW:
        return f"received   {summary}, kept as a suggestion"
    if ingested.outcome is IngestOutcome.REPLACED:
        return f"received   {summary}, kept; the one ranked last for this error made room"
    if ingested.outcome is IngestOutcome.DUPLICATE:
        return f"ignored    {summary}: already known"
    if ingested.outcome is IngestOutcome.OUTRANKED:
        return f"ignored    {summary}: ranks below every suggestion kept for this error"
    return "ignored    sent by this node"


def _result_line(ingested: ResultIngested) -> str:
    result = ingested.result
    node = _printable(result.node)
    fix = ingested.fix
    if ingested.outcome is ResultOutcome.COUNTED and fix is not None:
        return (
            f"result     {node}: the fix from {_printable(fix.node)} for "
            f"{_printable(fix.error)} {'worked' if result.worked else 'failed'} there"
        )
    if ingested.outcome is ResultOutcome.KNOWN:
        return f"ignored    result from {node}: already known"
    if ingested.outcome is ResultOutcome.UNKNOWN:
        return f"ignored    result from {node}: about a fix not kept here"
    return "ignored    sent by this node"


def _printable(text: str) -> str:
    """Text without control characters, which could fool the terminal. Tabs are kept."""
    return "".join(c if c.isprintable() or c == "\t" else "?" for c in text)


@app.command()
def outbox(
    send: Annotated[
        bool, typer.Option("--send", help="Send them now, if the broker takes them")
    ] = False,
    discard: Annotated[
        bool, typer.Option("--discard", help="Forget them: the team will not receive them")
    ] = False,
) -> None:
    """Show this node's accepted fixes, and the results of the team's fixes tested here,
    that the broker did not take and wait to be sent.

    'healer listen' sends them right after connecting and every OUTBOX_RETRY_SECONDS, and
    'healer run' once its next fix is delivered. Each one only goes to the topic it was
    meant for.
    """
    if send and discard:
        typer.echo("error: choose --send or --discard, not both", err=True)
        raise typer.Exit(code=2)
    settings = Settings()
    try:
        store = build_outbox(settings)
        waiting = store.pending()
        results = store.pending_results()
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not waiting and not results:
        typer.echo("nothing waiting; every shared fix and result reached the broker")
        return
    count = _waiting(len(waiting), len(results))
    if discard:
        if not _confirm(f"discard {count}? The team will not receive them"):
            typer.echo("kept       nothing was discarded")
            return
        store.clear()
        typer.echo(f"discarded  {count}")
        return
    if send:
        _send_outbox(settings, waiting, results)
        return
    typer.echo(f"{count} waiting to be sent, oldest first")
    for entry in waiting:
        solution = entry.solution
        topic = "" if entry.topic == settings.mqtt_topic else " (not the current MQTT_TOPIC)"
        typer.echo("")
        typer.echo(
            f"{solution.published_at:%Y-%m-%d %H:%M} UTC  for {_printable(entry.topic)}{topic}"
        )
        typer.echo(f"error      {_printable(solution.error)}")
        typer.echo(f"files      {_printable(', '.join(solution.patch.files))}")
        typer.echo(
            f"tried      {_plural(entry.attempts, 'time')}, last: {_printable(entry.last_error)}"
        )
    for pending in results:
        result = pending.result
        topic = "" if pending.topic == settings.mqtt_topic else " (not the current MQTT_TOPIC)"
        typer.echo("")
        typer.echo(
            f"{result.reported_at:%Y-%m-%d %H:%M} UTC  result for {_printable(pending.topic)}"
            f"{topic}"
        )
        typer.echo(
            f"result     fix {result.fix_id[:12]} {'worked' if result.worked else 'failed'} "
            f"here (error {result.signature[:12]})"
        )
        typer.echo(
            f"tried      {_plural(pending.attempts, 'time')}, last: "
            f"{_printable(pending.last_error)}"
        )


def _send_outbox(
    settings: Settings, waiting: list[PendingShare], results: list[PendingResult]
) -> None:
    try:
        sharer = build_solution_sharer(settings)
        typer.echo(
            f"sending    what waits for {settings.mqtt_topic} through "
            f"{settings.mqtt_host}:{settings.mqtt_port}..."
        )
        report = sharer.resend()
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _echo_resend(report)
    elsewhere = _waiting(
        sum(entry.topic != settings.mqtt_topic for entry in waiting),
        sum(entry.topic != settings.mqtt_topic for entry in results),
    )
    if elsewhere:
        typer.echo(
            f"kept       {elsewhere} for another topic, sent only when MQTT_TOPIC is that one again"
        )
    if report.error is not None:
        raise typer.Exit(code=1)


@app.command()
def team() -> None:
    """Show the fixes received from the team. 'healer run' offers them; none is applied alone."""
    settings = Settings()
    try:
        suggestions = build_team_solutions(settings).all()
        tallies = build_results(settings).tally(r.solution.fix_id for r in suggestions)
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if not suggestions:
        typer.echo("no suggestions yet; 'healer listen' receives them")
        return
    errors = len({received.solution.signature for received in suggestions})
    typer.echo(
        f"{_plural(len(suggestions), 'suggestion')} from the team for "
        f"{_plural(errors, 'error')}, newest first"
    )
    for received in suggestions:
        solution = received.solution
        typer.echo("")
        typer.echo(f"{received.received_at:%Y-%m-%d %H:%M}  from {_printable(solution.node)}")
        typer.echo(f"error      {_printable(solution.error)}")
        typer.echo(f"files      {_printable(', '.join(solution.patch.files))}")
        typer.echo(f"evidence   {_printable(solution.evidence.command)}")
        tally = tallies.get(solution.fix_id)
        if tally is not None:
            typer.echo(f"results    {_results(tally.worked, tally.failed)}")


@app.command()
def panel(
    host: Annotated[
        str, typer.Option(help="Address to listen on; the container uses 0.0.0.0")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on")] = 8765,
    allow_host: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-host",
            help="Also answer to this name; ./panel.sh dev uses it for a container name",
        ),
    ] = None,
) -> None:
    """Show this node in the browser: its health, savings, incidents and fixes. Read only.

    It answers only to localhost, so other machines and other web sites cannot read it.
    """
    try:
        application = build_panel_app(Settings(), extra_hosts=allow_host or [])
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"panel      serving on {host}:{port}; open http://localhost:{port}; stop with Ctrl+C"
    )
    uvicorn.run(
        application,
        host=host,
        port=port,
        lifespan="off",
        ws="none",
        access_log=False,
        server_header=False,
        proxy_headers=False,
        log_level="warning",
    )


if __name__ == "__main__":
    app()
