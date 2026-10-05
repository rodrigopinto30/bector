import json
import shutil
from functools import partial
from pathlib import Path

import pytest
from typer.testing import CliRunner

from healer import cli
from healer.config import Settings
from healer.factory import build_code_index, build_diagnoser
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


SAMPLE = Path(__file__).parent / "fixtures" / "sample_project"
TRACES = Path(__file__).parent / "fixtures" / "traces"


@pytest.fixture
def sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "sample"
    shutil.copytree(SAMPLE, project)
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    fake = FakeEmbeddingFunction()
    monkeypatch.setattr(cli, "build_code_index", partial(build_code_index, embedding_function=fake))
    monkeypatch.setattr(cli, "build_diagnoser", partial(build_diagnoser, embedding_function=fake))
    return project


@pytest.mark.integration
def test_diagnose_groups_the_same_bug_and_locates_it(sample: Path) -> None:
    runner.invoke(cli.app, ["index", str(sample)])
    log = (TRACES / "script_key.txt").read_text() + (TRACES / "pytest_long.txt").read_text()
    result = runner.invoke(cli.app, ["diagnose", "--path", str(sample)], input=log)
    assert result.exit_code == 0
    assert "[1] KeyError: 'db'" in result.stdout
    assert "origin     app/cfg.py:2 in read_db" in result.stdout
    assert "symbol     def read_db(config):  (app/cfg.py:1-2)" in result.stdout
    assert "seen       2 times" in result.stdout
    assert "[2] AssertionError: assert 2 == 3" in result.stdout


@pytest.mark.integration
def test_diagnose_json_output(sample: Path) -> None:
    result = runner.invoke(
        cli.app, ["diagnose", str(TRACES / "script_chain.txt"), "--path", str(sample), "--json"]
    )
    assert result.exit_code == 0
    [diagnosis] = json.loads(result.stdout)
    assert diagnosis["error"]["error_type"] == "RuntimeError"
    assert diagnosis["origin"]["workspace_file"] == "app/cfg.py"
    assert diagnosis["location"] is None
    assert len(diagnosis["signature"]) == 64


@pytest.mark.integration
def test_diagnose_without_errors_exits_1(sample: Path) -> None:
    result = runner.invoke(cli.app, ["diagnose", "--path", str(sample)], input="3 passed\n")
    assert result.exit_code == 1
    assert "no errors found" in result.output


def test_diagnose_missing_log_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["diagnose", str(tmp_path / "nope.log"), "-p", str(tmp_path)])
    assert result.exit_code == 2
    assert "Cannot read log" in result.output
