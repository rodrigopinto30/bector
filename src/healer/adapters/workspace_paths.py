"""Filesystem implementation of ``PathResolver`` confined to one workspace root."""

import re
from pathlib import Path, PurePosixPath

from healer.domain.errors import WorkspaceError

_EXTERNAL = re.compile(r"(?:^|/)(?:site-packages|dist-packages)/|/lib/python\d")


class WorkspacePathResolver:
    """Finds the workspace file behind a path printed on any machine.

    Traces carry absolute paths from wherever the code ran (a laptop, CI, a
    container), so the longest suffix of the path that exists in the workspace wins.
    """

    def __init__(self, root: Path) -> None:
        resolved = root.resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {root}")
        self.root = resolved
        self._cache: dict[str, str | None] = {}

    def resolve(self, raw_path: str) -> str | None:
        if raw_path not in self._cache:
            self._cache[raw_path] = self._lookup(raw_path)
        return self._cache[raw_path]

    def _lookup(self, raw_path: str) -> str | None:
        posix = raw_path.replace("\\", "/")
        if posix.startswith("<") or _EXTERNAL.search(posix):
            return None
        parts = [p for p in PurePosixPath(posix).parts if p not in {"/", ""}]
        if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
            parts = parts[1:]
        if ".." in parts:
            return None
        for start in range(len(parts)):
            candidate = self.root.joinpath(*parts[start:])
            resolved = candidate.resolve()
            if resolved.is_relative_to(self.root) and resolved.is_file():
                return candidate.relative_to(self.root).as_posix()
        return None
