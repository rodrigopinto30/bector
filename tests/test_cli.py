from functools import partial
from pathlib import Path

import pytest
from typer.testing import CliRunner

from healer import cli
from healer.config import Settings
from healer.factory import build_code_index
from tests.conftest import FakeEmbeddingFunction

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert "healer" in result.stdout


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "stats.py").write_text(
        "def average(numbers):\n"
        '    """Compute the arithmetic mean."""\n'
        "    return sum(numbers) / len(numbers)\n"
    )
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(
        cli,
        "build_code_index",
        partial(build_code_index, embedding_function=FakeEmbeddingFunction()),
    )
    return project


@pytest.mark.integration
def test_index_search_and_map(workspace: Path) -> None:
    indexed = runner.invoke(cli.app, ["index", str(workspace)])
    assert indexed.exit_code == 0
    assert "indexed 1 files (2 symbols)" in indexed.stdout

    again = runner.invoke(cli.app, ["index", str(workspace)])
    assert "indexed 0 files" in again.stdout and "1 unchanged" in again.stdout

    found = runner.invoke(cli.app, ["search", "arithmetic mean", "--path", str(workspace)])
    assert found.exit_code == 0
    assert "stats.py:1-3  def average(numbers):" in found.stdout

    outline = runner.invoke(cli.app, ["map", "stats.py", "--path", str(workspace)])
    assert outline.exit_code == 0
    assert "def average(numbers):" in outline.stdout


@pytest.mark.integration
def test_search_before_indexing_reports_no_results(workspace: Path) -> None:
    result = runner.invoke(cli.app, ["search", "anything", "--path", str(workspace)])
    assert result.exit_code == 1
    assert "no results" in result.stdout


@pytest.mark.integration
def test_map_of_unknown_file_fails(workspace: Path) -> None:
    result = runner.invoke(cli.app, ["map", "nope.py", "--path", str(workspace)])
    assert result.exit_code == 1


def test_index_of_missing_directory_reports_error(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["index", str(tmp_path / "missing")])
    assert result.exit_code == 2
    assert "not a directory" in result.output


def test_settings_ignore_unrelated_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSPACE_PATH", "./workspace")
    monkeypatch.setenv("MQTT_PORT", "1884")
    assert Settings().mqtt_port == 1884
