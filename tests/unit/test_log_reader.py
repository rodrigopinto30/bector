import io
import sys
from pathlib import Path

import pytest

from healer.adapters.log_reader import read_log
from healer.domain.errors import LogSourceError


def test_reads_a_file(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    log.write_text("KeyError: 'db'\n")
    assert read_log(log) == "KeyError: 'db'\n"


def test_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"from stdin\n")))
    assert read_log(None) == "from stdin\n"


def test_oversized_log_keeps_the_tail(tmp_path: Path) -> None:
    log = tmp_path / "big.log"
    log.write_text("noise\n" * 100 + "KeyError: 'db'\n")
    assert read_log(log, max_bytes=20).endswith("KeyError: 'db'\n")
    assert len(read_log(log, max_bytes=20)) == 20


def test_invalid_utf8_is_replaced_not_fatal(tmp_path: Path) -> None:
    log = tmp_path / "bad.log"
    log.write_bytes(b"caf\xe9 KeyError\n")
    assert "KeyError" in read_log(log)


def test_missing_file_raises_a_project_error(tmp_path: Path) -> None:
    with pytest.raises(LogSourceError):
        read_log(tmp_path / "missing.log")
