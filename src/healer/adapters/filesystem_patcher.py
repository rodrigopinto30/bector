"""``PatchApplier`` that edits files under a root directory, all or nothing."""

import difflib
from pathlib import Path

from healer.domain.errors import InvalidPatchError, PatchConflictError
from healer.domain.patch import AppliedPatch, FileEdit, Patch


class FilesystemPatcher:
    """Computes every edit in memory first and writes only when all of them fit.

    The text to replace must appear exactly once. If it is not found verbatim, a
    match that ignores trailing whitespace is accepted, as long as it is unique.
    """

    def __init__(self, *, max_changed_lines: int | None = None) -> None:
        self._max_changed_lines = max_changed_lines

    def apply(self, patch: Patch, root: Path) -> AppliedPatch:
        base = root.resolve()
        originals: dict[str, str | None] = {}
        contents: dict[str, str] = {}
        for edit in patch.edits:
            if edit.path not in contents:
                target = _target(base, edit.path)
                original = _read(target, edit.path)
                originals[edit.path] = original
                contents[edit.path] = original or ""
            contents[edit.path] = _apply_edit(contents[edit.path], edit, originals[edit.path])
        applied = _summary(originals, contents)
        if self._max_changed_lines is not None and applied.lines_changed > self._max_changed_lines:
            raise InvalidPatchError(
                f"Patch changes {applied.lines_changed} lines; "
                f"the limit is {self._max_changed_lines}"
            )
        for path, text in contents.items():
            target = _target(base, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return applied


def _target(base: Path, path: str) -> Path:
    current = base
    for part in Path(path.replace("\\", "/")).parts:
        current = current / part
        if current.is_symlink():
            raise InvalidPatchError(f"{path}: writing through symlinks is not allowed")
    if not current.resolve().is_relative_to(base):
        raise InvalidPatchError(f"{path}: resolves outside the project")
    return current


def _read(target: Path, path: str) -> str | None:
    if not target.exists():
        return None
    if not target.is_file():
        raise InvalidPatchError(f"{path}: is not a file")
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidPatchError(f"{path}: is not a UTF-8 text file") from exc


def _apply_edit(content: str, edit: FileEdit, original: str | None) -> str:
    if not edit.old:
        if original is not None or content:
            raise PatchConflictError(f"{edit.path}: cannot create a file that already exists")
        return edit.new
    if original is None and not content:
        raise PatchConflictError(f"{edit.path}: file does not exist")
    start, end = _locate(content, edit)
    return content[:start] + edit.new + content[end:]


def _locate(content: str, edit: FileEdit) -> tuple[int, int]:
    exact = content.count(edit.old)
    if exact == 1:
        start = content.index(edit.old)
        return start, start + len(edit.old)
    if exact > 1:
        raise PatchConflictError(
            f"{edit.path}: the text to replace appears {exact} times; it must be unique"
        )
    spans = _loose_matches(content, edit.old)
    if len(spans) == 1:
        return spans[0]
    first_line = edit.old.strip().splitlines()[0]
    if spans:
        raise PatchConflictError(
            f"{edit.path}: the text to replace appears {len(spans)} times; it must be unique"
        )
    raise PatchConflictError(f"{edit.path}: text to replace not found: {first_line!r}")


def _loose_matches(content: str, old: str) -> list[tuple[int, int]]:
    """Line-by-line matches that ignore trailing whitespace, as character spans."""
    wanted = [line.rstrip() for line in old.strip("\n").split("\n")]
    lines = content.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    spans = []
    for i in range(len(lines) - len(wanted) + 1):
        if all(lines[i + j].rstrip() == wanted[j] for j in range(len(wanted))):
            last = i + len(wanted) - 1
            spans.append((offsets[i], offsets[last] + len(lines[last])))
    return spans


def _summary(originals: dict[str, str | None], contents: dict[str, str]) -> AppliedPatch:
    chunks: list[str] = []
    added = removed = 0
    for path, new in contents.items():
        old = originals[path]
        diff = list(
            difflib.unified_diff(
                _lines(old or ""),
                _lines(new),
                fromfile="/dev/null" if old is None else f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        added += sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        removed += sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        chunks.extend(diff)
    return AppliedPatch(
        files=tuple(contents),
        created=tuple(path for path, old in originals.items() if old is None),
        diff="".join(chunks),
        lines_added=added,
        lines_removed=removed,
    )


def _lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines
