from pathlib import Path

import pytest

from healer.adapters.workspace_scanner import WorkspaceScanner
from healer.domain.errors import WorkspaceError


def write(root: Path, rel: str, content: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def paths(root: Path, **kwargs: object) -> list[str]:
    return [f.path for f in WorkspaceScanner(root, **kwargs).scan()]  # type: ignore[arg-type]


def test_returns_relative_posix_paths_sorted(tmp_path: Path) -> None:
    write(tmp_path, "b.py")
    write(tmp_path, "pkg/a.py")
    write(tmp_path, "notes.txt")
    assert paths(tmp_path) == ["b.py", "pkg/a.py"]


def test_ignores_vendor_and_cache_directories(tmp_path: Path) -> None:
    write(tmp_path, "keep.py")
    for ignored in (".git", ".venv", "node_modules", "__pycache__", ".healer"):
        write(tmp_path, f"{ignored}/skip.py")
    assert paths(tmp_path) == ["keep.py"]


def test_hash_changes_with_content(tmp_path: Path) -> None:
    target = write(tmp_path, "a.py", "x = 1\n")
    before = next(WorkspaceScanner(tmp_path).scan()).content_hash
    target.write_text("x = 2\n")
    assert next(WorkspaceScanner(tmp_path).scan()).content_hash != before


def test_skips_oversized_files(tmp_path: Path) -> None:
    write(tmp_path, "big.py", "x = 1\n" * 100)
    write(tmp_path, "small.py")
    assert paths(tmp_path, max_file_bytes=50) == ["small.py"]


def test_skips_non_utf8_files(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_bytes(b"x = '\xff\xfe'\n")
    write(tmp_path, "ok.py")
    assert paths(tmp_path) == ["ok.py"]


def test_file_symlink_escaping_the_workspace_is_skipped(tmp_path: Path) -> None:
    outside = write(tmp_path, "outside/secret.py", "TOKEN = 'abc'\n")
    root = tmp_path / "ws"
    write(root, "ok.py")
    (root / "leak.py").symlink_to(outside)
    assert paths(root) == ["ok.py"]


def test_directory_symlink_is_not_followed(tmp_path: Path) -> None:
    write(tmp_path, "outside/secret.py")
    root = tmp_path / "ws"
    write(root, "ok.py")
    (root / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert paths(root) == ["ok.py"]


def test_symlink_inside_the_workspace_is_allowed(tmp_path: Path) -> None:
    write(tmp_path, "real.py")
    (tmp_path / "alias.py").symlink_to(tmp_path / "real.py")
    assert paths(tmp_path) == ["alias.py", "real.py"]


def test_missing_workspace_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        WorkspaceScanner(tmp_path / "nope")
