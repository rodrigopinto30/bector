"""Use case: run the test command on an isolated copy, optionally after patching it."""

from healer.domain.execution import CommandPolicy, RunReport
from healer.domain.patch import Patch, PatchPolicy
from healer.domain.ports import CommandRunner, PatchApplier, SandboxProvider
from healer.services.diagnoser import Diagnoser


class SandboxRunner:
    def __init__(
        self,
        provider: SandboxProvider,
        runner: CommandRunner,
        policy: CommandPolicy,
        diagnoser: Diagnoser,
        applier: PatchApplier,
        patch_policy: PatchPolicy,
    ) -> None:
        self._provider = provider
        self._runner = runner
        self._policy = policy
        self._diagnoser = diagnoser
        self._applier = applier
        self._patch_policy = patch_policy

    def run_tests(self, command: str, patch: Patch | None = None) -> RunReport:
        """Validate the inputs, copy the workspace, apply ``patch`` to the copy, run, clean up.

        Everything that can be rejected is rejected before the copy is made. The real
        workspace is never written.
        """
        argv = self._policy.parse(command)
        if patch is not None:
            self._patch_policy.validate(patch)
        sandbox = self._provider.create()
        try:
            applied = self._applier.apply(patch, sandbox.root) if patch is not None else None
            result = self._runner.run(argv, sandbox.root)
        finally:
            sandbox.close()
        diagnoses = () if result.passed else tuple(self._diagnoser.diagnose(result.output))
        return RunReport(
            result=result,
            files_copied=sandbox.files_copied,
            diagnoses=diagnoses,
            patch=applied,
        )
