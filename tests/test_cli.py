import json
import shutil
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from typer.testing import CliRunner

from healer import cli
from healer.config import Settings
from healer.domain.errors import SharingError
from healer.domain.patch import FileEdit, Patch, parse_patch
from healer.domain.proposal import Proposal, TokenUsage
from healer.domain.sharing import Evidence, SharedSolution, seal
from healer.factory import (
    build_code_index,
    build_diagnoser,
    build_fix_proposer,
    build_repair_pipeline,
    build_sandbox_runner,
    build_solution_ingester,
    build_solution_sharer,
    build_team_solutions,
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


@pytest.mark.integration
def test_apply_command_tests_the_patch_and_applies_it_after_confirmation(
    sample: Path, tmp_path: Path
) -> None:
    (sample / "tests" / "test_collect.py").unlink()
    saved = tmp_path / "fix.json"
    saved.write_text(FIX_PATCH)
    command = ["apply", str(saved), "python -m pytest tests/test_app.py", "--path", str(sample)]
    original = (sample / "app" / "cfg.py").read_text()

    declined = runner.invoke(cli.app, command, input="n\n")
    assert declined.exit_code == 0, declined.output
    assert "verdict    the tests pass with this patch" in declined.stdout
    assert "skipped    your files were not changed" in declined.stdout
    assert (sample / "app" / "cfg.py").read_text() == original

    accepted = runner.invoke(cli.app, [*command, "--yes"])
    assert accepted.exit_code == 0, accepted.output
    assert "applied    2 files changed in your workspace (+2 -2)" in accepted.stdout
    assert 'return config.get("db")' in (sample / "app" / "cfg.py").read_text()


@pytest.mark.integration
def test_apply_command_never_applies_a_patch_whose_tests_fail(sample: Path) -> None:
    original = (sample / "app" / "cfg.py").read_text()
    partial_fix = json.dumps({"edits": json.loads(FIX_PATCH)["edits"][:1]})
    result = runner.invoke(
        cli.app,
        ["apply", "-", "python -m pytest tests/test_app.py", "--path", str(sample), "--yes"],
        input=partial_fix,
    )
    assert result.exit_code == 1, result.output
    assert "the tests still fail with this patch; your files were not changed" in result.stdout
    assert "AssertionError" in result.stdout
    assert (sample / "app" / "cfg.py").read_text() == original


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


class ScriptedLLM:
    """Stands in for Claude in the loop: a partial fix first, the complete one next."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def propose(self, system: str, prompt: str) -> Proposal:
        self.prompts.append(prompt)
        return FakeLLM(fix_test=len(self.prompts) > 1).propose(system, prompt)


@pytest.fixture
def scripted_llm(sample: Path, monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    (sample / "tests" / "test_collect.py").unlink()
    llm = ScriptedLLM()
    monkeypatch.setattr(
        cli,
        "build_repair_pipeline",
        partial(build_repair_pipeline, embedding_function=FakeEmbeddingFunction(), llm=llm),
    )
    return llm


@pytest.mark.integration
def test_run_retries_with_feedback_until_the_tests_pass(
    sample: Path, scripted_llm: ScriptedLLM, tmp_path: Path
) -> None:
    original = (sample / "app" / "cfg.py").read_text()
    target = tmp_path / "fix.json"
    result = runner.invoke(
        cli.app,
        ["run", "python -m pytest tests/test_app.py", "--path", str(sample), "--save", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert "error      KeyError: 'db'  (app/cfg.py:2)" in result.stdout
    assert "limits     3 attempts, 100000 tokens" in result.stdout
    assert "result     the tests still fail: AssertionError: assert 2 == 3" in result.stdout
    assert "verdict    fixed: the tests pass with the patch of attempt 2" in result.stdout
    assert "spent      2 attempts, 1800 tokens in, 240 out" in result.stdout
    assert '+    return config.get("db")' in result.stdout
    assert "AssertionError: assert 2 == 3" in scripted_llm.prompts[1]
    assert len(parse_patch(target.read_text()).edits) == 2
    assert "recommended patch: 2 files (+2 -2), tested on a copy" in result.stdout
    assert (
        f"skipped    your files were not changed; apply it later with 'healer apply {target}'"
        in (result.stdout)
    )
    assert (sample / "app" / "cfg.py").read_text() == original


@pytest.mark.integration
@pytest.mark.integration
def test_run_applies_the_fix_to_the_workspace_when_accepted(
    sample: Path, scripted_llm: ScriptedLLM
) -> None:
    result = runner.invoke(
        cli.app, ["run", "python -m pytest tests/test_app.py", "--path", str(sample)], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert f"apply this patch to your files in {sample.resolve()}? [y/N]: y" in result.stdout
    assert "applied    2 files changed in your workspace (+2 -2)" in result.stdout
    assert 'return config.get("db")' in (sample / "app" / "cfg.py").read_text()
    assert 'parse_port("2") == 2' in (sample / "tests" / "test_app.py").read_text()


@pytest.mark.integration
@pytest.mark.parametrize(("flag", "applied"), [("--apply", True), ("--no-apply", False)])
def test_run_apply_flags_skip_the_question(
    sample: Path, scripted_llm: ScriptedLLM, flag: str, applied: bool
) -> None:
    result = runner.invoke(
        cli.app, ["run", "python -m pytest tests/test_app.py", "--path", str(sample), flag]
    )
    assert result.exit_code == 0, result.output
    assert "apply this patch" not in result.stdout
    assert ("applied    2 files changed" in result.stdout) is applied
    assert ('config.get("db")' in (sample / "app" / "cfg.py").read_text()) is applied


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[bytes] = []

    def publish(self, message: bytes) -> None:
        self.messages.append(message)


@pytest.fixture
def team(monkeypatch: pytest.MonkeyPatch) -> FakePublisher:
    """Sharing on, with a team key and a fake broker."""
    publisher = FakePublisher()
    monkeypatch.setenv("SHARE_SOLUTIONS", "true")
    monkeypatch.setenv("SHARE_KEY", "0123456789abcdef-team-key")
    monkeypatch.setenv("NODE_ID", "laptop-ana")
    monkeypatch.setattr(
        cli, "build_solution_sharer", partial(build_solution_sharer, publisher=publisher)
    )
    return publisher


@pytest.mark.integration
def test_run_shares_an_applied_fix_with_the_team(
    sample: Path, scripted_llm: ScriptedLLM, team: FakePublisher
) -> None:
    result = runner.invoke(
        cli.app, ["run", "python -m pytest tests/test_app.py", "--path", str(sample)], input="y\n"
    )
    assert result.exit_code == 0, result.output
    assert "sharing    sending the fix to the team through 127.0.0.1:1...\n" in result.stdout
    assert "shared     sent to the team as node laptop-ana on healer/solutions/v1 (" in (
        result.stdout
    )
    [message] = team.messages
    body = json.loads(message)["solution"]
    assert body["patch"]["edits"][0]["path"] == "app/cfg.py"
    assert body["tokens_spent"] == 2040
    assert body["evidence"]["command"] == "python -m pytest tests/test_app.py"
    assert body["evidence"]["exit_code_before"] == 1


@pytest.mark.integration
@pytest.mark.parametrize(("flags", "answer"), [([], "n\n"), (["--no-share"], "y\n")])
def test_run_does_not_share_a_declined_fix_or_with_no_share(
    sample: Path, scripted_llm: ScriptedLLM, team: FakePublisher, flags: list[str], answer: str
) -> None:
    result = runner.invoke(
        cli.app,
        ["run", "python -m pytest tests/test_app.py", "--path", str(sample), *flags],
        input=answer,
    )
    assert result.exit_code == 0, result.output
    assert "shared" not in result.stdout
    assert team.messages == []


@pytest.mark.integration
def test_a_sharing_failure_keeps_the_applied_fix(
    sample: Path, scripted_llm: ScriptedLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MQTT_MAX_RETRIES", "0")
    command = ["run", "python -m pytest tests/test_app.py", "--path", str(sample), "--apply"]
    missing_key = runner.invoke(cli.app, [*command, "--share"])
    assert missing_key.exit_code == 0, missing_key.output
    assert "warning: the fix was applied but not shared: SHARE_KEY is not set" in (
        missing_key.output
    )
    assert "sharing    sending" not in missing_key.output
    assert 'config.get("db")' in (sample / "app" / "cfg.py").read_text()

    shutil.copy(SAMPLE / "app" / "cfg.py", sample / "app" / "cfg.py")
    shutil.copy(SAMPLE / "tests" / "test_app.py", sample / "tests" / "test_app.py")
    monkeypatch.setenv("SHARE_KEY", "0123456789abcdef-team-key")
    unreachable = runner.invoke(cli.app, [*command, "--share"])
    assert unreachable.exit_code == 0, unreachable.output
    assert "sharing    sending the fix to the team through 127.0.0.1:1..." in unreachable.stdout
    assert "not shared: the broker at 127.0.0.1:1 could not be reached" in unreachable.output
    assert "applied    2 files changed" in unreachable.stdout


def test_doctor_reports_whether_sharing_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "[info] sharing: off" in runner.invoke(cli.app, ["doctor"]).stdout
    monkeypatch.setenv("SHARE_SOLUTIONS", "true")
    missing = runner.invoke(cli.app, ["doctor"])
    assert missing.exit_code == 1
    assert "[fail] sharing is on, but SHARE_KEY is missing" in missing.stdout
    monkeypatch.setenv("SHARE_KEY", "0123456789abcdef-team-key")
    monkeypatch.setenv("NODE_ID", "laptop-ana")
    ready = runner.invoke(cli.app, ["doctor"])
    assert "[ok]   sharing: on, as node laptop-ana on healer/solutions/v1" in ready.stdout


TEAM_KEY = "0123456789abcdef-team-key"


def team_message(error: str = "KeyError: 'db'", key: str = TEAM_KEY) -> bytes:
    at = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    solution = SharedSolution(
        node="laptop-ana",
        published_at=at,
        signature="a" * 64,
        error=error,
        patch=Patch(edits=(FileEdit(path="app/cfg.py", old='c["db"]', new='c.get("db")'),)),
        explanation="Use a default.",
        model="claude-sonnet-5-5",
        tokens_spent=2_900,
        evidence=Evidence(command="python -m pytest", exit_code_before=1, verified_at=at),
    )
    return seal(solution, key.encode())


class FakeSubscriber:
    """Delivers the messages it was given, then ends as the real one would."""

    def __init__(self, *messages: bytes, ending: BaseException | None = None) -> None:
        self.messages = messages
        self.ending = ending

    def run(self, on_message: Callable[[bytes], None], on_status: Callable[[str], None]) -> None:
        on_status("connected to the broker")
        for message in self.messages:
            on_message(message)
        if self.ending is not None:
            raise self.ending

    def stop(self) -> None:
        pass


@pytest.fixture
def team_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """A node with the team key; call it with the subscriber ``healer listen`` should use."""
    fake = FakeEmbeddingFunction()
    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("SHARE_KEY", TEAM_KEY)
    monkeypatch.setenv("NODE_ID", "laptop-luis")
    monkeypatch.setattr(
        cli, "build_solution_ingester", partial(build_solution_ingester, embedding_function=fake)
    )
    monkeypatch.setattr(
        cli, "build_team_solutions", partial(build_team_solutions, embedding_function=fake)
    )

    def use(subscriber: FakeSubscriber) -> None:
        monkeypatch.setattr(cli, "build_subscriber", lambda _: subscriber)

    return use


def test_listen_keeps_valid_messages_and_refuses_the_rest(team_node: Callable[..., None]) -> None:
    team_node(
        FakeSubscriber(
            team_message(),
            team_message(),
            team_message(key="a-key-that-is-not-the-team-key"),
            b"not json",
            ending=KeyboardInterrupt(),
        )
    )
    result = runner.invoke(cli.app, ["listen"])
    assert result.exit_code == 0, result.output
    assert "listening  as node laptop-luis on healer/solutions/v1; stop with Ctrl+C" in (
        result.stdout
    )
    assert "broker     connected to the broker" in result.stdout
    assert "received   KeyError: 'db' from laptop-ana (1 file), kept as a suggestion" in (
        result.stdout
    )
    assert "ignored    KeyError: 'db' from laptop-ana (1 file): already known" in result.stdout
    assert "rejected   the signature does not match: forged or altered message" in result.stdout
    assert "rejected   the message is not valid JSON" in result.stdout
    assert result.stdout.rstrip().endswith("stopped")
    assert signal.getsignal(signal.SIGTERM) is not cli._interrupt

    shown = runner.invoke(cli.app, ["team"])
    assert shown.exit_code == 0, shown.output
    assert "1 suggestion from the team, newest first" in shown.stdout
    assert "from laptop-ana" in shown.stdout
    assert "error      KeyError: 'db'" in shown.stdout
    assert "files      app/cfg.py" in shown.stdout
    assert "evidence   python -m pytest" in shown.stdout


def test_text_from_other_nodes_cannot_control_the_terminal(
    team_node: Callable[..., None],
) -> None:
    team_node(FakeSubscriber(team_message(error="KeyError: \x1b[2Jfake\nverdict    fixed")))
    received = runner.invoke(cli.app, ["listen"])
    shown = runner.invoke(cli.app, ["team"])
    for output in (received.stdout, shown.stdout):
        assert "\x1b" not in output
        assert "?[2Jfake?verdict    fixed" in output


def test_listen_without_the_team_key_explains_what_to_do(
    team_node: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SHARE_KEY")
    team_node(FakeSubscriber())
    result = runner.invoke(cli.app, ["listen"])
    assert result.exit_code == 2
    assert "SHARE_KEY is not set" in result.output


def test_listen_warns_when_the_node_has_no_fixed_name(
    team_node: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NODE_ID")
    team_node(FakeSubscriber())
    result = runner.invoke(cli.app, ["listen"])
    assert result.exit_code == 0, result.output
    assert "warning: NODE_ID is not set" in result.output


def test_listen_reports_a_broker_that_refuses_the_node(team_node: Callable[..., None]) -> None:
    team_node(FakeSubscriber(ending=SharingError("the broker refused the connection: no")))
    result = runner.invoke(cli.app, ["listen"])
    assert result.exit_code == 2
    assert "error: the broker refused the connection: no" in result.output


def test_team_without_suggestions(team_node: Callable[..., None]) -> None:
    result = runner.invoke(cli.app, ["team"])
    assert result.exit_code == 0, result.output
    assert "no suggestions yet" in result.stdout


def test_run_stops_at_the_attempt_limit(sample: Path, scripted_llm: ScriptedLLM) -> None:
    result = runner.invoke(
        cli.app,
        ["run", "python -m pytest tests/test_app.py", "--path", str(sample), "--attempts", "1"],
    )
    assert result.exit_code == 1, result.output
    assert "verdict    not fixed: no patch made the tests pass in 1 attempt" in result.stdout
    assert len(scripted_llm.prompts) == 1


@pytest.mark.integration
def test_run_with_passing_tests_does_not_call_the_model(
    sample: Path, scripted_llm: ScriptedLLM
) -> None:
    result = runner.invoke(
        cli.app, ["run", "python -m pytest tests/test_app.py::test_ok", "--path", str(sample)]
    )
    assert result.exit_code == 0, result.output
    assert "nothing to fix" in result.stdout
    assert scripted_llm.prompts == []


def test_run_rejects_commands_outside_the_allowlist(
    sample: Path, scripted_llm: ScriptedLLM
) -> None:
    result = runner.invoke(cli.app, ["run", "rm -rf /", "--path", str(sample)])
    assert result.exit_code == 2
    assert "Command not allowed" in result.output
    assert scripted_llm.prompts == []


@pytest.mark.integration
def test_run_reuses_a_stored_solution_and_records_the_savings(
    sample: Path, scripted_llm: ScriptedLLM
) -> None:
    command = ["run", "python -m pytest tests/test_app.py", "--path", str(sample)]
    first = runner.invoke(cli.app, command)
    assert first.exit_code == 0, first.output
    assert "cache      stored: the same error will be fixed without Claude next time" in (
        first.stdout
    )

    second = runner.invoke(cli.app, command)
    assert second.exit_code == 0, second.output
    assert "attempt 1  local cache, 0 tokens (found by claude-sonnet-5-5 for 2040 tokens)" in (
        second.stdout
    )
    assert "verdict    fixed: the tests pass with a solution from the local cache" in second.stdout
    assert "spent      1 attempt, 0 tokens in, 0 out; saved about 2040 tokens" in second.stdout
    assert len(scripted_llm.prompts) == 2

    forced = runner.invoke(cli.app, [*command, "--no-cache"])
    assert forced.exit_code == 0, forced.output
    assert "local cache" not in forced.stdout
    assert len(scripted_llm.prompts) == 3

    report = runner.invoke(cli.app, ["savings"])
    assert report.exit_code == 0, report.output
    lines = report.stdout.splitlines()
    assert lines[0].split() == [
        "day",
        "incidents",
        "fixed",
        "from",
        "cache",
        "model",
        "calls",
        "tokens",
        "used",
        "tokens",
        "saved",
    ]
    assert lines[-3].split() == ["total", "3", "3", "1", "3", "3060", "2040"]
    assert "reuse avoided 40% of the tokens" in report.stdout

    by_project = runner.invoke(cli.app, ["savings", "--by", "project", "--days", "1"])
    assert str(sample.resolve()) in by_project.stdout


def test_savings_without_incidents() -> None:
    result = runner.invoke(cli.app, ["savings"])
    assert result.exit_code == 0
    assert "no incidents recorded yet" in result.stdout
