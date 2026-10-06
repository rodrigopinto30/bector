import os
from pathlib import Path

import pytest

from healer.adapters.tempdir_sandbox import TempDirSandboxProvider
from healer.domain.errors import SandboxError, WorkspaceError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "app").mkdir(parents=True)
    (root / "app" / "cfg.py").write_text("x = 1\n")
    (root / "README.md").write_text("hi\n")
    for ignored in (".git", ".venv", "__pycache__", "node_modules"):
        (root / ignored).mkdir()
        (root / ignored / "big.bin").write_bytes(b"0" * 10)
    return root


@pytest.fixture
def base(tmp_path: Path) -> Path:
    path = tmp_path / "sandboxes"
    path.mkdir()
    return path


def test_copies_files_and_skips_vendor_and_cache_folders(workspace: Path, base: Path) -> None:
    with TempDirSandboxProvider(workspace, base_dir=base).create() as sandbox:
        assert (sandbox.root / "app" / "cfg.py").read_text() == "x = 1\n"
        assert (sandbox.root / "README.md").exists()
        assert not (sandbox.root / ".git").exists()
        assert not (sandbox.root / ".venv").exists()
        assert sandbox.files_copied == 2


def test_changes_in_the_sandbox_never_reach_the_workspace(workspace: Path, base: Path) -> None:
    with TempDirSandboxProvider(workspace, base_dir=base).create() as sandbox:
        (sandbox.root / "app" / "cfg.py").write_text("x = 2\n")
        (sandbox.root / "new.py").write_text("")
    assert (workspace / "app" / "cfg.py").read_text() == "x = 1\n"
    assert not (workspace / "new.py").exists()


def test_close_deletes_the_copy_and_is_idempotent(workspace: Path, base: Path) -> None:
    sandbox = TempDirSandboxProvider(workspace, base_dir=base).create()
    sandbox.close()
    sandbox.close()
    assert not sandbox.root.exists()
    assert list(base.iterdir()) == []


def test_close_removes_read_only_files(workspace: Path, base: Path) -> None:
    sandbox = TempDirSandboxProvider(workspace, base_dir=base).create()
    locked = sandbox.root / "app"
    (locked / "ro.txt").write_text("x")
    os.chmod(locked / "ro.txt", 0o400)
    os.chmod(locked, 0o500)
    sandbox.close()
    assert not sandbox.root.exists()


def test_symlinks_are_copied_as_links_not_followed(
    workspace: Path, base: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN")
    (workspace / "leak.txt").symlink_to(secret)
    (workspace / "linked_dir").symlink_to(tmp_path, target_is_directory=True)
    with TempDirSandboxProvider(workspace, base_dir=base).create() as sandbox:
        assert (sandbox.root / "leak.txt").is_symlink()
        assert (sandbox.root / "linked_dir").is_symlink()
        assert sandbox.files_copied == 2


def test_oversized_workspace_is_rejected_and_nothing_is_left(workspace: Path, base: Path) -> None:
    with pytest.raises(SandboxError, match="larger than"):
        TempDirSandboxProvider(workspace, max_bytes=3, base_dir=base).create()
    assert list(base.iterdir()) == []


def test_sandbox_directory_is_private(workspace: Path, base: Path) -> None:
    with TempDirSandboxProvider(workspace, base_dir=base).create() as sandbox:
        assert sandbox.root.stat().st_mode & 0o077 == 0


def test_missing_workspace_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        TempDirSandboxProvider(tmp_path / "missing")
