"""Use case: apply a patch that passed on a copy to the developer's real workspace."""

from pathlib import Path

from healer.domain.errors import PatchConflictError, WorkspaceError
from healer.domain.patch import AppliedPatch, Patch, PatchPolicy
from healer.domain.ports import PatchApplier


class FixInstaller:
    """The only part of the system that writes the developer's files (Human-in-the-Loop, 7.5).

    It is called only after the developer accepts a patch. The patch must follow the same
    rules as on the copy, and it is written only if every file it touches still has the
    content that was tested; otherwise nothing is changed.
    """

    def __init__(self, workspace: Path, applier: PatchApplier, policy: PatchPolicy) -> None:
        root = workspace.resolve()
        if not root.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {workspace}")
        self.workspace = root
        self._applier = applier
        self._policy = policy

    def install(self, patch: Patch, tested: AppliedPatch) -> AppliedPatch:
        """Write ``patch``, which produced ``tested`` on a copy, to the workspace."""
        self._policy.validate(patch)
        if set(patch.files) != set(tested.files):
            raise PatchConflictError("the patch does not match the one that was tested")
        return self._applier.apply(patch, self.workspace, expected=tested.checksums)
