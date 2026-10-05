"""Normalized error signatures: the same bug yields the same hash on any machine."""

import hashlib
import re

from healer.domain.diagnosis import StackFrame, TracedError

_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_PATH = re.compile(r"(?:[A-Za-z]:\\|/)[^\s:'\",)]+")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_PACKAGE_ROOTS = ("site-packages/", "dist-packages/")


def normalize_message(message: str) -> str:
    """Replace values that change between runs or machines with placeholders."""
    text = _ADDRESS.sub("<addr>", message)
    text = _QUOTED.sub("<str>", text)
    text = _PATH.sub("<path>", text)
    text = _NUMBER.sub("<num>", text)
    return " ".join(text.split())


def portable_path(path: str) -> str:
    """Drop the machine-specific part of a path outside the workspace."""
    posix = path.replace("\\", "/")
    for root in _PACKAGE_ROOTS:
        if root in posix:
            return posix.split(root, 1)[1]
    return posix.rsplit("/", 1)[-1]


def signature_basis(error: TracedError, origin_index: int | None) -> str:
    """Error type, normalized message, and the frames from the origin inward.

    Callers above the origin are left out, so the same bug reached from different
    entry points (two tests, a script and a test) keeps one signature.
    """
    frames = error.frames if origin_index is None else error.frames[origin_index:]
    parts = [error.error_type, normalize_message(error.message)]
    parts.extend(f"{_frame_path(frame)}:{frame.function}" for frame in frames)
    return "|".join(parts)


def compute_signature(basis: str) -> str:
    return hashlib.sha256(basis.encode()).hexdigest()


def _frame_path(frame: StackFrame) -> str:
    return frame.workspace_file or portable_path(frame.file)
