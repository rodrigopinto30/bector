"""``SandboxProvider`` that copies the workspace into a private temporary directory."""

import contextlib
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path

from healer.adapters.workspace_scanner import DEFAULT_IGNORED_DIRS
from healer.domain.errors import SandboxError, WorkspaceError

logger = logging.getLogger(__name__)

DEFAULT_MAX_SANDBOX_BYTES = 500_000_000


class TempDirSandbox:
    def __init__(self, root: Path, files_copied: int) -> None:
        self._root = root
        self._files_copied = files_copied

    @property
    def root(self) -> Path:
        return self._root

    @property
    def files_copied(self) -> int:
        return self._files_copied

    def close(self) -> None:
        if not self._root.exists():
            return
        shutil.rmtree(self._root, onerror=_force_remove)
        if self._root.exists():
            logger.warning("Could not fully remove sandbox %s", self._root)

    def __enter__(self) -> "TempDirSandbox":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TempDirSandboxProvider:
    """Copies files and symlinks (as links, never followed); skips vendor and cache folders."""

    def __init__(
        self,
        workspace: Path,
        *,
        ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
        max_bytes: int = DEFAULT_MAX_SANDBOX_BYTES,
        base_dir: Path | None = None,
    ) -> None:
        resolved = workspace.resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {workspace}")
        self.workspace = resolved
        self._ignored_dirs = ignored_dirs
        self._max_bytes = max_bytes
        self._base_dir = base_dir

    def create(self) -> TempDirSandbox:
        """A new copy, readable only by its owner until something hands it over."""
        if self._base_dir is not None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="healer-sandbox-", dir=self._base_dir))
        try:
            files = self._copy(root)
        except BaseException:
            shutil.rmtree(root, onerror=_force_remove)
            raise
        return TempDirSandbox(root, files)

    def _copy(self, target: Path) -> int:
        files = 0
        total = 0
        for dirpath, dirnames, filenames in os.walk(self.workspace, followlinks=False):
            source_dir = Path(dirpath)
            target_dir = target / source_dir.relative_to(self.workspace)
            kept = []
            for name in sorted(dirnames):
                if name in self._ignored_dirs:
                    continue
                if (source_dir / name).is_symlink():
                    os.symlink(os.readlink(source_dir / name), target_dir / name)
                    continue
                (target_dir / name).mkdir()
                kept.append(name)
            dirnames[:] = kept
            for name in sorted(filenames):
                source = source_dir / name
                if source.is_symlink():
                    os.symlink(os.readlink(source), target_dir / name)
                elif source.is_file():
                    total += source.stat().st_size
                    if total > self._max_bytes:
                        raise SandboxError(
                            f"Workspace is larger than {self._max_bytes} bytes; "
                            "cannot create an isolated copy"
                        )
                    shutil.copy2(source, target_dir / name)
                    files += 1
        return files


def _force_remove(_function: object, path: str, _error: object) -> None:
    """Retry a removal after giving this user full access to the folder that holds it.

    Removing an entry needs write access to its folder, not to the entry, and only root
    gets around that. Links are never followed, so nothing outside the copy changes mode.
    """
    for folder in (os.path.dirname(path), path):
        if os.path.isdir(folder) and not os.path.islink(folder):
            with contextlib.suppress(OSError):
                os.chmod(folder, stat.S_IRWXU)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
