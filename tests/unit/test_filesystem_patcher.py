from pathlib import Path

import pytest

from healer.adapters.filesystem_patcher import FilesystemPatcher
from healer.domain.errors import InvalidPatchError, PatchConflictError
from healer.domain.patch import FileEdit, Patch

CFG = 'def read_db(config):\n    return config["db"]\n\n\ndef port(value):\n    return int(value)\n'


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "cfg.py").write_text(CFG)
    (tmp_path / "app" / "other.py").write_text("x = 1\n")
    return tmp_path


def apply(root: Path, *edits: FileEdit, **kwargs: int) -> object:
    return FilesystemPatcher(**kwargs).apply(Patch(edits=edits), root)  # type: ignore[arg-type]


def edit(old: str, new: str, path: str = "app/cfg.py") -> FileEdit:
    return FileEdit(path=path, old=old, new=new)


def test_replaces_the_unique_occurrence_and_reports_a_diff(root: Path) -> None:
    applied = FilesystemPatcher().apply(
        Patch(edits=(edit('config["db"]', 'config.get("db")'),)), root
    )
    assert 'return config.get("db")' in (root / "app" / "cfg.py").read_text()
    assert applied.files == ("app/cfg.py",)
    assert (applied.lines_added, applied.lines_removed) == (1, 1)
    assert "--- a/app/cfg.py\n+++ b/app/cfg.py\n" in applied.diff
    assert '-    return config["db"]\n+    return config.get("db")\n' in applied.diff


def test_edits_to_the_same_file_apply_in_order(root: Path) -> None:
    apply(root, edit('config["db"]', "config.get('db')"), edit("config.get('db')", "None"))
    assert "    return None\n" in (root / "app" / "cfg.py").read_text()


def test_creates_a_new_file_in_new_folders(root: Path) -> None:
    applied = FilesystemPatcher().apply(
        Patch(edits=(edit("", "def test_x():\n    pass\n", path="tests/unit/test_x.py"),)), root
    )
    assert (root / "tests" / "unit" / "test_x.py").read_text() == "def test_x():\n    pass\n"
    assert applied.created == ("tests/unit/test_x.py",)
    assert "--- /dev/null" in applied.diff


def test_cannot_create_a_file_that_exists(root: Path) -> None:
    with pytest.raises(PatchConflictError, match="already exists"):
        apply(root, edit("", "x = 2\n", path="app/other.py"))


def test_cannot_edit_a_missing_file(root: Path) -> None:
    with pytest.raises(PatchConflictError, match="does not exist"):
        apply(root, edit("x", "y", path="app/missing.py"))


def test_text_not_found_is_a_conflict(root: Path) -> None:
    with pytest.raises(PatchConflictError, match="not found: 'return config.data'"):
        apply(root, edit("return config.data", "x"))


def test_ambiguous_text_is_a_conflict(root: Path) -> None:
    with pytest.raises(PatchConflictError, match="appears 2 times"):
        apply(root, edit("    return ", "    yield "))


def test_trailing_whitespace_differences_are_tolerated(root: Path) -> None:
    apply(
        root,
        edit(
            'def read_db(config):   \n    return config["db"]  \n',
            "def read_db(config):\n    return None",
        ),
    )
    text = (root / "app" / "cfg.py").read_text()
    assert text.startswith("def read_db(config):\n    return None\n\n\ndef port")


def test_loose_match_must_also_be_unique(root: Path) -> None:
    (root / "app" / "dup.py").write_text("a = 1 \nb = 2\na = 1\nb = 2\n")
    with pytest.raises(PatchConflictError, match="appears 2 times"):
        apply(root, edit("a = 1\nb = 2  ", "c = 3", path="app/dup.py"))


def test_all_or_nothing_when_a_later_edit_fails(root: Path) -> None:
    with pytest.raises(PatchConflictError):
        apply(
            root,
            edit('config["db"]', "None"),
            edit("", "new\n", path="app/new.py"),
            edit("missing text", "x", path="app/other.py"),
        )
    assert (root / "app" / "cfg.py").read_text() == CFG
    assert not (root / "app" / "new.py").exists()


def test_changed_lines_limit_is_checked_before_writing(root: Path) -> None:
    with pytest.raises(InvalidPatchError, match="changes 2 lines; the limit is 1"):
        apply(root, edit('config["db"]', "None"), max_changed_lines=1)
    assert (root / "app" / "cfg.py").read_text() == CFG


def test_writing_through_a_symlinked_file_is_rejected(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "secret.py"
    outside.write_text("TOKEN = 1\n")
    (root / "app" / "link.py").symlink_to(outside)
    with pytest.raises(InvalidPatchError, match="symlinks"):
        apply(root, edit("TOKEN = 1", "TOKEN = 2", path="app/link.py"))
    assert outside.read_text() == "TOKEN = 1\n"


def test_writing_through_a_symlinked_folder_is_rejected(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidPatchError, match="symlinks"):
        apply(root, edit("", "x = 1\n", path="linked/new.py"))
    assert list(outside.iterdir()) == []


def test_binary_files_are_rejected(root: Path) -> None:
    (root / "app" / "blob.py").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(InvalidPatchError, match="UTF-8"):
        apply(root, edit("x", "y", path="app/blob.py"))


def test_directories_are_rejected(root: Path) -> None:
    with pytest.raises(InvalidPatchError, match="not a file"):
        apply(root, edit("x", "y", path="app"))


def test_file_without_final_newline(root: Path) -> None:
    (root / "app" / "tail.py").write_text("x = 1")
    applied = FilesystemPatcher().apply(
        Patch(edits=(edit("x = 1", "x = 2", path="app/tail.py"),)), root
    )
    assert (root / "app" / "tail.py").read_text() == "x = 2"
    assert (applied.lines_added, applied.lines_removed) == (1, 1)
