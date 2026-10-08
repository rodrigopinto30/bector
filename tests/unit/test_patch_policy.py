import pytest

from healer.domain.errors import InvalidPatchError
from healer.domain.patch import FileEdit, Patch, PatchPolicy, parse_patch

POLICY = PatchPolicy(max_files=2, max_bytes=100)


def patch(*edits: FileEdit) -> Patch:
    return Patch(edits=edits)


def edit(path: str = "app/cfg.py", old: str = "a", new: str = "b") -> FileEdit:
    return FileEdit(path=path, old=old, new=new)


def test_a_small_patch_inside_the_project_is_valid() -> None:
    POLICY.validate(patch(edit(), edit("tests/test_cfg.py", old="", new="def test(): ...\n")))


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../outside.py",
        "app/../../outside.py",
        "C:/Windows/system.ini",
        "C:\\Windows\\system.ini",
        "~/.bashrc",
        "app/\0cfg.py",
    ],
)
def test_paths_outside_the_project_are_rejected(path: str) -> None:
    with pytest.raises(InvalidPatchError, match="outside the project"):
        POLICY.validate(patch(edit(path)))


@pytest.mark.parametrize("path", [".git/hooks/pre-commit", "app/.git/config", ".healer/x"])
def test_protected_folders_are_rejected(path: str) -> None:
    with pytest.raises(InvalidPatchError, match="protected folder"):
        POLICY.validate(patch(edit(path)))


@pytest.mark.parametrize("path", ["app/", "."])
def test_directories_are_rejected(path: str) -> None:
    with pytest.raises(InvalidPatchError):
        POLICY.validate(patch(edit(path)))


def test_too_many_files_is_rejected() -> None:
    with pytest.raises(InvalidPatchError, match="touches 3 files"):
        POLICY.validate(patch(edit("a.py"), edit("b.py"), edit("c.py")))


def test_several_edits_to_the_same_file_count_as_one_file() -> None:
    POLICY.validate(patch(edit("a.py"), edit("a.py", old="c"), edit("b.py")))


def test_too_many_bytes_is_rejected() -> None:
    with pytest.raises(InvalidPatchError, match="bytes"):
        POLICY.validate(patch(edit(new="x" * 200)))


def test_whitespace_only_search_text_is_rejected() -> None:
    with pytest.raises(InvalidPatchError, match="only whitespace"):
        POLICY.validate(patch(edit(old="   \n")))


def test_binary_content_is_rejected() -> None:
    with pytest.raises(InvalidPatchError, match="text"):
        POLICY.validate(patch(edit(new="\0")))


def test_parse_patch_from_json() -> None:
    parsed = parse_patch(
        '{"description": "fix", "edits": [{"path": "a.py", "old": "x", "new": "y"}]}'
    )
    assert parsed.description == "fix"
    assert parsed.edits == (FileEdit(path="a.py", old="x", new="y"),)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '{"edits": []}',
        '{"edits": [{"path": "", "new": "y"}]}',
        '{"edits": [{"path": "a.py"}]}',
    ],
)
def test_parse_patch_rejects_malformed_input(text: str) -> None:
    with pytest.raises(InvalidPatchError, match="Invalid patch"):
        parse_patch(text)
