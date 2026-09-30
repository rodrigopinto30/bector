import importlib
import os

import typer

from healer import __version__

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
    typer.echo(f"[info] MQTT broker: {os.environ.get('MQTT_HOST', 'localhost')}:{os.environ.get('MQTT_PORT', '1883')}")
    raise typer.Exit(code=1 if failed else 0)


@app.command()
def run(command: str = typer.Argument(..., help="Test command to run, e.g. 'pytest'")) -> None:
    """Run a command and remediate failures (Phases 3-4)."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(code=1)


@app.command()
def index(path: str = typer.Argument(".", help="Repository path to index")) -> None:
    """Index the repository into the local vector database (Phase 2)."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(code=1)


@app.command()
def listen() -> None:
    """Start the MQTT listener that syncs shared solutions (Phase 5)."""
    typer.echo("Not implemented yet.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
