import json
import shutil
from functools import partial
from pathlib import Path

import pytest
from typer.testing import CliRunner

from healer import cli
from healer.config import Settings
from healer.domain.patch import FileEdit, Patch, parse_patch
from healer.domain.proposal import Proposal, TokenUsage
from healer.factory import (
    build_code_index,
    build_diagnoser,
    build_fix_proposer,
    build_sandbox_runner,
)
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
    monkeypatch.setattr(
        cli, "build_sandbox_runner", partial(build_sandbox_runner, embedding_function=fake)
    )
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
    assert "Cannot read" in result.output


@pytest.mark.integration
def test_test_command_runs_in_a_sandbox_and_diagnoses(sample: Path) -> None:
    before = sorted(p.relative_to(sample) for p in sample.rglob("*"))
    result = runner.invoke(
        cli.app, ["test", "python -m pytest tests/test_app.py", "--path", str(sample)]
    )
    assert result.exit_code == 1
    assert "result     tests failed (exit 1)" in result.stdout
    assert "[1] KeyError: 'db'" in result.stdout
    assert "[2] AssertionError: assert 2 == 3" in result.stdout
    assert sorted(p.relative_to(sample) for p in sample.rglob("*")) == before


@pytest.mark.integration
def test_test_command_passing_run(sample: Path) -> None:
    (sample / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    result = runner.invoke(
        cli.app,
        ["test", "python -m pytest tests/test_ok.py", "--path", str(sample), "--json"],
    )
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["result"]["exit_code"] == 0
    assert report["diagnoses"] == []


def test_test_command_rejects_commands_outside_the_allowlist(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["test", "rm -rf /", "--path", str(tmp_path)])
    assert result.exit_code == 2
    assert "Command not allowed" in result.output


def test_redact_command_hides_secrets_and_reports_counts(tmp_path: Path) -> None:
    config = tmp_path / "settings.env"
    config.write_text("DEBUG=true\nDB_PASSWORD=hunter2\nDATABASE_URL=postgres://u:pw1@db/app\n")
    result = runner.invoke(cli.app, ["redact", str(config)])
    assert result.exit_code == 0
    assert "hunter2" not in result.stdout and "pw1" not in result.stdout
    assert "DEBUG=true" in result.stdout
    assert "redacted 2 secrets: secret_assignment 1, url_credentials 1" in result.output


def test_redact_command_without_secrets() -> None:
    result = runner.invoke(cli.app, ["redact"], input="nothing to hide\n")
    assert result.exit_code == 0
    assert "nothing to hide" in result.stdout
    assert "no secrets found" in result.output


FIX_PATCH = json.dumps(
    {
        "description": "Use a default when the db key is missing",
        "edits": [
            {
                "path": "app/cfg.py",
                "old": '    return config["db"]',
                "new": '    return config.get("db")',
            },
            {
                "path": "tests/test_app.py",
                "old": 'assert parse_port("2") == 3',
                "new": 'assert parse_port("2") == 2',
            },
        ],
    }
)


@pytest.mark.integration
def test_patch_command_fixes_the_tests_in_the_copy_only(sample: Path) -> None:
    (sample / "tests" / "test_collect.py").unlink()
    original = (sample / "app" / "cfg.py").read_text()
    result = runner.invoke(
        cli.app,
        ["patch", "-", "python -m pytest tests/test_app.py", "--path", str(sample)],
        input=FIX_PATCH,
    )
    assert result.exit_code == 0
    assert "patch      2 edits in 2 files (+2 -2), applied to the copy only" in result.stdout
    assert '+    return config.get("db")' in result.stdout
    assert "verdict    the tests pass with this patch" in result.stdout
    assert (sample / "app" / "cfg.py").read_text() == original


@pytest.mark.integration
def test_patch_command_reports_tests_that_still_fail(sample: Path) -> None:
    partial_fix = json.dumps({"edits": json.loads(FIX_PATCH)["edits"][:1]})
    result = runner.invoke(
        cli.app,
        ["patch", "-", "python -m pytest tests/test_app.py", "--path", str(sample), "--json"],
        input=partial_fix,
    )
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["patch"]["files"] == ["app/cfg.py"]
    assert [d["error"]["error_type"] for d in report["diagnoses"]] == ["AssertionError"]


@pytest.mark.parametrize(
    ("patch_text", "message"),
    [
        ('{"edits": [{"path": "../x.py", "old": "a", "new": "b"}]}', "outside the project"),
        ('{"edits": [{"path": "app/cfg.py", "old": "nope", "new": "b"}]}', "not found"),
        ("not json", "Invalid patch"),
    ],
)
def test_patch_command_rejects_bad_patches(sample: Path, patch_text: str, message: str) -> None:
    result = runner.invoke(cli.app, ["patch", "-", "--path", str(sample)], input=patch_text)
    assert result.exit_code == 2
    assert message in result.output


class FakeLLM:
    """Stands in for Claude: proposes the read_db fix for the sample project."""

    def __init__(self, fix_test: bool = False) -> None:
        self.prompts: list[str] = []
        self.fix_test = fix_test

    def propose(self, system: str, prompt: str) -> Proposal:
        self.prompts.append(prompt)
        edits = [
            FileEdit(
                path="app/cfg.py", old='    return config["db"]', new='    return config.get("db")'
            )
        ]
        if self.fix_test:
            edits.append(
                FileEdit(
                    path="tests/test_app.py", old='parse_port("2") == 3', new='parse_port("2") == 2'
                )
            )
        return Proposal(
            patch=Patch(edits=tuple(edits), description="Use a default for db."),
            explanation="Use a default for db.",
            model="claude-sonnet-5-5",
            usage=TokenUsage(input_tokens=600, output_tokens=120, cache_write_tokens=300),
        )


@pytest.fixture
def fake_llm(sample: Path, monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    (sample / "tests" / "test_collect.py").unlink()
    llm = FakeLLM(fix_test=True)
    monkeypatch.setattr(
        cli,
        "build_fix_proposer",
        partial(build_fix_proposer, embedding_function=FakeEmbeddingFunction(), llm=llm),
    )
    return llm


@pytest.mark.integration
def test_propose_command_asks_for_a_fix_and_validates_it(sample: Path, fake_llm: FakeLLM) -> None:
    original = (sample / "app" / "cfg.py").read_text()
    log = (TRACES / "pytest_long.txt").read_text()
    result = runner.invoke(
        cli.app,
        ["propose", "--path", str(sample), "--command", "python -m pytest tests/test_app.py"],
        input=log,
    )
    assert result.exit_code == 0, result.output
    assert "error      KeyError: 'db'" in result.stdout
    assert "context    " in result.stdout and "app/cfg.py:read_db" in result.stdout
    assert "model      claude-sonnet-5-5, 900 tokens in, 120 out" in result.stdout
    assert '+    return config.get("db")' in result.stdout
    assert "verdict    the tests pass with this patch" in result.stdout
    assert 'symbol="read_db"' in fake_llm.prompts[0]
    assert (sample / "app" / "cfg.py").read_text() == original


@pytest.mark.integration
def test_propose_without_tests_shows_the_edits_and_saves_them(
    sample: Path, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    target = tmp_path / "fix.json"
    result = runner.invoke(
        cli.app,
        ["propose", "--path", str(sample), "--no-test", "--save", str(target)],
        input=(TRACES / "script_key.txt").read_text(),
    )
    assert result.exit_code == 0, result.output
    assert '+     return config.get("db")' in result.stdout
    assert "verdict" not in result.stdout
    saved = parse_patch(target.read_text())
    assert saved.edits[0].path == "app/cfg.py"


def test_propose_without_an_api_key_explains_what_to_do(
    sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    result = runner.invoke(
        cli.app, ["propose", "--path", str(sample)], input=(TRACES / "script_key.txt").read_text()
    )
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY is not set" in result.output


def test_propose_with_a_log_without_errors(sample: Path, fake_llm: FakeLLM) -> None:
    result = runner.invoke(cli.app, ["propose", "--path", str(sample)], input="3 passed\n")
    assert result.exit_code == 1
    assert "no errors found" in result.output
    assert fake_llm.prompts == []


@pytest.mark.integration
def test_propose_show_prompt_needs_no_api_key(
    sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    secret = "AKIA" + "IOSFODNN7EXAMPLE"
    (sample / "app" / "cfg.py").write_text(
        f'KEY = "{secret}"\n\n\ndef read_db(config):\n    return config["db"]\n'
    )
    result = runner.invoke(
        cli.app,
        ["propose", "--path", str(sample), "--show-prompt"],
        input=(TRACES / "script_key.txt").read_text().replace("line 2,", "line 5,"),
    )
    assert result.exit_code == 0, result.output
    assert "----- system -----" in result.stdout
    assert '<code path="app/cfg.py"' in result.stdout
    assert secret not in result.stdout
