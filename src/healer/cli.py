import importlib
import json
import os
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from healer import __version__
from healer.adapters.log_reader import read_log
from healer.config import Settings
from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import HealerError
from healer.domain.execution import RunReport
from healer.domain.ledger import LedgerSummary, summarize, total
from healer.domain.patch import AppliedPatch, Patch, parse_patch
from healer.domain.proposal import SYSTEM_PROMPT, TokenUsage, render_request
from healer.domain.redaction import Redactor
from healer.domain.repair import (
    Attempt,
    AttemptOutcome,
    AttemptSource,
    RepairResult,
    RepairStatus,
)
from healer.factory import (
    build_code_index,
    build_diagnoser,
    build_fix_installer,
    build_fix_proposer,
    build_ledger,
    build_repair_pipeline,
    build_sandbox_runner,
)

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
    host = os.environ.get("MQTT_HOST", "localhost")
    port = os.environ.get("MQTT_PORT", "1883")
    typer.echo(f"[info] MQTT broker: {host}:{port}")
    raise typer.Exit(code=1 if failed else 0)


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
    in_workspace = sum(1 for f in d.error.frames if f.workspace_file)
    typer.echo(f"[{number}] {d.error.summary}")
    typer.echo(f"    signature  {d.signature[:12]}")
    if d.origin is None:
        typer.echo("    origin     no frame inside the workspace")
    else:
        where = f" in {d.origin.function}" if d.origin.function else ""
        typer.echo(f"    origin     {d.origin.workspace_file}:{d.origin.line}{where}")
        if d.origin.code:
            typer.echo(f"    code       {d.origin.code}")
        if d.location is None:
            typer.echo("    symbol     not indexed (run 'healer index')")
        else:
            s = d.location
            label = s.signature or f"module {s.name}"
            typer.echo(f"    symbol     {label}  ({s.file}:{s.start_line}-{s.end_line})")
    typer.echo(f"    frames     {len(d.error.frames)} ({in_workspace} in workspace)")
    for cause in d.error.causes:
        typer.echo(f"    caused by  {cause}")
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
        for edit in proposal.patch.edits:
            typer.echo(f"--- {edit.path}" if edit.old else f"+++ {edit.path} (new file)")
            for line in edit.old.splitlines():
                typer.echo(f"- {line}")
            for line in edit.new.splitlines():
                typer.echo(f"+ {line}")
            typer.echo("")
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
        typer.Option("--cache/--no-cache", help="Try a stored solution before asking Claude"),
    ] = True,
    apply: ApplyOption = None,
) -> None:
    """Run the tests on a copy and, if they fail, find a patch that makes them pass.

    A solution already verified here for the same error is tried first, for free.
    Otherwise Claude is asked for patches, and told why the previous ones failed, until
    the tests pass or the attempt or token limit is reached. Each patch is tested on an
    isolated copy. The fix is then shown and applied to your files only if you accept it.
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
        )
    except HealerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    fix = _report_repair(result, save)
    if fix is not None and fix.report is not None and fix.report.patch is not None:
        _offer_fix(path, settings, fix.proposal.patch, fix.report.patch, apply=apply, saved=save)


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
        where = f"  ({origin.workspace_file}:{origin.line})" if origin is not None else ""
        typer.echo(f"error      {target.error.summary}{where}")
        typer.echo(f"limits     {self._limits}")

    def attempted(self, attempt: Attempt) -> None:
        proposal = attempt.proposal
        typer.echo("")
        if attempt.source is AttemptSource.CACHE:
            typer.echo(
                f"attempt {attempt.number}  local cache, 0 tokens "
                f"(found by {proposal.model} for {attempt.saved_tokens} tokens)"
            )
        else:
            typer.echo(f"attempt {attempt.number}  {proposal.model}, {_tokens(proposal.usage)}")
        typer.echo(f"proposal   {proposal.explanation}")
        report = attempt.report
        if report is not None and report.patch is not None:
            applied = report.patch
            typer.echo(
                f"patch      {_plural(len(applied.files), 'file')} "
                f"(+{applied.lines_added} -{applied.lines_removed})"
            )
        typer.echo(f"result     {_attempt_result(attempt)}")
        if attempt.source is AttemptSource.CACHE and attempt.outcome is not AttemptOutcome.PASSED:
            typer.echo("cache      the stored solution no longer works and was discarded")


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
            typer.echo(f"error: {result.detail}", err=True)
            if result.attempts:
                typer.echo(f"spent      {spent}")
            raise typer.Exit(code=2)
        typer.echo(f"verdict    not fixed: {result.detail}")
        typer.echo(f"spent      {spent}")
        raise typer.Exit(code=1)
    if fix.source is AttemptSource.CACHE:
        typer.echo("verdict    fixed: the tests pass with a solution from the local cache")
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
) -> None:
    """Write the tested patch to the workspace if the developer accepts it."""
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
        return
    try:
        applied = installer.install(patch, tested)
    except HealerError as exc:
        typer.echo(f"error: not applied, your files were not changed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(
        f"applied    {_plural(len(applied.files), 'file')} changed in your workspace "
        f"(+{applied.lines_added} -{applied.lines_removed})"
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
    """Print a unified diff, in color when the terminal supports it."""
    for line in diff.rstrip().splitlines():
        color = next((c for prefix, c in _DIFF_COLORS if line.startswith(prefix)), None)
        typer.secho(line, fg=color, bold=line.startswith(("+++", "---")))


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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


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
        typer.echo(result.output.rstrip())


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
        f"{by.value:<{width}}  incidents  fixed  from cache  model calls  tokens used  tokens saved"
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
        f"{row.model_attempts:>11}  {row.tokens_used:>11}  {row.tokens_saved:>12}"
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
    """Start the MQTT listener that syncs shared solutions (Phase 5)."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
