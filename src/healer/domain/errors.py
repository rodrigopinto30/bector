"""Project exception hierarchy. Catch ``HealerError`` at the CLI boundary only."""


class HealerError(Exception):
    """Base class for every error raised on purpose by this project."""


class WorkspaceError(HealerError):
    """The workspace path is missing, not a directory, or an input escapes it."""


class IndexingError(HealerError):
    """The symbol index could not be read or written."""


class LogSourceError(HealerError):
    """The log to diagnose could not be read."""


class CommandNotAllowedError(HealerError):
    """A command is not in the allowlist or has arguments that escape the sandbox."""


class ExecutionError(HealerError):
    """A command could not be started."""


class SandboxError(HealerError):
    """The isolated copy of the workspace could not be created."""
