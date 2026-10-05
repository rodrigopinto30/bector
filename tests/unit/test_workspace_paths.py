from pathlib import Path

import pytest

from healer.adapters.workspace_paths import WorkspacePathResolver
from healer.domain.errors import WorkspaceError


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "cfg.py").write_text("x = 1\n")
    (workspace / "main.py").write_text("x = 1\n")
    (workspace / "json").mkdir()
    (workspace / "json" / "decoder.py").write_text("x = 1\n")
    return workspace


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/ana/project/app/cfg.py", "app/cfg.py"),
        ("/tmp/sample/main.py", "main.py"),
        ("app/cfg.py", "app/cfg.py"),
        (r"C:\Users\ana\project\app\cfg.py", "app/cfg.py"),
    ],
)
def test_resolves_paths_printed_on_other_machines(root: Path, raw: str, expected: str) -> None:
    assert WorkspacePathResolver(root).resolve(raw) == expected


def test_absolute_path_inside_the_workspace(root: Path) -> None:
    assert WorkspacePathResolver(root).resolve(str(root / "app" / "cfg.py")) == "app/cfg.py"


@pytest.mark.parametrize(
    "raw",
    [
        "<frozen importlib._bootstrap>",
        "<string>",
        "/usr/lib/python3.11/site-packages/app/cfg.py",
        "/usr/local/lib/python3.11/json/decoder.py",
        "/home/ana/other/unknown.py",
        "",
    ],
)
def test_external_or_unknown_files_are_not_in_the_workspace(root: Path, raw: str) -> None:
    assert WorkspacePathResolver(root).resolve(raw) is None


@pytest.mark.parametrize("raw", ["../outside.py", "app/../../outside.py", "/x/../outside.py"])
def test_path_traversal_is_rejected(root: Path, raw: str) -> None:
    (root.parent / "outside.py").write_text("SECRET = 1\n")
    assert WorkspacePathResolver(root).resolve(raw) is None


def test_symlink_escaping_the_workspace_is_rejected(root: Path) -> None:
    secret = root.parent / "secret.py"
    secret.write_text("TOKEN = 'x'\n")
    (root / "leak.py").symlink_to(secret)
    assert WorkspacePathResolver(root).resolve("/somewhere/leak.py") is None


def test_directories_are_not_files(root: Path) -> None:
    assert WorkspacePathResolver(root).resolve("/x/app") is None


def test_missing_workspace_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        WorkspacePathResolver(tmp_path / "missing")
