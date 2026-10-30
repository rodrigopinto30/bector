"""Project exception hierarchy. Catch ``HealerError`` at the CLI boundary only."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from healer.domain.proposal import Proposal


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


class InvalidPatchError(HealerError):
    """A patch breaks the rules: unsafe path, too large, or not text."""


class RejectedProposalError(InvalidPatchError):
    """The model answered, but its patch cannot be used. Keeps the proposal to count its cost."""

    def __init__(self, message: str, proposal: "Proposal") -> None:
        super().__init__(message)
        self.proposal = proposal


class PatchConflictError(HealerError):
    """A patch does not fit the current code: the text to replace is missing or ambiguous."""


class LLMError(HealerError):
    """The language model could not produce a usable proposal."""


class MissingContextError(HealerError):
    """There is no indexed project code related to the error to show the model."""


class LedgerError(HealerError):
    """The token ledger could not be read or written."""


class RejectedMessageError(HealerError):
    """A message from the network was refused: forged, altered, malformed or unsafe."""


class SharingError(HealerError):
    """A solution could not be shared: sharing is misconfigured, the patch holds a secret,
    the message is too large, or the broker could not be reached."""
