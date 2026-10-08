"""Patches as search-and-replace edits, and the rules a patch must follow."""

import re
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from healer.domain.errors import InvalidPatchError

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_FORBIDDEN_DIRS = frozenset({".git", ".hg", ".svn", ".healer"})


class FileEdit(BaseModel):
    """Replace the only occurrence of ``old`` in ``path`` with ``new``.

    An empty ``old`` creates ``path``, which must not exist yet.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1)
    old: str = ""
    new: str


class Patch(BaseModel):
    model_config = ConfigDict(frozen=True)

    edits: tuple[FileEdit, ...] = Field(min_length=1)
    description: str = ""

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(edit.path for edit in self.edits))


class AppliedPatch(BaseModel):
    """What a patch changed, as a unified diff the developer can review."""

    model_config = ConfigDict(frozen=True)

    files: tuple[str, ...]
    created: tuple[str, ...] = ()
    diff: str
    lines_added: int = Field(ge=0)
    lines_removed: int = Field(ge=0)

    @property
    def lines_changed(self) -> int:
        return self.lines_added + self.lines_removed


class PatchPolicy:
    """Limits from the plan (7.5): patches stay small and inside the project."""

    def __init__(self, *, max_files: int, max_bytes: int) -> None:
        self.max_files = max_files
        self.max_bytes = max_bytes

    def validate(self, patch: Patch) -> None:
        """Raise ``InvalidPatchError`` before anything is read or written."""
        for edit in patch.edits:
            _check_path(edit.path)
            if "\0" in edit.old or "\0" in edit.new:
                raise InvalidPatchError(f"{edit.path}: edits must be text")
            if edit.old and not edit.old.strip():
                raise InvalidPatchError(f"{edit.path}: the text to replace is only whitespace")
        if len(patch.files) > self.max_files:
            raise InvalidPatchError(
                f"Patch touches {len(patch.files)} files; the limit is {self.max_files}"
            )
        size = sum(len(e.old.encode()) + len(e.new.encode()) for e in patch.edits)
        if size > self.max_bytes:
            raise InvalidPatchError(f"Patch is {size} bytes; the limit is {self.max_bytes}")


def _check_path(path: str) -> None:
    posix = path.replace("\\", "/")
    parts = PurePosixPath(posix).parts
    if (
        PurePosixPath(posix).is_absolute()
        or _WINDOWS_DRIVE.match(posix)
        or posix.startswith("~")
        or ".." in parts
        or "\0" in posix
    ):
        raise InvalidPatchError(f"Path {path!r} is outside the project")
    if any(part in _FORBIDDEN_DIRS for part in parts):
        raise InvalidPatchError(f"Path {path!r} is inside a protected folder")
    if not parts or posix.endswith("/"):
        raise InvalidPatchError(f"Path {path!r} is not a file")


def parse_patch(text: str) -> Patch:
    """Read a patch from its JSON form, or raise ``InvalidPatchError``."""
    try:
        return Patch.model_validate_json(text)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or 'patch'}: {error['msg']}"
            for error in exc.errors()
        )
        raise InvalidPatchError(f"Invalid patch: {problems}") from exc
