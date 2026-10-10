import importlib
import json
import os
from pathlib import Path
from typing import Annotated

import typer

from healer import __version__
from healer.adapters.log_reader import read_log
from healer.config import Settings
from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import HealerError
from healer.domain.execution import RunReport
from healer.domain.patch import AppliedPatch, Patch, parse_patch
from healer.domain.proposal import SYSTEM_PROMPT, render_request
from healer.domain.redaction import Redactor
from healer.factory import (
    build_code_index,
    build_diagnoser,
    build_fix_proposer,
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


@app.command()
def run(command: str = typer.Argument(..., help="Test command to run, e.g. 'pytest'")) -> None:
    """Run a command and remediate failures (Phase 4)."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(code=1)


WorkspaceOption = Annotated[
    Path, typer.Option("--path", "-p", help="Workspace root that was indexed")
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
    usage = proposal.usage
    cached = f" ({usage.cache_read_tokens} from cache)" if usage.cache_read_tokens else ""
    typer.echo(
        f"model      {proposal.model}, {usage.input_total} tokens in{cached}, "
        f"{usage.output_tokens} out"
    )
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
    typer.echo(applied.diff.rstrip())
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
    if result.exit_code is None:
        status = "timed out"
    else:
        label = _PYTEST_EXIT_CODES.get(result.exit_code, "failed")
        status = f"{label} (exit {result.exit_code})"
    typer.echo(f"sandbox    {report.files_copied} files copied to an isolated copy, then deleted")
    typer.echo(f"command    {' '.join(result.command)}")
    typer.echo(f"result     {status} in {result.duration_seconds:.1f}s")
    if result.output_truncated:
        typer.echo("output     truncated: only the last part was kept")
    if show_output:
        typer.echo("")
        typer.echo(result.output.rstrip())
    if verdict:
        outcome = "the tests pass with this patch" if result.passed else "the tests still fail"
        typer.echo(f"verdict    {outcome}")
    typer.echo("")
    for number, diagnosis in enumerate(report.diagnoses, start=1):
        _print_diagnosis(number, diagnosis)
    raise typer.Exit(code=0 if result.passed else 1)


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
