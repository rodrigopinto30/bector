from collections.abc import Sequence
from pathlib import Path

import pytest

from healer.domain.diagnosis import Diagnosis
from healer.domain.errors import CommandNotAllowedError, ExecutionError
from healer.domain.execution import CommandPolicy, RunResult
from healer.services.sandbox_runner import SandboxRunner


class FakeSandbox:
    def __init__(self) -> None:
        self.closed = False

    @property
    def root(self) -> Path:
        return Path("/sandbox")

    @property
    def files_copied(self) -> int:
        return 4

    def close(self) -> None:
        self.closed = True


class FakeProvider:
    def __init__(self) -> None:
        self.created: list[FakeSandbox] = []

    def create(self) -> FakeSandbox:
        self.created.append(FakeSandbox())
        return self.created[-1]


class FakeRunner:
    def __init__(self, exit_code: int | None, output: str = "", fail: bool = False) -> None:
        self.exit_code = exit_code
        self.output = output
        self.fail = fail
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def run(self, argv: Sequence[str], cwd: Path) -> RunResult:
        self.calls.append((tuple(argv), cwd))
        if self.fail:
            raise ExecutionError("boom")
        return RunResult(
            command=tuple(argv),
            exit_code=self.exit_code,
            output=self.output,
            duration_seconds=0.1,
            timed_out=self.exit_code is None,
        )


class FakeDiagnoser:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def diagnose(self, text: str) -> list[Diagnosis]:
        self.texts.append(text)
        return []


POLICY = CommandPolicy(["python -m pytest"])


def build(runner: FakeRunner) -> tuple[SandboxRunner, FakeProvider, FakeDiagnoser]:
    provider, diagnoser = FakeProvider(), FakeDiagnoser()
    return SandboxRunner(provider, runner, POLICY, diagnoser), provider, diagnoser  # type: ignore[arg-type]


def test_runs_inside_the_sandbox_and_closes_it() -> None:
    service, provider, diagnoser = build(FakeRunner(0))
    report = service.run_tests("python -m pytest -q")
    [sandbox] = provider.created
    assert report.result.passed
    assert report.files_copied == 4
    assert service._runner.calls == [(("python", "-m", "pytest", "-q"), Path("/sandbox"))]  # type: ignore[attr-defined]
    assert sandbox.closed
    assert diagnoser.texts == []


def test_failed_run_is_diagnosed() -> None:
    service, _, diagnoser = build(FakeRunner(1, output="Traceback ..."))
    report = service.run_tests("python -m pytest")
    assert not report.result.passed
    assert diagnoser.texts == ["Traceback ..."]


def test_timed_out_run_is_diagnosed() -> None:
    service, _, diagnoser = build(FakeRunner(None, output="partial"))
    assert service.run_tests("python -m pytest").result.timed_out
    assert diagnoser.texts == ["partial"]


def test_rejected_command_never_creates_a_sandbox() -> None:
    service, provider, _ = build(FakeRunner(0))
    with pytest.raises(CommandNotAllowedError):
        service.run_tests("rm -rf /")
    assert provider.created == []


def test_sandbox_is_closed_even_when_the_runner_fails() -> None:
    service, provider, _ = build(FakeRunner(0, fail=True))
    with pytest.raises(ExecutionError):
        service.run_tests("python -m pytest")
    assert provider.created[0].closed
