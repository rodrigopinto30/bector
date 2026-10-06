"""Use case: run the test command on an isolated copy and diagnose what failed."""

from healer.domain.execution import CommandPolicy, RunReport
from healer.domain.ports import CommandRunner, SandboxProvider
from healer.services.diagnoser import Diagnoser


class SandboxRunner:
    def __init__(
        self,
        provider: SandboxProvider,
        runner: CommandRunner,
        policy: CommandPolicy,
        diagnoser: Diagnoser,
    ) -> None:
        self._provider = provider
        self._runner = runner
        self._policy = policy
        self._diagnoser = diagnoser

    def run_tests(self, command: str) -> RunReport:
        """Validate ``command``, run it on a fresh copy of the workspace, then delete the copy."""
        argv = self._policy.parse(command)
        sandbox = self._provider.create()
        try:
            result = self._runner.run(argv, sandbox.root)
        finally:
            sandbox.close()
        diagnoses = () if result.passed else tuple(self._diagnoser.diagnose(result.output))
        return RunReport(result=result, files_copied=sandbox.files_copied, diagnoses=diagnoses)
