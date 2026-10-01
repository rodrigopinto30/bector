"""Project exception hierarchy. Catch ``HealerError`` at the CLI boundary only."""


class HealerError(Exception):
    """Base class for every error raised on purpose by this project."""


class WorkspaceError(HealerError):
    """The workspace path is missing, not a directory, or an input escapes it."""


class IndexingError(HealerError):
    """The symbol index could not be read or written."""
