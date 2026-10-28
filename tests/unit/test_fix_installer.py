from pathlib import Path

import pytest

from healer.adapters.filesystem_patcher import FilesystemPatcher
from healer.domain.errors import InvalidPatchError, PatchConflictError, WorkspaceError
from healer.domain.patch import AppliedPatch, FileEdit, Patch, PatchPolicy
from healer.services.fix_installer import FixInstaller

CFG = 'def read_db(config):\n    return config["db"]\n'
PATCH = Patch(edits=(FileEdit(path="app/cfg.py", old='config["db"]', new='config.get("db")'),))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "cfg.py").write_text(CFG)
    return tmp_path


def patch_a_copy(workspace: Path, tmp_path_factory: pytest.TempPathFactory) -> AppliedPatch:
    copy = tmp_path_factory.mktemp("copy")
    (copy / "app").mkdir()
    (copy / "app" / "cfg.py").write_text((workspace / "app" / "cfg.py").read_text())
    return FilesystemPatcher().apply(PATCH, copy)


def installer(workspace: Path, max_files: int = 5) -> FixInstaller:
    return FixInstaller(
        workspace, FilesystemPatcher(), PatchPolicy(max_files=max_files, max_bytes=10_000)
    )


def test_installs_the_tested_patch_in_the_workspace(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    tested = patch_a_copy(workspace, tmp_path_factory)
    applied = installer(workspace).install(PATCH, tested)
    assert (workspace / "app" / "cfg.py").read_text() == CFG.replace(
        'config["db"]', 'config.get("db")'
    )
    assert applied.diff == tested.diff


def test_refuses_when_a_file_changed_after_the_test(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    tested = patch_a_copy(workspace, tmp_path_factory)
    edited = CFG + "\n\nTIMEOUT = 5\n"
    (workspace / "app" / "cfg.py").write_text(edited)
    with pytest.raises(PatchConflictError, match="changed since the patch was tested"):
        installer(workspace).install(PATCH, tested)
    assert (workspace / "app" / "cfg.py").read_text() == edited


def test_refuses_a_patch_that_is_not_the_one_tested(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    tested = patch_a_copy(workspace, tmp_path_factory)
    other = Patch(edits=(*PATCH.edits, FileEdit(path="app/new.py", new="x = 1\n")))
    with pytest.raises(PatchConflictError, match="does not match"):
        installer(workspace).install(other, tested)
    assert not (workspace / "app" / "new.py").exists()


def test_applies_the_same_rules_as_on_the_copy(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    tested = patch_a_copy(workspace, tmp_path_factory)
    with pytest.raises(InvalidPatchError, match="the limit is 0"):
        installer(workspace, max_files=0).install(PATCH, tested)
    assert (workspace / "app" / "cfg.py").read_text() == CFG


def test_the_workspace_must_exist(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        installer(tmp_path / "missing")
