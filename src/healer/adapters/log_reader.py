"""Reads raw output from a file or standard input: the log connector."""

import logging
import sys
from pathlib import Path
from typing import BinaryIO

from healer.domain.errors import LogSourceError

logger = logging.getLogger(__name__)

DEFAULT_MAX_LOG_BYTES = 5_000_000


def read_log(source: Path | None, *, max_bytes: int = DEFAULT_MAX_LOG_BYTES) -> str:
    """Return the text of ``source``, or of stdin when it is None.

    Oversized logs keep their tail, because the failure is usually printed last.
    """
    if source is None:
        return _read(sys.stdin.buffer, "stdin", max_bytes)
    try:
        with source.open("rb") as stream:
            return _read(stream, str(source), max_bytes)
    except OSError as exc:
        raise LogSourceError(f"Cannot read log {source}: {exc.strerror}") from exc


def _read(stream: BinaryIO, name: str, max_bytes: int) -> str:
    data = stream.read()
    if len(data) > max_bytes:
        logger.warning("%s is larger than %d bytes; keeping the last part", name, max_bytes)
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")
