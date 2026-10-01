"""Filesystem implementation of ``SourceScanner`` confined to one workspace root."""

import hashlib
import logging
import os
from collections.abc import Iterator
from pathlib import Path

from healer.domain.errors import WorkspaceError
from healer.domain.models import SourceFile

logger = logging.getLogger(__name__)

DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        "build",
        "dist",
        ".healer",
    }
)
DEFAULT_MAX_FILE_BYTES = 1_000_000


class WorkspaceScanner:
    def __init__(
        self,
        root: Path,
        *,
        suffixes: frozenset[str] = frozenset({".py"}),
        ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        resolved = root.resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {root}")
        self.root = resolved
        self._suffixes = suffixes
        self._ignored_dirs = ignored_dirs
        self._max_file_bytes = max_file_bytes

    def scan(self) -> Iterator[SourceFile]:
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in self._ignored_dirs)
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                if path.suffix not in self._suffixes:
                    continue
                source = self._read(path)
                if source is not None:
                    yield source

    def _read(self, path: Path) -> SourceFile | None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            logger.warning("Skipping %s: resolves outside the workspace", path)
            return None
        if resolved.stat().st_size > self._max_file_bytes:
            logger.warning("Skipping %s: larger than %d bytes", path, self._max_file_bytes)
            return None
        data = resolved.read_bytes()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping %s: not valid UTF-8", path)
            return None
        return SourceFile(
            path=path.relative_to(self.root).as_posix(),
            content=content,
            content_hash=hashlib.sha256(data).hexdigest(),
        )
