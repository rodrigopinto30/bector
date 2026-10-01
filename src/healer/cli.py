import importlib
import os
from pathlib import Path
from typing import Annotated

import typer

from healer import __version__
from healer.config import Settings
from healer.domain.errors import HealerError
from healer.factory import build_code_index

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
    """Run a command and remediate failures (Phases 3-4)."""
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
def listen() -> None:
    """Start the MQTT listener that syncs shared solutions (Phase 5)."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
